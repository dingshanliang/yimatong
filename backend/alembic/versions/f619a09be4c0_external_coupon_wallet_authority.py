"""external coupon wallet authority

Revision ID: f619a09be4c0
Revises: 16d96e6016e9

补齐外部权威券（authority_type='external'）的钱包写入路径，消除「schema 就绪、
无 writer」的既有空白。

当前函数真实形态（0b91acfedc87 之后）：
- public.mutate_member_coupon_authority 是 wrapper：收紧鉴权（仅 yimatong_app）、
  按 membership 取 pg_advisory_xact_lock、自持 record_partial_refund、其余动作
  委托 mutate_member_coupon_authority_v1；
- _v1 是 992308d73df7 的原函数体（已 REVOKE yimatong_app，仅可经 wrapper 调用）。

因此本迁移只 REPLACE **_v1**（加入 external_* 四动作），并把 wrapper 的
membership 锁推导扩到 external_issue；wrapper 的鉴权与退款语义保持不变：

1. _v1 新动作：
   - external_issue：领取时创建 external 券（sync_status='pending'，事件 external_sync_pending）
   - external_sync_confirm：pending/error → synchronized（发放成功确认）
   - external_mark_error：pending → error（发放终态失败标记）
   - external_consume：available → used（外部平台核销回流，仅 external 券）
2. 放宽 ck_member_coupons_usage：external 券 used 状态不再强制本地订单/门店引用
   （核销权威在外部平台）。
3. 新增部分唯一索引 uq_member_coupons_external_ref (tenant_id, external_connector_id,
   external_coupon_ref) WHERE authority_type='external'，DB 层兜底外部引用唯一。

downgrade 注意：若已有外部券经 external_consume 核销（used 且无本地引用），
严格版 ck_member_coupons_usage 的 ADD CONSTRAINT 会失败——downgrade 会先做
前置检查并显式报错，而不是半途失败。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f619a09be4c0"
down_revision: str | Sequence[str] | None = "16d96e6016e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) _v1 函数体：以 992308d73df7 原体为基线追加 external_* 动作
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.mutate_member_coupon_authority_v1(
          requested_tenant_id uuid, requested_action text, payload jsonb
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE
          target_id uuid := (payload->>'coupon_id')::uuid;
          target_event_type text;
          prior_event record;
          coupon public.member_coupons%ROWTYPE;
          rule public.repurchase_coupon_rule_versions%ROWTYPE;
          old_status text;
          next_status text;
          now_at timestamptz := statement_timestamp();
          valid_from_at timestamptz;
          valid_until_at timestamptz;
          discount_minor integer;
          event_details jsonb := '{}'::jsonb;
        BEGIN
          IF (session_user<>'yimatong_app' AND session_user<>current_user)
             OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member coupon authority denied';
          END IF;
          target_event_type := CASE requested_action WHEN 'issue' THEN 'issued' WHEN 'reserve' THEN 'reserved'
            WHEN 'commit' THEN 'committed' WHEN 'release' THEN 'released' WHEN 'reverse' THEN 'reversed'
            WHEN 'store_redeem' THEN 'store_redeemed' WHEN 'expire' THEN 'expired' WHEN 'revoke' THEN 'revoked'
            WHEN 'external_issue' THEN 'external_sync_pending'
            WHEN 'external_sync_confirm' THEN 'external_sync_confirmed'
            WHEN 'external_mark_error' THEN 'external_sync_error'
            WHEN 'external_consume' THEN 'external_sync_confirmed'
            ELSE NULL END;
          IF target_event_type IS NULL OR NULLIF(payload->>'idempotency_key','') IS NULL
             OR length(COALESCE(payload->>'payload_digest',''))<>64 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid member coupon authority request';
          END IF;
          SELECT event_type,payload_digest,coupon_id INTO prior_event
          FROM public.repurchase_coupon_events
          WHERE tenant_id=requested_tenant_id AND idempotency_key=payload->>'idempotency_key';
          IF FOUND THEN
            IF prior_event.event_type<>target_event_type OR prior_event.payload_digest<>payload->>'payload_digest' THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='coupon_idempotency_conflict';
            END IF;
            RETURN prior_event.coupon_id;
          END IF;

          IF requested_action IN ('issue','external_issue') THEN
            SELECT * INTO rule FROM public.repurchase_coupon_rule_versions
              WHERE tenant_id=requested_tenant_id AND id=(payload->>'rule_version_id')::uuid FOR UPDATE;
            IF NOT FOUND OR rule.status<>'published' OR rule.issued_count>=rule.issuance_limit THEN
              RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_rule_not_issuable';
            END IF;
            IF NOT EXISTS(SELECT 1 FROM public.brand_memberships WHERE tenant_id=requested_tenant_id
              AND id=(payload->>'membership_id')::uuid AND status='active') THEN
              RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='active_membership_required';
            END IF;
            IF requested_action='external_issue' THEN
              IF NOT EXISTS(SELECT 1 FROM public.connectors WHERE tenant_id=requested_tenant_id
                AND id=(payload->>'external_connector_id')::uuid) THEN
                RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='external_connector_not_found';
              END IF;
              IF length(trim(payload->>'external_coupon_ref'))=0
                 OR length(trim(payload->>'external_coupon_ref'))>200 THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid external coupon reference';
              END IF;
            END IF;
            SELECT * INTO coupon FROM public.member_coupons WHERE tenant_id=requested_tenant_id
              AND membership_id=(payload->>'membership_id')::uuid AND rule_version_id=rule.id
              AND status IN ('available','reserved') FOR UPDATE;
            IF FOUND THEN
              IF (requested_action='external_issue' AND coupon.authority_type<>'external')
                 OR (requested_action='issue' AND coupon.authority_type<>'yimatong') THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_authority_conflict';
              END IF;
              target_id := coupon.id;
              old_status := coupon.status;
              next_status := coupon.status;
              event_details := jsonb_build_object('outcome','existing_active_coupon');
            ELSE
              valid_from_at := CASE WHEN rule.validity_mode='relative' THEN now_at ELSE rule.fixed_valid_from END;
              valid_until_at := CASE WHEN rule.validity_mode='relative' THEN now_at+make_interval(days=>rule.valid_days)
                ELSE rule.fixed_valid_until END;
              IF valid_until_at IS NULL OR valid_until_at<=now_at THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_rule_validity_exhausted';
              END IF;
              INSERT INTO public.member_coupons(
                id,tenant_id,membership_id,rule_version_id,source_claim_id,source_scan_event_id,source_scan_time,
                source_public_id,coupon_number,status,valid_from,valid_until,authority_type,sync_status,version,
                external_connector_id,external_coupon_ref
              ) VALUES (
                target_id,requested_tenant_id,(payload->>'membership_id')::uuid,rule.id,
                NULLIF(payload->>'source_claim_id','')::uuid,NULLIF(payload->>'source_scan_event_id','')::uuid,
                NULLIF(payload->>'source_scan_time','')::timestamptz,NULLIF(payload->>'source_public_id',''),
                payload->>'coupon_number','available',valid_from_at,valid_until_at,
                CASE WHEN requested_action='external_issue' THEN 'external' ELSE 'yimatong' END,
                CASE WHEN requested_action='external_issue' THEN 'pending' ELSE 'not_required' END,1,
                CASE WHEN requested_action='external_issue' THEN (payload->>'external_connector_id')::uuid END,
                CASE WHEN requested_action='external_issue' THEN trim(payload->>'external_coupon_ref') END
              ) RETURNING * INTO coupon;
              UPDATE public.repurchase_coupon_rule_versions SET issued_count=issued_count+1,updated_at=now_at
                WHERE tenant_id=requested_tenant_id AND id=rule.id;
              old_status := NULL;
              next_status := 'available';
            END IF;
          ELSE
            SELECT * INTO coupon FROM public.member_coupons
              WHERE tenant_id=requested_tenant_id AND id=target_id FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='member_coupon_not_found'; END IF;
            SELECT * INTO rule FROM public.repurchase_coupon_rule_versions
              WHERE tenant_id=requested_tenant_id AND id=coupon.rule_version_id;
            old_status := coupon.status;
            IF requested_action='external_sync_confirm' THEN
              IF coupon.authority_type<>'external' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_external_authority_required';
              END IF;
              IF coupon.sync_status='synchronized' THEN
                next_status := coupon.status;
                event_details := jsonb_build_object('outcome','already_synchronized');
              ELSIF coupon.sync_status NOT IN ('pending','error') THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_external_sync_invalid';
              ELSE
                next_status := coupon.status;
                UPDATE public.member_coupons SET sync_status='synchronized',sync_error=NULL,
                  version=version+1,updated_at=now_at
                  WHERE tenant_id=requested_tenant_id AND id=target_id;
                event_details := jsonb_build_object('external_id',NULLIF(payload->>'external_id',''));
              END IF;
            ELSIF requested_action='external_mark_error' THEN
              IF coupon.authority_type<>'external' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_external_authority_required';
              END IF;
              IF coupon.sync_status='error' THEN
                next_status := coupon.status;
                event_details := jsonb_build_object('outcome','already_error');
              ELSIF coupon.sync_status<>'pending' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_external_sync_invalid';
              ELSE
                IF NULLIF(trim(payload->>'reason'),'') IS NULL THEN
                  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='coupon_sync_error_reason_required';
                END IF;
                next_status := coupon.status;
                UPDATE public.member_coupons SET sync_status='error',
                  sync_error=left(trim(payload->>'reason'),500),version=version+1,updated_at=now_at
                  WHERE tenant_id=requested_tenant_id AND id=target_id;
              END IF;
            ELSIF requested_action='external_consume' THEN
              IF coupon.authority_type<>'external' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_external_authority_required';
              END IF;
              IF coupon.sync_status<>'synchronized' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_external_pending';
              END IF;
              IF coupon.status='used' THEN
                next_status := coupon.status;
                event_details := jsonb_build_object('outcome','already_used');
              ELSIF coupon.status<>'available' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_not_available';
              ELSE
                next_status := 'used';
                UPDATE public.member_coupons SET status=next_status,used_at=now_at,
                  version=version+1,updated_at=now_at
                  WHERE tenant_id=requested_tenant_id AND id=target_id;
                event_details := jsonb_build_object('external_event',NULLIF(payload->>'external_event',''));
              END IF;
            ELSIF requested_action='reserve' THEN
              IF coupon.membership_id<>(payload->>'membership_id')::uuid THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='coupon_membership_mismatch';
              END IF;
              IF rule.channel_scope NOT IN ('online','both') THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_channel_not_allowed';
              END IF;
              IF coupon.authority_type='external' AND coupon.sync_status<>'synchronized' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_external_pending';
              END IF;
              IF coupon.status='reserved' AND coupon.reserved_order_ref=payload->>'order_ref' THEN
                next_status := coupon.status;
                event_details := jsonb_build_object('outcome','existing_order_reservation');
              ELSE
                IF coupon.status<>'available' OR coupon.valid_from>now_at OR coupon.valid_until<=now_at THEN
                  RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_not_available';
                END IF;
                IF (payload->>'goods_subtotal_minor')::integer<rule.minimum_spend_minor
                   OR (payload->>'eligible_subtotal_minor')::integer<=0 THEN
                  RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_order_not_eligible';
                END IF;
                discount_minor := LEAST(rule.amount_minor,(payload->>'eligible_subtotal_minor')::integer,
                  (payload->>'goods_subtotal_minor')::integer-1);
                IF discount_minor<=0 THEN
                  RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_order_not_eligible';
                END IF;
                next_status := 'reserved';
                UPDATE public.member_coupons SET status=next_status,reserved_order_ref=payload->>'order_ref',
                  reservation_expires_at=now_at+interval '15 minutes',reserved_discount_minor=discount_minor,
                  version=version+1,updated_at=now_at WHERE tenant_id=requested_tenant_id AND id=target_id;
              END IF;
            ELSIF requested_action='commit' THEN
              IF coupon.status<>'reserved' OR coupon.reserved_order_ref<>payload->>'order_ref' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_reservation_mismatch';
              END IF;
              IF coupon.reservation_expires_at<=now_at THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_reservation_expired';
              END IF;
              next_status := 'used';
              UPDATE public.member_coupons SET status=next_status,used_order_ref=payload->>'order_ref',used_at=now_at,
                reserved_order_ref=NULL,reservation_expires_at=NULL,reserved_discount_minor=NULL,
                version=version+1,updated_at=now_at WHERE tenant_id=requested_tenant_id AND id=target_id;
            ELSIF requested_action='release' THEN
              IF coupon.status<>'reserved' OR coupon.reserved_order_ref<>payload->>'order_ref' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_reservation_mismatch';
              END IF;
              next_status := CASE WHEN coupon.valid_until>now_at THEN 'available' ELSE 'expired' END;
              UPDATE public.member_coupons SET status=next_status,reserved_order_ref=NULL,reservation_expires_at=NULL,
                reserved_discount_minor=NULL,version=version+1,updated_at=now_at
                WHERE tenant_id=requested_tenant_id AND id=target_id;
            ELSIF requested_action='reverse' THEN
              IF coupon.status<>'used' OR coupon.used_order_ref<>payload->>'order_ref' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_use_mismatch';
              END IF;
              next_status := CASE WHEN coupon.valid_until>now_at THEN 'available' ELSE 'expired' END;
              UPDATE public.member_coupons SET status=next_status,used_order_ref=NULL,used_store_id=NULL,used_at=NULL,
                version=version+1,updated_at=now_at WHERE tenant_id=requested_tenant_id AND id=target_id;
            ELSIF requested_action='store_redeem' THEN
              IF coupon.membership_id<>(payload->>'membership_id')::uuid THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='coupon_membership_mismatch';
              END IF;
              IF rule.channel_scope NOT IN ('store','both') THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_channel_not_allowed';
              END IF;
              IF coupon.authority_type='external' AND coupon.sync_status<>'synchronized' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_external_pending';
              END IF;
              IF coupon.status<>'available' OR coupon.valid_from>now_at OR coupon.valid_until<=now_at THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_not_available';
              END IF;
              IF NOT EXISTS(SELECT 1 FROM public.stores WHERE tenant_id=requested_tenant_id
                AND id=(payload->>'store_id')::uuid AND status='active') THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_store_not_active';
              END IF;
              next_status := 'used';
              UPDATE public.member_coupons SET status=next_status,used_store_id=(payload->>'store_id')::uuid,
                used_at=now_at,version=version+1,updated_at=now_at
                WHERE tenant_id=requested_tenant_id AND id=target_id;
            ELSIF requested_action='expire' THEN
              IF coupon.status NOT IN ('available','reserved') OR coupon.valid_until>now_at THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_not_expirable';
              END IF;
              next_status := 'expired';
              UPDATE public.member_coupons SET status=next_status,reserved_order_ref=NULL,reservation_expires_at=NULL,
                reserved_discount_minor=NULL,version=version+1,updated_at=now_at
                WHERE tenant_id=requested_tenant_id AND id=target_id;
            ELSIF requested_action='revoke' THEN
              IF coupon.status<>'available' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_not_revocable';
              END IF;
              IF NULLIF(trim(payload->>'reason'),'') IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='coupon_revoke_reason_required';
              END IF;
              next_status := 'revoked';
              UPDATE public.member_coupons SET status=next_status,revoked_at=now_at,
                revoke_reason=trim(payload->>'reason'),
                version=version+1,updated_at=now_at WHERE tenant_id=requested_tenant_id AND id=target_id;
            END IF;
          END IF;
          INSERT INTO public.repurchase_coupon_events(
            id,tenant_id,rule_version_id,coupon_id,event_type,from_status,to_status,idempotency_key,payload_digest,
            order_ref,store_id,actor_type,actor_id,reason,details
          ) VALUES (
            (payload->>'event_id')::uuid,requested_tenant_id,rule.id,target_id,target_event_type,old_status,next_status,
            payload->>'idempotency_key',payload->>'payload_digest',NULLIF(payload->>'order_ref',''),
            NULLIF(payload->>'store_id','')::uuid,COALESCE(NULLIF(payload->>'actor_type',''),'service'),
            NULLIF(payload->>'actor_id','')::uuid,NULLIF(payload->>'reason',''),
            event_details || CASE WHEN discount_minor IS NULL THEN '{}'::jsonb
              ELSE jsonb_build_object('discount_minor',discount_minor) END
          );
          RETURN target_id;
        END $f$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.mutate_member_coupon_authority_v1(uuid,text,jsonb) FROM PUBLIC,yimatong_app"
    )

    # 2) wrapper：保持 0b91acfedc87 语义，仅把 membership 锁推导扩到 external_issue
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.mutate_member_coupon_authority(
          requested_tenant_id uuid,requested_action text,payload jsonb) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE target_id uuid:=(payload->>'coupon_id')::uuid;membership_value uuid;prior record;
          coupon public.member_coupons%ROWTYPE;
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member coupon authority denied'; END IF;
          IF requested_action IN ('issue','external_issue') THEN membership_value:=(payload->>'membership_id')::uuid;
          ELSE SELECT membership_id INTO membership_value FROM public.member_coupons
            WHERE tenant_id=requested_tenant_id AND id=target_id; END IF;
          IF membership_value IS NOT NULL THEN PERFORM pg_advisory_xact_lock(hashtextextended(
            'member-assets:'||requested_tenant_id::text||':'||membership_value::text,0)); END IF;
          IF requested_action<>'record_partial_refund' THEN
            RETURN public.mutate_member_coupon_authority_v1(requested_tenant_id,requested_action,payload); END IF;
          IF NULLIF(payload->>'idempotency_key','') IS NULL OR length(COALESCE(payload->>'payload_digest',''))<>64 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid member coupon authority request'; END IF;
          SELECT event_type,payload_digest,coupon_id INTO prior FROM public.repurchase_coupon_events
            WHERE tenant_id=requested_tenant_id AND idempotency_key=payload->>'idempotency_key';
          IF FOUND THEN
            IF prior.event_type<>'refund_recorded' OR prior.payload_digest<>payload->>'payload_digest' THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='coupon_idempotency_conflict'; END IF;
            RETURN prior.coupon_id;
          END IF;
          SELECT * INTO coupon FROM public.member_coupons WHERE tenant_id=requested_tenant_id AND id=target_id FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='member_coupon_not_found'; END IF;
          IF coupon.status<>'used' OR coupon.used_order_ref<>payload->>'order_ref' THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_use_mismatch'; END IF;
          INSERT INTO public.repurchase_coupon_events(id,tenant_id,rule_version_id,coupon_id,event_type,
            from_status,to_status,idempotency_key,payload_digest,order_ref,actor_type,actor_id,details)
          VALUES((payload->>'event_id')::uuid,requested_tenant_id,coupon.rule_version_id,target_id,
            'refund_recorded','used','used',payload->>'idempotency_key',payload->>'payload_digest',
            payload->>'order_ref','service',NULLIF(payload->>'actor_id','')::uuid,
            jsonb_build_object('outcome','partial_refund_no_coupon_restore'));
          RETURN target_id;
        END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.mutate_member_coupon_authority(uuid,text,jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.mutate_member_coupon_authority(uuid,text,jsonb) TO yimatong_app")

    # 3) 约束放宽与外部引用唯一索引
    op.execute("ALTER TABLE public.member_coupons DROP CONSTRAINT IF EXISTS ck_member_coupons_usage")
    op.execute(
        "ALTER TABLE public.member_coupons ADD CONSTRAINT ck_member_coupons_usage "
        "CHECK ((status='used' AND used_at IS NOT NULL AND (used_order_ref IS NOT NULL OR used_store_id IS NOT NULL "
        "OR authority_type='external')) OR "
        "(status<>'used' AND used_at IS NULL AND used_order_ref IS NULL AND used_store_id IS NULL))"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_member_coupons_external_ref "
        "ON public.member_coupons(tenant_id, external_connector_id, external_coupon_ref) "
        "WHERE authority_type='external'"
    )


def downgrade() -> None:
    # 前置守卫：外部券核销行与严格约束不兼容时显式失败
    op.execute(
        r"""
        DO $guard$
        DECLARE consumed_external integer;
        BEGIN
          SELECT count(*) INTO consumed_external FROM public.member_coupons
            WHERE authority_type='external' AND status='used'
              AND used_order_ref IS NULL AND used_store_id IS NULL;
          IF consumed_external>0 THEN
            RAISE EXCEPTION USING ERRCODE='23514',
              MESSAGE='cannot downgrade: '||consumed_external||
                ' externally-consumed coupon(s) violate the strict usage constraint';
          END IF;
        END $guard$;
        """
    )
    op.execute("DROP INDEX IF EXISTS public.uq_member_coupons_external_ref")
    op.execute("ALTER TABLE public.member_coupons DROP CONSTRAINT IF EXISTS ck_member_coupons_usage")
    op.execute(
        "ALTER TABLE public.member_coupons ADD CONSTRAINT ck_member_coupons_usage "
        "CHECK ((status='used' AND used_at IS NOT NULL "
        "AND (used_order_ref IS NOT NULL OR used_store_id IS NOT NULL)) OR "
        "(status<>'used' AND used_at IS NULL AND used_order_ref IS NULL AND used_store_id IS NULL))"
    )
    # 还原 0b91acfedc87 的 wrapper：去掉 external_issue 锁推导扩展
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.mutate_member_coupon_authority(
          requested_tenant_id uuid,requested_action text,payload jsonb) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE target_id uuid:=(payload->>'coupon_id')::uuid;membership_value uuid;prior record;
          coupon public.member_coupons%ROWTYPE;
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member coupon authority denied'; END IF;
          IF requested_action='issue' THEN membership_value:=(payload->>'membership_id')::uuid;
          ELSE SELECT membership_id INTO membership_value FROM public.member_coupons
            WHERE tenant_id=requested_tenant_id AND id=target_id; END IF;
          IF membership_value IS NOT NULL THEN PERFORM pg_advisory_xact_lock(hashtextextended(
            'member-assets:'||requested_tenant_id::text||':'||membership_value::text,0)); END IF;
          IF requested_action<>'record_partial_refund' THEN
            RETURN public.mutate_member_coupon_authority_v1(requested_tenant_id,requested_action,payload); END IF;
          IF NULLIF(payload->>'idempotency_key','') IS NULL OR length(COALESCE(payload->>'payload_digest',''))<>64 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid member coupon authority request'; END IF;
          SELECT event_type,payload_digest,coupon_id INTO prior FROM public.repurchase_coupon_events
            WHERE tenant_id=requested_tenant_id AND idempotency_key=payload->>'idempotency_key';
          IF FOUND THEN
            IF prior.event_type<>'refund_recorded' OR prior.payload_digest<>payload->>'payload_digest' THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='coupon_idempotency_conflict'; END IF;
            RETURN prior.coupon_id;
          END IF;
          SELECT * INTO coupon FROM public.member_coupons WHERE tenant_id=requested_tenant_id AND id=target_id FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='member_coupon_not_found'; END IF;
          IF coupon.status<>'used' OR coupon.used_order_ref<>payload->>'order_ref' THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_use_mismatch'; END IF;
          INSERT INTO public.repurchase_coupon_events(id,tenant_id,rule_version_id,coupon_id,event_type,
            from_status,to_status,idempotency_key,payload_digest,order_ref,actor_type,actor_id,details)
          VALUES((payload->>'event_id')::uuid,requested_tenant_id,coupon.rule_version_id,target_id,
            'refund_recorded','used','used',payload->>'idempotency_key',payload->>'payload_digest',
            payload->>'order_ref','service',NULLIF(payload->>'actor_id','')::uuid,
            jsonb_build_object('outcome','partial_refund_no_coupon_restore'));
          RETURN target_id;
        END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.mutate_member_coupon_authority(uuid,text,jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.mutate_member_coupon_authority(uuid,text,jsonb) TO yimatong_app")
    # 还原 _v1 为 992308d73df7 原体（不含 external_* 动作）
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.mutate_member_coupon_authority_v1(
          requested_tenant_id uuid, requested_action text, payload jsonb
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE
          target_id uuid := (payload->>'coupon_id')::uuid;
          target_event_type text;
          prior_event record;
          coupon public.member_coupons%ROWTYPE;
          rule public.repurchase_coupon_rule_versions%ROWTYPE;
          old_status text;
          next_status text;
          now_at timestamptz := statement_timestamp();
          valid_from_at timestamptz;
          valid_until_at timestamptz;
          discount_minor integer;
          event_details jsonb := '{}'::jsonb;
        BEGIN
          IF (session_user<>'yimatong_app' AND session_user<>current_user)
             OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member coupon authority denied';
          END IF;
          target_event_type := CASE requested_action WHEN 'issue' THEN 'issued' WHEN 'reserve' THEN 'reserved'
            WHEN 'commit' THEN 'committed' WHEN 'release' THEN 'released' WHEN 'reverse' THEN 'reversed'
            WHEN 'store_redeem' THEN 'store_redeemed' WHEN 'expire' THEN 'expired' WHEN 'revoke' THEN 'revoked'
            ELSE NULL END;
          IF target_event_type IS NULL OR NULLIF(payload->>'idempotency_key','') IS NULL
             OR length(COALESCE(payload->>'payload_digest',''))<>64 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid member coupon authority request';
          END IF;
          SELECT event_type,payload_digest,coupon_id INTO prior_event
          FROM public.repurchase_coupon_events
          WHERE tenant_id=requested_tenant_id AND idempotency_key=payload->>'idempotency_key';
          IF FOUND THEN
            IF prior_event.event_type<>target_event_type OR prior_event.payload_digest<>payload->>'payload_digest' THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='coupon_idempotency_conflict';
            END IF;
            RETURN prior_event.coupon_id;
          END IF;

          IF requested_action='issue' THEN
            SELECT * INTO rule FROM public.repurchase_coupon_rule_versions
              WHERE tenant_id=requested_tenant_id AND id=(payload->>'rule_version_id')::uuid FOR UPDATE;
            IF NOT FOUND OR rule.status<>'published' OR rule.issued_count>=rule.issuance_limit THEN
              RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_rule_not_issuable';
            END IF;
            IF NOT EXISTS(SELECT 1 FROM public.brand_memberships WHERE tenant_id=requested_tenant_id
              AND id=(payload->>'membership_id')::uuid AND status='active') THEN
              RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='active_membership_required';
            END IF;
            SELECT * INTO coupon FROM public.member_coupons WHERE tenant_id=requested_tenant_id
              AND membership_id=(payload->>'membership_id')::uuid AND rule_version_id=rule.id
              AND status IN ('available','reserved') FOR UPDATE;
            IF FOUND THEN
              target_id := coupon.id;
              old_status := coupon.status;
              next_status := coupon.status;
              event_details := jsonb_build_object('outcome','existing_active_coupon');
            ELSE
              valid_from_at := CASE WHEN rule.validity_mode='relative' THEN now_at ELSE rule.fixed_valid_from END;
              valid_until_at := CASE WHEN rule.validity_mode='relative' THEN now_at+make_interval(days=>rule.valid_days)
                ELSE rule.fixed_valid_until END;
              IF valid_until_at IS NULL OR valid_until_at<=now_at THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_rule_validity_exhausted';
              END IF;
              INSERT INTO public.member_coupons(
                id,tenant_id,membership_id,rule_version_id,source_claim_id,source_scan_event_id,source_scan_time,
                source_public_id,coupon_number,status,valid_from,valid_until,authority_type,sync_status,version
              ) VALUES (
                target_id,requested_tenant_id,(payload->>'membership_id')::uuid,rule.id,
                NULLIF(payload->>'source_claim_id','')::uuid,NULLIF(payload->>'source_scan_event_id','')::uuid,
                NULLIF(payload->>'source_scan_time','')::timestamptz,NULLIF(payload->>'source_public_id',''),
                payload->>'coupon_number','available',valid_from_at,valid_until_at,'yimatong','not_required',1
              ) RETURNING * INTO coupon;
              UPDATE public.repurchase_coupon_rule_versions SET issued_count=issued_count+1,updated_at=now_at
                WHERE tenant_id=requested_tenant_id AND id=rule.id;
              old_status := NULL;
              next_status := 'available';
            END IF;
          ELSE
            SELECT * INTO coupon FROM public.member_coupons
              WHERE tenant_id=requested_tenant_id AND id=target_id FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='member_coupon_not_found'; END IF;
            SELECT * INTO rule FROM public.repurchase_coupon_rule_versions
              WHERE tenant_id=requested_tenant_id AND id=coupon.rule_version_id;
            old_status := coupon.status;
            IF requested_action='reserve' THEN
              IF coupon.membership_id<>(payload->>'membership_id')::uuid THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='coupon_membership_mismatch';
              END IF;
              IF rule.channel_scope NOT IN ('online','both') THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_channel_not_allowed';
              END IF;
              IF coupon.authority_type='external' AND coupon.sync_status<>'synchronized' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_external_pending';
              END IF;
              IF coupon.status='reserved' AND coupon.reserved_order_ref=payload->>'order_ref' THEN
                next_status := coupon.status;
                event_details := jsonb_build_object('outcome','existing_order_reservation');
              ELSE
                IF coupon.status<>'available' OR coupon.valid_from>now_at OR coupon.valid_until<=now_at THEN
                  RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_not_available';
                END IF;
                IF (payload->>'goods_subtotal_minor')::integer<rule.minimum_spend_minor
                   OR (payload->>'eligible_subtotal_minor')::integer<=0 THEN
                  RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_order_not_eligible';
                END IF;
                discount_minor := LEAST(rule.amount_minor,(payload->>'eligible_subtotal_minor')::integer,
                  (payload->>'goods_subtotal_minor')::integer-1);
                IF discount_minor<=0 THEN
                  RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_order_not_eligible';
                END IF;
                next_status := 'reserved';
                UPDATE public.member_coupons SET status=next_status,reserved_order_ref=payload->>'order_ref',
                  reservation_expires_at=now_at+interval '15 minutes',reserved_discount_minor=discount_minor,
                  version=version+1,updated_at=now_at WHERE tenant_id=requested_tenant_id AND id=target_id;
              END IF;
            ELSIF requested_action='commit' THEN
              IF coupon.status<>'reserved' OR coupon.reserved_order_ref<>payload->>'order_ref' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_reservation_mismatch';
              END IF;
              IF coupon.reservation_expires_at<=now_at THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_reservation_expired';
              END IF;
              next_status := 'used';
              UPDATE public.member_coupons SET status=next_status,used_order_ref=payload->>'order_ref',used_at=now_at,
                reserved_order_ref=NULL,reservation_expires_at=NULL,reserved_discount_minor=NULL,
                version=version+1,updated_at=now_at WHERE tenant_id=requested_tenant_id AND id=target_id;
            ELSIF requested_action='release' THEN
              IF coupon.status<>'reserved' OR coupon.reserved_order_ref<>payload->>'order_ref' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_reservation_mismatch';
              END IF;
              next_status := CASE WHEN coupon.valid_until>now_at THEN 'available' ELSE 'expired' END;
              UPDATE public.member_coupons SET status=next_status,reserved_order_ref=NULL,reservation_expires_at=NULL,
                reserved_discount_minor=NULL,version=version+1,updated_at=now_at
                WHERE tenant_id=requested_tenant_id AND id=target_id;
            ELSIF requested_action='reverse' THEN
              IF coupon.status<>'used' OR coupon.used_order_ref<>payload->>'order_ref' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_use_mismatch';
              END IF;
              next_status := CASE WHEN coupon.valid_until>now_at THEN 'available' ELSE 'expired' END;
              UPDATE public.member_coupons SET status=next_status,used_order_ref=NULL,used_store_id=NULL,used_at=NULL,
                version=version+1,updated_at=now_at WHERE tenant_id=requested_tenant_id AND id=target_id;
            ELSIF requested_action='store_redeem' THEN
              IF coupon.membership_id<>(payload->>'membership_id')::uuid THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='coupon_membership_mismatch';
              END IF;
              IF rule.channel_scope NOT IN ('store','both') THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_channel_not_allowed';
              END IF;
              IF coupon.authority_type='external' AND coupon.sync_status<>'synchronized' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_external_pending';
              END IF;
              IF coupon.status<>'available' OR coupon.valid_from>now_at OR coupon.valid_until<=now_at THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_not_available';
              END IF;
              IF NOT EXISTS(SELECT 1 FROM public.stores WHERE tenant_id=requested_tenant_id
                AND id=(payload->>'store_id')::uuid AND status='active') THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_store_not_active';
              END IF;
              next_status := 'used';
              UPDATE public.member_coupons SET status=next_status,used_store_id=(payload->>'store_id')::uuid,
                used_at=now_at,version=version+1,updated_at=now_at
                WHERE tenant_id=requested_tenant_id AND id=target_id;
            ELSIF requested_action='expire' THEN
              IF coupon.status NOT IN ('available','reserved') OR coupon.valid_until>now_at THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_not_expirable';
              END IF;
              next_status := 'expired';
              UPDATE public.member_coupons SET status=next_status,reserved_order_ref=NULL,reservation_expires_at=NULL,
                reserved_discount_minor=NULL,version=version+1,updated_at=now_at
                WHERE tenant_id=requested_tenant_id AND id=target_id;
            ELSIF requested_action='revoke' THEN
              IF coupon.status<>'available' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_not_revocable';
              END IF;
              IF NULLIF(trim(payload->>'reason'),'') IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='coupon_revoke_reason_required';
              END IF;
              next_status := 'revoked';
              UPDATE public.member_coupons SET status=next_status,revoked_at=now_at,
                revoke_reason=trim(payload->>'reason'),
                version=version+1,updated_at=now_at WHERE tenant_id=requested_tenant_id AND id=target_id;
            END IF;
          END IF;
          INSERT INTO public.repurchase_coupon_events(
            id,tenant_id,rule_version_id,coupon_id,event_type,from_status,to_status,idempotency_key,payload_digest,
            order_ref,store_id,actor_type,actor_id,reason,details
          ) VALUES (
            (payload->>'event_id')::uuid,requested_tenant_id,rule.id,target_id,target_event_type,old_status,next_status,
            payload->>'idempotency_key',payload->>'payload_digest',NULLIF(payload->>'order_ref',''),
            NULLIF(payload->>'store_id','')::uuid,COALESCE(NULLIF(payload->>'actor_type',''),'service'),
            NULLIF(payload->>'actor_id','')::uuid,NULLIF(payload->>'reason',''),
            event_details || CASE WHEN discount_minor IS NULL THEN '{}'::jsonb
              ELSE jsonb_build_object('discount_minor',discount_minor) END
          );
          RETURN target_id;
        END $f$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.mutate_member_coupon_authority_v1(uuid,text,jsonb) FROM PUBLIC,yimatong_app"
    )
