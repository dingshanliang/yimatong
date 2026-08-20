"""add authoritative repurchase coupon lifecycle

Revision ID: 992308d73df7
Revises: 36e723af82b6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "992308d73df7"
down_revision: str | Sequence[str] | None = "36e723af82b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "repurchase_coupon_rule_versions",
    "member_coupons",
    "repurchase_coupon_events",
)


def _tenant_policy(table: str, *, append_only: bool = False) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    if append_only:
        op.execute(
            f"CREATE POLICY tenant_select ON public.{table} FOR SELECT USING (tenant_id=public.current_tenant_id())"
        )
        op.execute(
            f"CREATE POLICY tenant_insert ON public.{table} FOR INSERT "
            "WITH CHECK (tenant_id=public.current_tenant_id())"
        )
    else:
        op.execute(
            f"CREATE POLICY tenant_isolation ON public.{table} "
            "USING (tenant_id=public.current_tenant_id()) "
            "WITH CHECK (tenant_id=public.current_tenant_id())"
        )
    op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")


def _create_authority_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.mutate_repurchase_coupon_rule_authority(
          requested_tenant_id uuid, requested_action text, payload jsonb
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE
          target_id uuid := (payload->>'rule_version_id')::uuid;
          target_event_type text;
          target_status text;
          prior_event record;
          current_rule public.repurchase_coupon_rule_versions%ROWTYPE;
          old_status text;
        BEGIN
          IF (session_user<>'yimatong_app' AND session_user<>current_user)
             OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='coupon rule authority denied';
          END IF;
          target_event_type := CASE requested_action
            WHEN 'create' THEN 'rule_created' WHEN 'publish' THEN 'rule_published'
            WHEN 'pause' THEN 'rule_paused' WHEN 'resume' THEN 'rule_resumed'
            WHEN 'end' THEN 'rule_ended' ELSE NULL END;
          IF target_event_type IS NULL OR NULLIF(payload->>'idempotency_key','') IS NULL
             OR length(COALESCE(payload->>'payload_digest',''))<>64 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid coupon rule authority request';
          END IF;
          SELECT event_type,payload_digest,rule_version_id INTO prior_event
          FROM public.repurchase_coupon_events
          WHERE tenant_id=requested_tenant_id AND idempotency_key=payload->>'idempotency_key';
          IF FOUND THEN
            IF prior_event.event_type<>target_event_type OR prior_event.payload_digest<>payload->>'payload_digest' THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='coupon_idempotency_conflict';
            END IF;
            RETURN prior_event.rule_version_id;
          END IF;

          IF requested_action='create' THEN
            IF payload ? 'benefit_id' AND NULLIF(payload->>'benefit_id','') IS NOT NULL AND NOT EXISTS(
              SELECT 1 FROM public.benefits
              WHERE tenant_id=requested_tenant_id AND id=(payload->>'benefit_id')::uuid
                AND benefit_type='platform_coupon' AND connector_id IS NULL
            ) THEN
              RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_benefit_authority_invalid';
            END IF;
            INSERT INTO public.repurchase_coupon_rule_versions(
              id,tenant_id,rule_key,version,benefit_id,name,amount_minor,minimum_spend_minor,currency,
              product_scope,eligible_product_refs,channel_scope,validity_mode,valid_days,fixed_valid_from,
              fixed_valid_until,issuance_limit,issued_count,status,created_by
            ) VALUES (
              target_id,requested_tenant_id,trim(payload->>'rule_key'),(payload->>'version')::integer,
              NULLIF(payload->>'benefit_id','')::uuid,trim(payload->>'name'),(payload->>'amount_minor')::integer,
              (payload->>'minimum_spend_minor')::integer,'CNY',payload->>'product_scope',
              COALESCE(payload->'eligible_product_refs','[]'::jsonb),payload->>'channel_scope',
              payload->>'validity_mode',NULLIF(payload->>'valid_days','')::integer,
              NULLIF(payload->>'fixed_valid_from','')::timestamptz,NULLIF(payload->>'fixed_valid_until','')::timestamptz,
              (payload->>'issuance_limit')::integer,0,'draft',NULLIF(payload->>'actor_id','')::uuid
            );
            old_status := NULL;
            target_status := 'draft';
          ELSE
            SELECT * INTO current_rule FROM public.repurchase_coupon_rule_versions
            WHERE tenant_id=requested_tenant_id AND id=target_id FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='coupon_rule_not_found'; END IF;
            old_status := current_rule.status;
            target_status := CASE requested_action WHEN 'publish' THEN 'published' WHEN 'pause' THEN 'paused'
              WHEN 'resume' THEN 'published' WHEN 'end' THEN 'ended' END;
            IF (requested_action='publish' AND old_status<>'draft')
               OR (requested_action='pause' AND old_status<>'published')
               OR (requested_action='resume' AND old_status<>'paused')
               OR (requested_action='end' AND old_status NOT IN ('published','paused')) THEN
              RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon_rule_transition_invalid';
            END IF;
            UPDATE public.repurchase_coupon_rule_versions SET status=target_status,
              published_at=CASE WHEN requested_action='publish' THEN statement_timestamp() ELSE published_at END,
              ended_at=CASE WHEN requested_action='end' THEN statement_timestamp() ELSE ended_at END,
              updated_at=statement_timestamp()
            WHERE tenant_id=requested_tenant_id AND id=target_id;
          END IF;
          INSERT INTO public.repurchase_coupon_events(
            id,tenant_id,rule_version_id,event_type,from_status,to_status,idempotency_key,payload_digest,
            actor_type,actor_id,details
          ) VALUES (
            (payload->>'event_id')::uuid,requested_tenant_id,target_id,target_event_type,old_status,target_status,
            payload->>'idempotency_key',payload->>'payload_digest','brand',NULLIF(payload->>'actor_id','')::uuid,'{}'::jsonb
          );
          RETURN target_id;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.mutate_repurchase_coupon_rule_authority(uuid,text,jsonb) FROM PUBLIC")
    op.execute(
        r"""
        CREATE FUNCTION public.mutate_member_coupon_authority(
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
    op.execute("REVOKE ALL ON FUNCTION public.mutate_member_coupon_authority(uuid,text,jsonb) FROM PUBLIC")


def upgrade() -> None:
    op.create_table(
        "repurchase_coupon_rule_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("rule_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("benefit_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("minimum_spend_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("product_scope", sa.String(20), nullable=False),
        sa.Column("eligible_product_refs", sa.JSON(), nullable=False),
        sa.Column("channel_scope", sa.String(20), nullable=False),
        sa.Column("validity_mode", sa.String(20), nullable=False),
        sa.Column("valid_days", sa.Integer(), nullable=True),
        sa.Column("fixed_valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fixed_valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issuance_limit", sa.Integer(), nullable=False),
        sa.Column("issued_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.statement_timestamp(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.statement_timestamp(), nullable=False
        ),
        sa.CheckConstraint("length(trim(rule_key)) BETWEEN 1 AND 64", name="ck_coupon_rules_key"),
        sa.CheckConstraint("version > 0", name="ck_coupon_rules_version"),
        sa.CheckConstraint("amount_minor > 0", name="ck_coupon_rules_amount"),
        sa.CheckConstraint(
            "minimum_spend_minor = 0 OR minimum_spend_minor > amount_minor",
            name="ck_coupon_rules_minimum_spend",
        ),
        sa.CheckConstraint("currency='CNY'", name="ck_coupon_rules_currency"),
        sa.CheckConstraint("product_scope IN ('all','specified')", name="ck_coupon_rules_product_scope"),
        sa.CheckConstraint("channel_scope IN ('online','store','both')", name="ck_coupon_rules_channel_scope"),
        sa.CheckConstraint(
            "(validity_mode='relative' AND valid_days BETWEEN 1 AND 365 AND fixed_valid_from IS NULL "
            "AND fixed_valid_until IS NULL) OR (validity_mode='fixed' AND valid_days IS NULL "
            "AND fixed_valid_from IS NOT NULL AND fixed_valid_until>fixed_valid_from)",
            name="ck_coupon_rules_validity",
        ),
        sa.CheckConstraint("issuance_limit > 0", name="ck_coupon_rules_issuance_limit"),
        sa.CheckConstraint("issued_count BETWEEN 0 AND issuance_limit", name="ck_coupon_rules_issued_count"),
        sa.CheckConstraint("status IN ('draft','published','paused','ended')", name="ck_coupon_rules_status"),
        sa.CheckConstraint(
            "(status='draft' AND published_at IS NULL AND ended_at IS NULL) OR "
            "(status IN ('published','paused') AND published_at IS NOT NULL AND ended_at IS NULL) OR "
            "(status='ended' AND published_at IS NOT NULL AND ended_at IS NOT NULL)",
            name="ck_coupon_rules_lifecycle_times",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_coupon_rules_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "benefit_id"], ["benefits.tenant_id", "benefits.id"], name="fk_coupon_rules_tenant_benefit"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["accounts.tenant_id", "accounts.id"], name="fk_coupon_rules_tenant_creator"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_coupon_rules_tenant_id", "repurchase_coupon_rule_versions", ["tenant_id", "id"], unique=True)
    op.create_index(
        "uq_coupon_rules_tenant_key_version",
        "repurchase_coupon_rule_versions",
        ["tenant_id", "rule_key", "version"],
        unique=True,
    )
    op.create_index(
        "uq_coupon_rules_tenant_benefit",
        "repurchase_coupon_rule_versions",
        ["tenant_id", "benefit_id"],
        unique=True,
        postgresql_where=sa.text("benefit_id IS NOT NULL"),
    )
    op.create_index("ix_coupon_rules_tenant_status", "repurchase_coupon_rule_versions", ["tenant_id", "status"])

    op.create_table(
        "member_coupons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("rule_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_claim_id", sa.Uuid(), nullable=True),
        sa.Column("source_scan_event_id", sa.Uuid(), nullable=True),
        sa.Column("source_scan_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_public_id", sa.String(20), nullable=True),
        sa.Column("coupon_number", sa.String(24), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_order_ref", sa.String(160), nullable=True),
        sa.Column("reservation_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reserved_discount_minor", sa.Integer(), nullable=True),
        sa.Column("used_order_ref", sa.String(160), nullable=True),
        sa.Column("used_store_id", sa.Uuid(), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(500), nullable=True),
        sa.Column("authority_type", sa.String(20), nullable=False),
        sa.Column("external_connector_id", sa.Uuid(), nullable=True),
        sa.Column("external_coupon_ref", sa.String(200), nullable=True),
        sa.Column("sync_status", sa.String(20), nullable=False),
        sa.Column("sync_error", sa.String(500), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.func.statement_timestamp(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.statement_timestamp(), nullable=False
        ),
        sa.CheckConstraint("coupon_number ~ '^RCP-[0-9A-F]{16}$'", name="ck_member_coupons_number"),
        sa.CheckConstraint(
            "status IN ('available','reserved','used','expired','revoked')", name="ck_member_coupons_status"
        ),
        sa.CheckConstraint("valid_until>valid_from", name="ck_member_coupons_validity"),
        sa.CheckConstraint(
            "(source_scan_event_id IS NULL) = (source_scan_time IS NULL)",
            name="ck_member_coupons_scan_source",
        ),
        sa.CheckConstraint(
            "(status='reserved' AND reserved_order_ref IS NOT NULL AND reservation_expires_at IS NOT NULL "
            "AND reserved_discount_minor>0) OR (status<>'reserved' AND reserved_order_ref IS NULL "
            "AND reservation_expires_at IS NULL AND reserved_discount_minor IS NULL)",
            name="ck_member_coupons_reservation",
        ),
        sa.CheckConstraint(
            "(status='used' AND used_at IS NOT NULL AND (used_order_ref IS NOT NULL OR used_store_id IS NOT NULL)) "
            "OR (status<>'used' AND used_at IS NULL AND used_order_ref IS NULL AND used_store_id IS NULL)",
            name="ck_member_coupons_usage",
        ),
        sa.CheckConstraint(
            "(status='revoked' AND revoked_at IS NOT NULL AND NULLIF(trim(revoke_reason),'') IS NOT NULL) OR "
            "(status<>'revoked' AND revoked_at IS NULL AND revoke_reason IS NULL)",
            name="ck_member_coupons_revocation",
        ),
        sa.CheckConstraint("authority_type IN ('yimatong','external')", name="ck_member_coupons_authority"),
        sa.CheckConstraint(
            "(authority_type='yimatong' AND external_connector_id IS NULL AND external_coupon_ref IS NULL "
            "AND sync_status='not_required' AND sync_error IS NULL) OR (authority_type='external' "
            "AND external_connector_id IS NOT NULL AND NULLIF(trim(external_coupon_ref),'') IS NOT NULL "
            "AND sync_status IN ('pending','synchronized','error'))",
            name="ck_member_coupons_external_sync",
        ),
        sa.CheckConstraint("version>0", name="ck_member_coupons_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_member_coupons_tenant_membership",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_version_id"],
            ["repurchase_coupon_rule_versions.tenant_id", "repurchase_coupon_rule_versions.id"],
            name="fk_member_coupons_tenant_rule",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_claim_id"],
            ["benefit_claims.tenant_id", "benefit_claims.id"],
            name="fk_member_coupons_tenant_claim",
        ),
        sa.ForeignKeyConstraint(
            ["source_scan_event_id", "source_scan_time"],
            ["scan_events.id", "scan_events.scan_time"],
            name="fk_member_coupons_scan",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "used_store_id"], ["stores.tenant_id", "stores.id"], name="fk_member_coupons_tenant_store"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "external_connector_id"],
            ["connectors.tenant_id", "connectors.id"],
            name="fk_member_coupons_tenant_connector",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_member_coupons_tenant_id", "member_coupons", ["tenant_id", "id"], unique=True)
    op.create_index("uq_member_coupons_tenant_number", "member_coupons", ["tenant_id", "coupon_number"], unique=True)
    op.create_index(
        "uq_member_coupons_tenant_claim",
        "member_coupons",
        ["tenant_id", "source_claim_id"],
        unique=True,
        postgresql_where=sa.text("source_claim_id IS NOT NULL"),
    )
    op.create_index(
        "uq_member_coupons_active_rule",
        "member_coupons",
        ["tenant_id", "membership_id", "rule_version_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('available','reserved')"),
    )
    op.create_index(
        "ix_member_coupons_wallet", "member_coupons", ["tenant_id", "membership_id", "status", "valid_until"]
    )

    op.create_table(
        "repurchase_coupon_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("rule_version_id", sa.Uuid(), nullable=False),
        sa.Column("coupon_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("order_ref", sa.String(160), nullable=True),
        sa.Column("store_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.String(30), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.statement_timestamp(), nullable=False
        ),
        sa.CheckConstraint(
            "event_type IN ('rule_created','rule_published','rule_paused','rule_resumed','rule_ended',"
            "'issued','reserved','committed','released','reversed','expired','revoked','store_redeemed',"
            "'external_sync_pending','external_sync_confirmed','external_sync_error')",
            name="ck_coupon_events_type",
        ),
        sa.CheckConstraint("length(idempotency_key) BETWEEN 1 AND 120", name="ck_coupon_events_idempotency"),
        sa.CheckConstraint("length(payload_digest)=64", name="ck_coupon_events_digest"),
        sa.CheckConstraint(
            "actor_type IN ('consumer','store','brand','service','system')", name="ck_coupon_events_actor"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_version_id"],
            ["repurchase_coupon_rule_versions.tenant_id", "repurchase_coupon_rule_versions.id"],
            name="fk_coupon_events_tenant_rule",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "coupon_id"],
            ["member_coupons.tenant_id", "member_coupons.id"],
            name="fk_coupon_events_tenant_coupon",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "store_id"], ["stores.tenant_id", "stores.id"], name="fk_coupon_events_tenant_store"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_coupon_events_tenant_id", "repurchase_coupon_events", ["tenant_id", "id"], unique=True)
    op.create_index(
        "uq_coupon_events_tenant_idempotency",
        "repurchase_coupon_events",
        ["tenant_id", "idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_coupon_events_coupon_time", "repurchase_coupon_events", ["tenant_id", "coupon_id", "occurred_at"]
    )
    op.create_index(
        "ix_coupon_events_rule_time", "repurchase_coupon_events", ["tenant_id", "rule_version_id", "occurred_at"]
    )

    for table in _TABLES:
        _tenant_policy(table, append_only=table == "repurchase_coupon_events")

    op.execute(
        """
        CREATE FUNCTION public.guard_repurchase_coupon_event() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $f$
        BEGIN
          RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='repurchase coupon events are immutable';
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_repurchase_coupon_event() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_repurchase_coupon_event BEFORE UPDATE OR DELETE "
        "ON public.repurchase_coupon_events FOR EACH ROW "
        "EXECUTE FUNCTION public.guard_repurchase_coupon_event()"
    )
    op.execute(
        """
        CREATE FUNCTION public.guard_repurchase_coupon_rule_snapshot() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $f$
        BEGIN
          IF OLD.status<>'draft' AND (
            NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
            OR NEW.rule_key IS DISTINCT FROM OLD.rule_key OR NEW.version IS DISTINCT FROM OLD.version
            OR NEW.benefit_id IS DISTINCT FROM OLD.benefit_id OR NEW.name IS DISTINCT FROM OLD.name
            OR NEW.amount_minor IS DISTINCT FROM OLD.amount_minor
            OR NEW.minimum_spend_minor IS DISTINCT FROM OLD.minimum_spend_minor
            OR NEW.currency IS DISTINCT FROM OLD.currency OR NEW.product_scope IS DISTINCT FROM OLD.product_scope
            OR NEW.eligible_product_refs::jsonb IS DISTINCT FROM OLD.eligible_product_refs::jsonb
            OR NEW.channel_scope IS DISTINCT FROM OLD.channel_scope
            OR NEW.validity_mode IS DISTINCT FROM OLD.validity_mode OR NEW.valid_days IS DISTINCT FROM OLD.valid_days
            OR NEW.fixed_valid_from IS DISTINCT FROM OLD.fixed_valid_from
            OR NEW.fixed_valid_until IS DISTINCT FROM OLD.fixed_valid_until
            OR NEW.issuance_limit IS DISTINCT FROM OLD.issuance_limit
            OR NEW.created_by IS DISTINCT FROM OLD.created_by OR NEW.created_at IS DISTINCT FROM OLD.created_at
          ) THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='published coupon rule snapshot is immutable';
          END IF;
          IF (OLD.status='draft' AND NEW.status NOT IN ('draft','published'))
             OR (OLD.status='published' AND NEW.status NOT IN ('published','paused','ended'))
             OR (OLD.status='paused' AND NEW.status NOT IN ('paused','published','ended'))
             OR OLD.status='ended' THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='invalid coupon rule transition';
          END IF;
          RETURN NEW;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_repurchase_coupon_rule_snapshot() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_repurchase_coupon_rule_snapshot BEFORE UPDATE "
        "ON public.repurchase_coupon_rule_versions FOR EACH ROW "
        "EXECUTE FUNCTION public.guard_repurchase_coupon_rule_snapshot()"
    )
    op.execute(
        """
        CREATE FUNCTION public.guard_member_coupon_source() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $f$
        BEGIN
          IF NEW.source_scan_event_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM public.scan_events AS scan
            WHERE scan.id=NEW.source_scan_event_id AND scan.scan_time=NEW.source_scan_time
              AND scan.tenant_id=NEW.tenant_id
          ) THEN
            RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='coupon source scan is outside tenant';
          END IF;
          RETURN NEW;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_member_coupon_source() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_member_coupon_source BEFORE INSERT ON public.member_coupons "
        "FOR EACH ROW EXECUTE FUNCTION public.guard_member_coupon_source()"
    )
    op.execute(
        """
        CREATE FUNCTION public.guard_member_coupon_transition() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $f$
        BEGIN
          IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
             OR NEW.membership_id IS DISTINCT FROM OLD.membership_id
             OR NEW.rule_version_id IS DISTINCT FROM OLD.rule_version_id
             OR NEW.source_claim_id IS DISTINCT FROM OLD.source_claim_id
             OR NEW.source_scan_event_id IS DISTINCT FROM OLD.source_scan_event_id
             OR NEW.source_scan_time IS DISTINCT FROM OLD.source_scan_time
             OR NEW.source_public_id IS DISTINCT FROM OLD.source_public_id
             OR NEW.coupon_number IS DISTINCT FROM OLD.coupon_number
             OR NEW.valid_from IS DISTINCT FROM OLD.valid_from OR NEW.valid_until IS DISTINCT FROM OLD.valid_until
             OR NEW.authority_type IS DISTINCT FROM OLD.authority_type
             OR NEW.external_connector_id IS DISTINCT FROM OLD.external_connector_id
             OR NEW.external_coupon_ref IS DISTINCT FROM OLD.external_coupon_ref
             OR NEW.issued_at IS DISTINCT FROM OLD.issued_at THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='coupon identity and commercial terms are immutable';
          END IF;
          IF NOT (
            (OLD.status='available' AND NEW.status IN ('available','reserved','used','expired','revoked'))
            OR (OLD.status='reserved' AND NEW.status IN ('reserved','available','used','expired'))
            OR (OLD.status='used' AND NEW.status IN ('used','available','expired'))
            OR (OLD.status IN ('expired','revoked') AND NEW.status=OLD.status)
          ) THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='invalid coupon asset transition';
          END IF;
          IF NEW.version<>OLD.version+1 THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon transition version must increment';
          END IF;
          RETURN NEW;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_member_coupon_transition() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_member_coupon_transition BEFORE UPDATE ON public.member_coupons "
        "FOR EACH ROW EXECUTE FUNCTION public.guard_member_coupon_transition()"
    )
    _create_authority_functions()
    op.execute(
        "DO $do$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app') THEN "
        "REVOKE ALL ON public.repurchase_coupon_rule_versions,public.member_coupons,public.repurchase_coupon_events "
        "FROM yimatong_app; "
        "GRANT SELECT ON public.repurchase_coupon_rule_versions,public.member_coupons,public.repurchase_coupon_events "
        "TO yimatong_app; "
        "GRANT EXECUTE ON FUNCTION public.mutate_repurchase_coupon_rule_authority(uuid,text,jsonb) TO yimatong_app; "
        "GRANT EXECUTE ON FUNCTION public.mutate_member_coupon_authority(uuid,text,jsonb) TO yimatong_app; "
        "END IF; END $do$"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $do$ BEGIN
          IF EXISTS(SELECT 1 FROM public.repurchase_coupon_rule_versions LIMIT 1)
             OR EXISTS(SELECT 1 FROM public.member_coupons LIMIT 1)
             OR EXISTS(SELECT 1 FROM public.repurchase_coupon_events LIMIT 1) THEN
            RAISE EXCEPTION USING ERRCODE='55000',
              MESSAGE='cannot downgrade repurchase coupon authority while durable coupon facts exist; '
                'use forward recovery';
          END IF;
        END $do$;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS public.mutate_member_coupon_authority(uuid,text,jsonb)")
    op.execute("DROP FUNCTION IF EXISTS public.mutate_repurchase_coupon_rule_authority(uuid,text,jsonb)")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_member_coupon_transition ON public.member_coupons")
    op.execute("DROP FUNCTION IF EXISTS public.guard_member_coupon_transition()")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_member_coupon_source ON public.member_coupons")
    op.execute("DROP FUNCTION IF EXISTS public.guard_member_coupon_source()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_guard_repurchase_coupon_rule_snapshot ON public.repurchase_coupon_rule_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS public.guard_repurchase_coupon_rule_snapshot()")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_repurchase_coupon_event ON public.repurchase_coupon_events")
    op.execute("DROP FUNCTION IF EXISTS public.guard_repurchase_coupon_event()")
    op.drop_table("repurchase_coupon_events")
    op.drop_table("member_coupons")
    op.drop_table("repurchase_coupon_rule_versions")
