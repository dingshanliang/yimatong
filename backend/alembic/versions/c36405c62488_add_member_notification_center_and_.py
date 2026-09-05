"""Add member notification center and delivery authority.

Revision ID: c36405c62488
Revises: c8a8c3346d7c
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c36405c62488"
down_revision: Union[str, None] = "c8a8c3346d7c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "member_notification_preferences",
    "member_channel_grants",
    "member_notifications",
    "member_notification_deliveries",
)


def _plain(sql: str) -> None:
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _plain(
        r"""
        CREATE TABLE public.member_notification_preferences(
          id uuid PRIMARY KEY,tenant_id uuid NOT NULL,membership_id uuid NOT NULL,
          marketing_enabled boolean NOT NULL DEFAULT false,service_wechat_enabled boolean NOT NULL DEFAULT true,
          critical_sms_enabled boolean NOT NULL DEFAULT false,marketing_consent_id uuid,
          marketing_opted_in_at timestamptz,marketing_opted_out_at timestamptz,version integer NOT NULL DEFAULT 1,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_member_notification_preferences_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_member_notification_preferences_membership UNIQUE(tenant_id,membership_id),
          CONSTRAINT fk_member_notification_preferences_membership FOREIGN KEY(tenant_id,membership_id)
            REFERENCES public.brand_memberships(tenant_id,id),
          CONSTRAINT fk_member_notification_preferences_consent FOREIGN KEY(tenant_id,marketing_consent_id)
            REFERENCES public.consent_records(tenant_id,id),
          CONSTRAINT ck_member_notification_preferences_version CHECK(version>0),
          CONSTRAINT ck_member_notification_preferences_marketing CHECK(
            (marketing_enabled AND marketing_consent_id IS NOT NULL AND marketing_opted_in_at IS NOT NULL
             AND marketing_opted_out_at IS NULL) OR NOT marketing_enabled)
        );
        CREATE TABLE public.member_channel_grants(
          id uuid PRIMARY KEY,tenant_id uuid NOT NULL,membership_id uuid NOT NULL,channel varchar(30) NOT NULL,
          template_code varchar(80) NOT NULL,purpose varchar(20) NOT NULL,authorization_ref varchar(160) NOT NULL,
          status varchar(20) NOT NULL,authorized_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
          consumed_at timestamptz,created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_member_channel_grants_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_member_channel_grants_authorization_ref UNIQUE(tenant_id,authorization_ref),
          CONSTRAINT fk_member_channel_grants_membership FOREIGN KEY(tenant_id,membership_id)
            REFERENCES public.brand_memberships(tenant_id,id),
          CONSTRAINT ck_member_channel_grants_channel CHECK(channel='wechat_subscription'),
          CONSTRAINT ck_member_channel_grants_purpose CHECK(purpose IN ('service','marketing')),
          CONSTRAINT ck_member_channel_grants_status CHECK(status IN ('available','consumed','expired','revoked')),
          CONSTRAINT ck_member_channel_grants_validity CHECK(expires_at>authorized_at),
          CONSTRAINT ck_member_channel_grants_consumption CHECK((status='consumed' AND consumed_at IS NOT NULL)
            OR (status<>'consumed' AND consumed_at IS NULL))
        );
        CREATE INDEX ix_member_channel_grants_available
          ON public.member_channel_grants(tenant_id,membership_id,template_code,status);
        CREATE TABLE public.member_notifications(
          id uuid PRIMARY KEY,tenant_id uuid NOT NULL,membership_id uuid NOT NULL,
          notification_class varchar(20) NOT NULL,notification_type varchar(50) NOT NULL,
          source_product varchar(30) NOT NULL,source_event_id varchar(160) NOT NULL,source_event_version integer NOT NULL,
          object_ref varchar(160) NOT NULL,title varchar(120) NOT NULL,body varchar(500) NOT NULL,
          action_path varchar(300),facts jsonb NOT NULL,occurred_at timestamptz NOT NULL,read_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_member_notifications_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_member_notifications_source_event UNIQUE(tenant_id,source_product,source_event_id,
            source_event_version,notification_type),
          CONSTRAINT fk_member_notifications_membership FOREIGN KEY(tenant_id,membership_id)
            REFERENCES public.brand_memberships(tenant_id,id),
          CONSTRAINT ck_member_notifications_class CHECK(notification_class IN ('service','marketing')),
          CONSTRAINT ck_member_notifications_event_version CHECK(source_event_version>0)
        );
        CREATE INDEX ix_member_notifications_inbox
          ON public.member_notifications(tenant_id,membership_id,occurred_at);
        CREATE TABLE public.member_notification_deliveries(
          id uuid PRIMARY KEY,tenant_id uuid NOT NULL,notification_id uuid NOT NULL,channel_grant_id uuid,
          channel varchar(30) NOT NULL,template_code varchar(80) NOT NULL,template_version varchar(40) NOT NULL,
          status varchar(30) NOT NULL,suppression_reason varchar(80),attempt_count integer NOT NULL DEFAULT 0,
          next_attempt_at timestamptz,accepted_at timestamptz,exhausted_at timestamptz,
          channel_message_ref varchar(160),last_error_code varchar(80),consent_snapshot jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_member_notification_deliveries_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_member_notification_deliveries_channel UNIQUE(tenant_id,notification_id,channel),
          CONSTRAINT fk_member_notification_deliveries_notification FOREIGN KEY(tenant_id,notification_id)
            REFERENCES public.member_notifications(tenant_id,id),
          CONSTRAINT fk_member_notification_deliveries_grant FOREIGN KEY(tenant_id,channel_grant_id)
            REFERENCES public.member_channel_grants(tenant_id,id),
          CONSTRAINT ck_member_notification_deliveries_channel CHECK(channel IN ('wechat_subscription','sms')),
          CONSTRAINT ck_member_notification_deliveries_status CHECK(status IN
            ('authorization_missing','pending','accepted','failed','exhausted','suppressed')),
          CONSTRAINT ck_member_notification_deliveries_attempts CHECK(attempt_count BETWEEN 0 AND 3)
        );
        CREATE INDEX ix_member_notification_deliveries_due
          ON public.member_notification_deliveries(tenant_id,status,next_attempt_at);
        """
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON public.{table} TO yimatong_app "
            "USING(tenant_id=public.current_tenant_id())"
        )
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC,yimatong_app,yimatong_callback")
        op.execute(f"GRANT SELECT ON public.{table} TO yimatong_app")

    op.execute(
        r"""
        CREATE FUNCTION public.mutate_member_notification_preference_authority(requested_tenant_id uuid,payload jsonb)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE preference_id uuid; action_name text:=payload->>'action'; now_at timestamptz:=statement_timestamp();
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
             OR NOT EXISTS(SELECT 1 FROM public.brand_membership_profile_links link
               JOIN public.brand_memberships membership ON membership.tenant_id=link.tenant_id
                 AND membership.id=link.membership_id AND membership.status='active'
               WHERE link.tenant_id=requested_tenant_id AND link.membership_id=(payload->>'membership_id')::uuid
                 AND link.consumer_profile_id=(payload->>'consumer_id')::uuid) THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member notification preference authority denied';
          END IF;
          SELECT id INTO preference_id FROM public.member_notification_preferences
          WHERE tenant_id=requested_tenant_id AND membership_id=(payload->>'membership_id')::uuid FOR UPDATE;
          IF NOT FOUND THEN
            preference_id:=(payload->>'preference_id')::uuid;
            INSERT INTO public.member_notification_preferences(id,tenant_id,membership_id)
            VALUES(preference_id,requested_tenant_id,(payload->>'membership_id')::uuid);
          END IF;
          IF action_name='subscribe_marketing' THEN
            IF NOT EXISTS(SELECT 1 FROM public.consent_records consent
              WHERE consent.tenant_id=requested_tenant_id AND consent.id=(payload->>'marketing_consent_id')::uuid
                AND consent.consumer_id=(payload->>'consumer_id')::uuid
                AND consent.purpose IN ('marketing','lead_capture')
                AND consent.consent_type='marketing' AND consent.status='granted' AND consent.withdrawn_at IS NULL) THEN
              RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='active marketing consent required';
            END IF;
            UPDATE public.member_notification_preferences SET marketing_enabled=true,
              marketing_consent_id=(payload->>'marketing_consent_id')::uuid,marketing_opted_in_at=now_at,
              marketing_opted_out_at=NULL,version=version+1,updated_at=now_at
            WHERE tenant_id=requested_tenant_id AND id=preference_id;
            INSERT INTO public.member_channel_grants(id,tenant_id,membership_id,channel,template_code,purpose,
              authorization_ref,status,authorized_at,expires_at)
            VALUES((payload->>'grant_id')::uuid,requested_tenant_id,(payload->>'membership_id')::uuid,
              'wechat_subscription',payload->>'template_code','marketing',payload->>'authorization_ref','available',
              (payload->>'authorized_at')::timestamptz,(payload->>'expires_at')::timestamptz)
            ON CONFLICT(tenant_id,authorization_ref) DO NOTHING;
          ELSIF action_name='unsubscribe_marketing' THEN
            UPDATE public.member_notification_preferences SET marketing_enabled=false,marketing_opted_out_at=now_at,
              version=version+1,updated_at=now_at WHERE tenant_id=requested_tenant_id AND id=preference_id;
            UPDATE public.member_channel_grants SET status='revoked' WHERE tenant_id=requested_tenant_id
              AND membership_id=(payload->>'membership_id')::uuid AND purpose='marketing' AND status='available';
            UPDATE public.member_notification_deliveries delivery SET status='suppressed',
              suppression_reason='marketing_opted_out',next_attempt_at=NULL,updated_at=now_at
            FROM public.member_notifications notification WHERE notification.tenant_id=requested_tenant_id
              AND notification.membership_id=(payload->>'membership_id')::uuid
              AND notification.notification_class='marketing' AND delivery.tenant_id=notification.tenant_id
              AND delivery.notification_id=notification.id AND delivery.status IN ('pending','failed');
          ELSIF action_name='set_service_wechat' THEN
            UPDATE public.member_notification_preferences SET service_wechat_enabled=(payload->>'enabled')::boolean,
              version=version+1,updated_at=now_at WHERE tenant_id=requested_tenant_id AND id=preference_id;
          ELSIF action_name='grant_service_wechat' THEN
            INSERT INTO public.member_channel_grants(id,tenant_id,membership_id,channel,template_code,purpose,
              authorization_ref,status,authorized_at,expires_at)
            VALUES((payload->>'grant_id')::uuid,requested_tenant_id,(payload->>'membership_id')::uuid,
              'wechat_subscription',payload->>'template_code','service',payload->>'authorization_ref','available',
              (payload->>'authorized_at')::timestamptz,(payload->>'expires_at')::timestamptz)
            ON CONFLICT(tenant_id,authorization_ref) DO NOTHING;
          ELSE RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid notification preference action'; END IF;
          RETURN preference_id;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.mutate_member_notification_preference_authority(uuid,jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.mutate_member_notification_preference_authority(uuid,jsonb) TO yimatong_app")

    op.execute(
        r"""
        CREATE FUNCTION public.record_commerce_member_notification_authority(requested_tenant_id uuid,message_row_id uuid)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE message_row public.commerce_integration_messages%ROWTYPE; order_row public.commerce_order_facts%ROWTYPE;
          notification_id uuid; grant_id uuid; type_value text; title_value text; body_value text; template_value text;
          service_enabled boolean:=true;
        BEGIN
          IF session_user<>'yimatong_callback' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member notification recording denied';
          END IF;
          SELECT * INTO message_row FROM public.commerce_integration_messages WHERE tenant_id=requested_tenant_id
            AND id=message_row_id AND direction='inbox' AND projected_at IS NOT NULL;
          SELECT * INTO order_row FROM public.commerce_order_facts WHERE tenant_id=requested_tenant_id
            AND last_message_row_id=message_row_id AND membership_id IS NOT NULL;
          IF NOT FOUND OR message_row.message_type NOT IN ('commerce.order.paid','commerce.order.fulfilled',
            'commerce.order.cancelled','commerce.order.refunded') THEN RETURN NULL; END IF;
          SELECT CASE message_row.message_type WHEN 'commerce.order.paid' THEN 'order_paid'
            WHEN 'commerce.order.fulfilled' THEN 'order_fulfilled' WHEN 'commerce.order.cancelled' THEN 'order_cancelled'
            ELSE 'order_refunded' END,
            CASE message_row.message_type WHEN 'commerce.order.paid' THEN '订单支付成功'
            WHEN 'commerce.order.fulfilled' THEN '订单已发货' WHEN 'commerce.order.cancelled' THEN '订单已取消'
            ELSE '订单退款已更新' END,
            CASE message_row.message_type WHEN 'commerce.order.paid' THEN '品牌已确认收到您的付款。'
            WHEN 'commerce.order.fulfilled' THEN '您的订单已由品牌安排发货。'
            WHEN 'commerce.order.cancelled' THEN '您的订单已取消，相关交易事实已更新。'
            ELSE '退款结果已确认，商品净额已同步更新。' END
          INTO type_value,title_value,body_value;
          template_value:=type_value;
          SELECT id INTO notification_id FROM public.member_notifications WHERE tenant_id=requested_tenant_id
            AND source_product='commerce' AND source_event_id=message_row.message_id
            AND source_event_version=message_row.message_version AND notification_type=type_value;
          IF FOUND THEN RETURN notification_id; END IF;
          notification_id:=(md5(requested_tenant_id::text||message_row.id::text||type_value))::uuid;
          INSERT INTO public.member_notifications(id,tenant_id,membership_id,notification_class,notification_type,
            source_product,source_event_id,source_event_version,object_ref,title,body,action_path,facts,occurred_at)
          VALUES(notification_id,requested_tenant_id,order_row.membership_id,'service',type_value,'commerce',
            message_row.message_id,message_row.message_version,order_row.external_order_ref,title_value,body_value,
            '/member/orders/'||order_row.external_order_ref,
            jsonb_build_object('status',order_row.status,'currency',order_row.currency,
              'product_net_amount_fen',order_row.product_net_amount_fen),message_row.occurred_at);
          SELECT COALESCE(service_wechat_enabled,true) INTO service_enabled FROM public.member_notification_preferences
          WHERE tenant_id=requested_tenant_id AND membership_id=order_row.membership_id;
          IF service_enabled THEN
            SELECT id INTO grant_id FROM public.member_channel_grants WHERE tenant_id=requested_tenant_id
              AND membership_id=order_row.membership_id AND template_code=template_value AND purpose='service'
              AND status='available' AND expires_at>statement_timestamp() ORDER BY authorized_at LIMIT 1 FOR UPDATE;
          END IF;
          INSERT INTO public.member_notification_deliveries(id,tenant_id,notification_id,channel_grant_id,channel,
            template_code,template_version,status,suppression_reason,next_attempt_at,consent_snapshot)
          VALUES((md5(requested_tenant_id::text||notification_id::text||'wechat'))::uuid,requested_tenant_id,
            notification_id,grant_id,'wechat_subscription',template_value,'v1',
            CASE WHEN NOT service_enabled THEN 'suppressed' WHEN grant_id IS NULL THEN 'authorization_missing' ELSE 'pending' END,
            CASE WHEN NOT service_enabled THEN 'service_channel_disabled'
              WHEN grant_id IS NULL THEN 'wechat_authorization_missing' END,
            CASE WHEN grant_id IS NOT NULL AND service_enabled THEN statement_timestamp() END,
            jsonb_build_object('marketing_required',false,'grant_id',grant_id));
          IF grant_id IS NOT NULL THEN UPDATE public.member_channel_grants SET status='consumed',consumed_at=statement_timestamp()
            WHERE tenant_id=requested_tenant_id AND id=grant_id; END IF;
          RETURN notification_id;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.record_commerce_member_notification_authority(uuid,uuid) FROM PUBLIC,yimatong_app")
    op.execute("GRANT EXECUTE ON FUNCTION public.record_commerce_member_notification_authority(uuid,uuid) TO yimatong_callback")

    op.execute(
        r"""
        CREATE FUNCTION public.record_coupon_member_notification_authority(requested_tenant_id uuid,coupon_event_id uuid)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE event_row public.repurchase_coupon_events%ROWTYPE; coupon_row public.member_coupons%ROWTYPE;
          rule_row public.repurchase_coupon_rule_versions%ROWTYPE; notification_id uuid; grant_id uuid;
          type_value text; title_value text; body_value text; service_enabled boolean:=true;
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='coupon member notification recording denied';
          END IF;
          SELECT * INTO event_row FROM public.repurchase_coupon_events WHERE tenant_id=requested_tenant_id
            AND id=coupon_event_id AND event_type IN ('issued','committed','store_redeemed');
          IF NOT FOUND OR event_row.coupon_id IS NULL THEN RETURN NULL; END IF;
          SELECT * INTO coupon_row FROM public.member_coupons WHERE tenant_id=requested_tenant_id AND id=event_row.coupon_id;
          SELECT * INTO rule_row FROM public.repurchase_coupon_rule_versions WHERE tenant_id=requested_tenant_id
            AND id=event_row.rule_version_id;
          IF coupon_row.id IS NULL OR rule_row.id IS NULL THEN RETURN NULL; END IF;
          IF event_row.event_type='issued' THEN
            type_value:='coupon_issued';title_value:='优惠券已到账';
            body_value:=rule_row.name||'已存入您的会员券包，有效期至'||to_char(coupon_row.valid_until AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD')||'。';
          ELSE
            type_value:='coupon_used';title_value:='优惠券已使用';body_value:=rule_row.name||'的使用结果已记录。';
          END IF;
          SELECT id INTO notification_id FROM public.member_notifications WHERE tenant_id=requested_tenant_id
            AND source_product='repurchase_coupon' AND source_event_id=coupon_row.id::text AND notification_type=type_value;
          IF FOUND THEN RETURN notification_id; END IF;
          notification_id:=(md5(requested_tenant_id::text||coupon_row.id::text||type_value))::uuid;
          INSERT INTO public.member_notifications(id,tenant_id,membership_id,notification_class,notification_type,
            source_product,source_event_id,source_event_version,object_ref,title,body,action_path,facts,occurred_at)
          VALUES(notification_id,requested_tenant_id,coupon_row.membership_id,'service',type_value,'repurchase_coupon',
            coupon_row.id::text,1,coupon_row.coupon_number,title_value,body_value,'/member/coupons',
            jsonb_build_object('coupon_status',coupon_row.status,'valid_until',coupon_row.valid_until,
              'amount_minor',rule_row.amount_minor,'minimum_spend_minor',rule_row.minimum_spend_minor),event_row.occurred_at);
          SELECT COALESCE(service_wechat_enabled,true) INTO service_enabled FROM public.member_notification_preferences
          WHERE tenant_id=requested_tenant_id AND membership_id=coupon_row.membership_id;
          IF type_value='coupon_issued' AND service_enabled THEN
            SELECT id INTO grant_id FROM public.member_channel_grants WHERE tenant_id=requested_tenant_id
              AND membership_id=coupon_row.membership_id AND template_code=type_value AND purpose='service'
              AND status='available' AND expires_at>statement_timestamp() ORDER BY authorized_at LIMIT 1 FOR UPDATE;
          END IF;
          INSERT INTO public.member_notification_deliveries(id,tenant_id,notification_id,channel_grant_id,channel,
            template_code,template_version,status,suppression_reason,next_attempt_at,consent_snapshot)
          VALUES((md5(requested_tenant_id::text||notification_id::text||'wechat'))::uuid,requested_tenant_id,
            notification_id,grant_id,'wechat_subscription',type_value,'v1',
            CASE WHEN type_value='coupon_used' OR NOT service_enabled THEN 'suppressed'
              WHEN grant_id IS NULL THEN 'authorization_missing' ELSE 'pending' END,
            CASE WHEN type_value='coupon_used' THEN 'channel_not_applicable'
              WHEN NOT service_enabled THEN 'service_channel_disabled'
              WHEN grant_id IS NULL THEN 'wechat_authorization_missing' END,
            CASE WHEN grant_id IS NOT NULL AND type_value='coupon_issued' AND service_enabled THEN statement_timestamp() END,
            jsonb_build_object('marketing_required',false,'grant_id',grant_id));
          IF grant_id IS NOT NULL THEN UPDATE public.member_channel_grants SET status='consumed',consumed_at=statement_timestamp()
            WHERE tenant_id=requested_tenant_id AND id=grant_id; END IF;
          RETURN notification_id;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.record_coupon_member_notification_authority(uuid,uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.record_coupon_member_notification_authority(uuid,uuid) TO yimatong_app")

    op.execute(
        r"""
        CREATE FUNCTION public.create_member_marketing_notification_authority(requested_tenant_id uuid,payload jsonb)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE notification_id uuid; grant_id uuid; preference public.member_notification_preferences%ROWTYPE;
          daily_count integer; weekly_count integer; delivery_status text; reason_value text; due_at timestamptz;
          occurred timestamptz:=(payload->>'occurred_at')::timestamptz;
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
             OR payload->>'notification_type' NOT IN ('coupon_expiry','repurchase_invite','gift_coupon') THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member marketing notification authority denied';
          END IF;
          SELECT * INTO preference FROM public.member_notification_preferences WHERE tenant_id=requested_tenant_id
            AND membership_id=(payload->>'membership_id')::uuid FOR UPDATE;
          IF NOT FOUND OR NOT preference.marketing_enabled OR NOT EXISTS(SELECT 1 FROM public.consent_records consent
            WHERE consent.tenant_id=requested_tenant_id AND consent.id=preference.marketing_consent_id
              AND consent.status='granted' AND consent.consent_type='marketing'
              AND consent.purpose IN ('marketing','lead_capture') AND consent.withdrawn_at IS NULL) THEN
            RETURN NULL;
          END IF;
          SELECT id INTO notification_id FROM public.member_notifications WHERE tenant_id=requested_tenant_id
            AND source_product=payload->>'source_product' AND source_event_id=payload->>'source_event_id'
            AND source_event_version=(payload->>'source_event_version')::integer
            AND notification_type=payload->>'notification_type';
          IF FOUND THEN RETURN notification_id; END IF;
          SELECT count(*) FILTER(WHERE (notification.occurred_at AT TIME ZONE 'Asia/Shanghai')::date=
              (occurred AT TIME ZONE 'Asia/Shanghai')::date),
            count(*) FILTER(WHERE notification.occurred_at>occurred-interval '7 days')
            INTO daily_count,weekly_count FROM public.member_notifications notification
          WHERE notification.tenant_id=requested_tenant_id AND notification.membership_id=(payload->>'membership_id')::uuid
            AND notification.notification_class='marketing';
          notification_id:=(payload->>'notification_id')::uuid;
          INSERT INTO public.member_notifications(id,tenant_id,membership_id,notification_class,notification_type,
            source_product,source_event_id,source_event_version,object_ref,title,body,action_path,facts,occurred_at)
          VALUES(notification_id,requested_tenant_id,(payload->>'membership_id')::uuid,'marketing',
            payload->>'notification_type',payload->>'source_product',payload->>'source_event_id',
            (payload->>'source_event_version')::integer,payload->>'object_ref',payload->>'title',payload->>'body',
            NULLIF(payload->>'action_path',''),COALESCE(payload->'facts','{}'::jsonb),occurred);
          IF daily_count>=1 OR weekly_count>=3 THEN
            delivery_status:='suppressed'; reason_value:='marketing_frequency_limited';
          ELSE
            SELECT id INTO grant_id FROM public.member_channel_grants WHERE tenant_id=requested_tenant_id
              AND membership_id=(payload->>'membership_id')::uuid AND template_code=payload->>'template_code'
              AND purpose='marketing' AND status='available' AND expires_at>statement_timestamp()
            ORDER BY authorized_at LIMIT 1 FOR UPDATE;
            IF grant_id IS NULL THEN delivery_status:='authorization_missing'; reason_value:='wechat_authorization_missing';
            ELSE delivery_status:='pending';
              due_at:=CASE WHEN extract(hour FROM statement_timestamp() AT TIME ZONE 'Asia/Shanghai')>=21
                THEN ((statement_timestamp() AT TIME ZONE 'Asia/Shanghai')::date+interval '1 day 8 hours') AT TIME ZONE 'Asia/Shanghai'
                WHEN extract(hour FROM statement_timestamp() AT TIME ZONE 'Asia/Shanghai')<8
                THEN ((statement_timestamp() AT TIME ZONE 'Asia/Shanghai')::date+interval '8 hours') AT TIME ZONE 'Asia/Shanghai'
                ELSE statement_timestamp() END;
            END IF;
          END IF;
          INSERT INTO public.member_notification_deliveries(id,tenant_id,notification_id,channel_grant_id,channel,
            template_code,template_version,status,suppression_reason,next_attempt_at,consent_snapshot)
          VALUES((payload->>'delivery_id')::uuid,requested_tenant_id,notification_id,grant_id,'wechat_subscription',
            payload->>'template_code',payload->>'template_version',delivery_status,reason_value,due_at,
            jsonb_build_object('marketing_consent_id',preference.marketing_consent_id,'preference_version',preference.version,
              'grant_id',grant_id));
          IF delivery_status='pending' THEN UPDATE public.member_channel_grants SET status='consumed',
            consumed_at=statement_timestamp() WHERE tenant_id=requested_tenant_id AND id=grant_id; END IF;
          RETURN notification_id;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.create_member_marketing_notification_authority(uuid,jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.create_member_marketing_notification_authority(uuid,jsonb) TO yimatong_app")

    op.execute(
        r"""
        CREATE FUNCTION public.mutate_member_notification_delivery_authority(requested_tenant_id uuid,payload jsonb)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE delivery public.member_notification_deliveries%ROWTYPE; result_value text:=payload->>'result'; next_attempt integer;
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member notification delivery authority denied';
          END IF;
          SELECT * INTO delivery FROM public.member_notification_deliveries WHERE tenant_id=requested_tenant_id
            AND id=(payload->>'delivery_id')::uuid FOR UPDATE;
          IF NOT FOUND OR delivery.status NOT IN ('pending','failed') THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='notification delivery not attemptable'; END IF;
          next_attempt:=delivery.attempt_count+1;
          IF result_value='accepted' THEN
            UPDATE public.member_notification_deliveries SET status='accepted',attempt_count=next_attempt,
              accepted_at=statement_timestamp(),next_attempt_at=NULL,channel_message_ref=payload->>'channel_message_ref',
              last_error_code=NULL,updated_at=statement_timestamp() WHERE tenant_id=requested_tenant_id AND id=delivery.id;
          ELSIF result_value='permanent_failure' OR next_attempt>=3 THEN
            UPDATE public.member_notification_deliveries SET status='exhausted',attempt_count=next_attempt,
              exhausted_at=statement_timestamp(),next_attempt_at=NULL,last_error_code=payload->>'error_code',
              updated_at=statement_timestamp() WHERE tenant_id=requested_tenant_id AND id=delivery.id;
          ELSIF result_value='transient_failure' THEN
            UPDATE public.member_notification_deliveries SET status='failed',attempt_count=next_attempt,
              next_attempt_at=statement_timestamp()+(interval '5 minutes'*power(2,next_attempt-1)),
              last_error_code=payload->>'error_code',updated_at=statement_timestamp()
            WHERE tenant_id=requested_tenant_id AND id=delivery.id;
          ELSE RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid notification delivery result'; END IF;
          RETURN delivery.id;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.mutate_member_notification_delivery_authority(uuid,jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.mutate_member_notification_delivery_authority(uuid,jsonb) TO yimatong_app")


def downgrade() -> None:
    op.execute(
        r"""DO $f$ BEGIN IF EXISTS(SELECT 1 FROM public.member_notifications LIMIT 1)
        OR EXISTS(SELECT 1 FROM public.member_notification_deliveries LIMIT 1)
        THEN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='member notification facts exist; archive before downgrade'; END IF; END $f$;"""
    )
    op.execute("DROP FUNCTION public.mutate_member_notification_delivery_authority(uuid,jsonb)")
    op.execute("DROP FUNCTION public.create_member_marketing_notification_authority(uuid,jsonb)")
    op.execute("DROP FUNCTION public.record_coupon_member_notification_authority(uuid,uuid)")
    op.execute("DROP FUNCTION public.record_commerce_member_notification_authority(uuid,uuid)")
    op.execute("DROP FUNCTION public.mutate_member_notification_preference_authority(uuid,jsonb)")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE public.{table}")
