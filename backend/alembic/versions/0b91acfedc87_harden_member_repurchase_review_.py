"""Harden member repurchase review boundaries.

Revision ID: 0b91acfedc87
Revises: 7fc04855cee3
"""
# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0b91acfedc87"
down_revision: str | Sequence[str] | None = "7fc04855cee3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_member_coupons_active_rule", table_name="member_coupons")
    op.drop_constraint("ck_coupon_events_type", "repurchase_coupon_events", type_="check")
    op.create_check_constraint(
        "ck_coupon_events_type",
        "repurchase_coupon_events",
        "event_type IN ('rule_created','rule_published','rule_paused','rule_resumed','rule_ended',"
        "'issued','reserved','committed','released','reversed','refund_recorded','expired','revoked',"
        "'store_redeemed','external_sync_pending','external_sync_confirmed','external_sync_error')",
    )
    op.create_check_constraint(
        "ck_member_channel_grants_server_window",
        "member_channel_grants",
        "authorized_at BETWEEN created_at-interval '5 minutes' AND created_at+interval '1 minute' "
        "AND expires_at<=authorized_at+interval '7 days'",
    )
    op.add_column("member_notification_deliveries", sa.Column("lease_token", sa.Uuid(), nullable=True))
    op.add_column(
        "member_notification_deliveries",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("ck_member_notification_deliveries_status", "member_notification_deliveries", type_="check")
    op.create_check_constraint(
        "ck_member_notification_deliveries_status",
        "member_notification_deliveries",
        "status IN ('authorization_missing','pending','delivering','accepted','failed','exhausted','suppressed')",
    )
    op.create_check_constraint(
        "ck_member_notification_deliveries_lease",
        "member_notification_deliveries",
        "(status='delivering' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
        "(status<>'delivering' AND lease_token IS NULL AND lease_expires_at IS NULL)",
    )
    _replace_coupon_authority()
    _replace_merge_authority()
    _replace_marketing_authority()
    _restrict_delivery_results()
    _cancel_withdrawn_marketing_deliveries()


def _replace_coupon_authority() -> None:
    op.execute(
        "ALTER FUNCTION public.mutate_member_coupon_authority(uuid,text,jsonb) "
        "RENAME TO mutate_member_coupon_authority_v1"
    )
    op.execute(
        r"""CREATE FUNCTION public.mutate_member_coupon_authority(
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
    op.execute("REVOKE ALL ON FUNCTION public.mutate_member_coupon_authority_v1(uuid,text,jsonb) FROM PUBLIC,yimatong_app")
    op.execute("REVOKE ALL ON FUNCTION public.mutate_member_coupon_authority(uuid,text,jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.mutate_member_coupon_authority(uuid,text,jsonb) TO yimatong_app")


def _replace_merge_authority() -> None:
    signature = "(uuid,uuid,uuid,uuid,uuid,uuid,text,text,uuid,uuid,text,jsonb)"
    op.execute(f"ALTER FUNCTION public.merge_brand_memberships_authority{signature} RENAME TO merge_brand_memberships_authority_v1")
    op.execute(
        r"""CREATE FUNCTION public.merge_brand_memberships_authority(
          requested_tenant_id uuid,requested_source_id uuid,requested_target_id uuid,
          requested_current_consumer_id uuid,requested_source_credential_id uuid,requested_target_credential_id uuid,
          requested_source_token_key text,requested_target_token_key text,requested_source_event_id uuid,
          requested_target_event_id uuid,requested_payload_hash text,requested_reencrypted_credentials jsonb
        ) RETURNS TABLE(membership_id uuid,membership_number text,consumer_id uuid,status text,
          joined_at timestamptz,replayed boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        #variable_conflict use_column
        DECLARE result_row record;source_preference public.member_notification_preferences%ROWTYPE;
          target_preference public.member_notification_preferences%ROWTYPE;
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member merge authority denied'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('member-assets:'||requested_tenant_id::text||':'||
            least(requested_source_id,requested_target_id)::text,0));
          PERFORM pg_advisory_xact_lock(hashtextextended('member-assets:'||requested_tenant_id::text||':'||
            greatest(requested_source_id,requested_target_id)::text,0));
          PERFORM 1 FROM public.member_coupons WHERE tenant_id=requested_tenant_id
            AND membership_id IN (requested_source_id,requested_target_id) ORDER BY id FOR UPDATE;
          IF EXISTS(SELECT 1 FROM public.member_coupons WHERE tenant_id=requested_tenant_id
            AND membership_id IN (requested_source_id,requested_target_id) AND status='reserved') THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='membership_merge_blocked_by_reserved_coupon'; END IF;
          IF EXISTS(SELECT 1 FROM public.commerce_member_references source_ref
            JOIN public.commerce_member_references target_ref ON target_ref.tenant_id=source_ref.tenant_id
              AND target_ref.connection_id=source_ref.connection_id
            WHERE source_ref.tenant_id=requested_tenant_id AND source_ref.membership_id=requested_source_id
              AND target_ref.membership_id=requested_target_id) THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='membership_commerce_reference_conflict'; END IF;
          SELECT * INTO result_row FROM public.merge_brand_memberships_authority_v1(
            requested_tenant_id,requested_source_id,requested_target_id,requested_current_consumer_id,
            requested_source_credential_id,requested_target_credential_id,requested_source_token_key,
            requested_target_token_key,requested_source_event_id,requested_target_event_id,
            requested_payload_hash,requested_reencrypted_credentials);
          IF NOT result_row.replayed THEN
            UPDATE public.member_coupons SET membership_id=requested_target_id,updated_at=statement_timestamp()
              WHERE tenant_id=requested_tenant_id AND membership_id=requested_source_id;
            UPDATE public.member_channel_grants SET membership_id=requested_target_id
              WHERE tenant_id=requested_tenant_id AND membership_id=requested_source_id;
            UPDATE public.member_notifications SET membership_id=requested_target_id
              WHERE tenant_id=requested_tenant_id AND membership_id=requested_source_id;
            SELECT * INTO source_preference FROM public.member_notification_preferences
              WHERE tenant_id=requested_tenant_id AND membership_id=requested_source_id FOR UPDATE;
            SELECT * INTO target_preference FROM public.member_notification_preferences
              WHERE tenant_id=requested_tenant_id AND membership_id=requested_target_id FOR UPDATE;
            IF source_preference.id IS NOT NULL AND target_preference.id IS NOT NULL THEN
              UPDATE public.member_notification_preferences SET
                marketing_enabled=source_preference.marketing_enabled AND target_preference.marketing_enabled,
                service_wechat_enabled=source_preference.service_wechat_enabled AND target_preference.service_wechat_enabled,
                critical_sms_enabled=source_preference.critical_sms_enabled AND target_preference.critical_sms_enabled,
                marketing_opted_out_at=CASE WHEN source_preference.marketing_enabled AND target_preference.marketing_enabled
                  THEN NULL ELSE statement_timestamp() END,
                version=greatest(source_preference.version,target_preference.version)+1,updated_at=statement_timestamp()
                WHERE tenant_id=requested_tenant_id AND id=target_preference.id;
              DELETE FROM public.member_notification_preferences
                WHERE tenant_id=requested_tenant_id AND id=source_preference.id;
            ELSIF source_preference.id IS NOT NULL THEN
              UPDATE public.member_notification_preferences SET membership_id=requested_target_id,version=version+1,
                updated_at=statement_timestamp() WHERE tenant_id=requested_tenant_id AND id=source_preference.id;
            END IF;
            UPDATE public.commerce_member_references SET membership_id=requested_target_id,updated_at=statement_timestamp()
              WHERE tenant_id=requested_tenant_id AND membership_id=requested_source_id;
            UPDATE public.commerce_order_facts SET membership_id=requested_target_id,updated_at=statement_timestamp()
              WHERE tenant_id=requested_tenant_id AND membership_id=requested_source_id;
            UPDATE public.commerce_repurchase_attributions SET membership_id=requested_target_id
              WHERE tenant_id=requested_tenant_id AND membership_id=requested_source_id;
            UPDATE public.privacy_rights_requests SET membership_id=requested_target_id,updated_at=statement_timestamp()
              WHERE tenant_id=requested_tenant_id AND membership_id=requested_source_id;
          END IF;
          RETURN QUERY SELECT result_row.membership_id,result_row.membership_number,result_row.consumer_id,
            result_row.status,result_row.joined_at,result_row.replayed;
        END $f$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.merge_brand_memberships_authority_v1{signature} FROM PUBLIC,yimatong_app")
    op.execute(f"REVOKE ALL ON FUNCTION public.merge_brand_memberships_authority{signature} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.merge_brand_memberships_authority{signature} TO yimatong_app")


def _replace_marketing_authority() -> None:
    op.execute(
        r"""CREATE OR REPLACE FUNCTION public.create_member_marketing_notification_authority(
          requested_tenant_id uuid,payload jsonb) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
          SET search_path=pg_catalog,public AS $f$
        DECLARE notification_id uuid;grant_id uuid;preference public.member_notification_preferences%ROWTYPE;
          daily_count integer;weekly_count integer;delivery_status text;reason_value text;due_at timestamptz;
          server_now timestamptz:=statement_timestamp();
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
             OR payload->>'notification_type' NOT IN ('coupon_expiry','repurchase_invite','gift_coupon') THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member marketing notification authority denied'; END IF;
          SELECT * INTO preference FROM public.member_notification_preferences WHERE tenant_id=requested_tenant_id
            AND membership_id=(payload->>'membership_id')::uuid FOR UPDATE;
          IF NOT FOUND OR NOT preference.marketing_enabled OR NOT EXISTS(SELECT 1 FROM public.consent_records consent
            WHERE consent.tenant_id=requested_tenant_id AND consent.id=preference.marketing_consent_id
              AND consent.status='granted' AND consent.consent_type='marketing'
              AND consent.purpose IN ('marketing','lead_capture') AND consent.withdrawn_at IS NULL) THEN RETURN NULL; END IF;
          SELECT id INTO notification_id FROM public.member_notifications WHERE tenant_id=requested_tenant_id
            AND source_product=payload->>'source_product' AND source_event_id=payload->>'source_event_id'
            AND source_event_version=(payload->>'source_event_version')::integer
            AND notification_type=payload->>'notification_type';
          IF FOUND THEN RETURN notification_id; END IF;
          SELECT count(*) FILTER(WHERE (delivery.created_at AT TIME ZONE 'Asia/Shanghai')::date=
              (server_now AT TIME ZONE 'Asia/Shanghai')::date),
            count(*) FILTER(WHERE delivery.created_at>server_now-interval '7 days') INTO daily_count,weekly_count
          FROM public.member_notification_deliveries delivery JOIN public.member_notifications notification
            ON notification.tenant_id=delivery.tenant_id AND notification.id=delivery.notification_id
          WHERE notification.tenant_id=requested_tenant_id
            AND notification.membership_id=(payload->>'membership_id')::uuid
            AND notification.notification_class='marketing'
            AND delivery.status IN ('pending','delivering','accepted','failed','exhausted');
          notification_id:=(payload->>'notification_id')::uuid;
          INSERT INTO public.member_notifications(id,tenant_id,membership_id,notification_class,notification_type,
            source_product,source_event_id,source_event_version,object_ref,title,body,action_path,facts,occurred_at)
          VALUES(notification_id,requested_tenant_id,(payload->>'membership_id')::uuid,'marketing',
            payload->>'notification_type',payload->>'source_product',payload->>'source_event_id',
            (payload->>'source_event_version')::integer,payload->>'object_ref',payload->>'title',payload->>'body',
            NULLIF(payload->>'action_path',''),COALESCE(payload->'facts','{}'),(payload->>'occurred_at')::timestamptz);
          IF daily_count>=1 OR weekly_count>=3 THEN
            delivery_status:='suppressed';reason_value:='marketing_frequency_limited';
          ELSE
            SELECT id INTO grant_id FROM public.member_channel_grants WHERE tenant_id=requested_tenant_id
              AND membership_id=(payload->>'membership_id')::uuid AND template_code=payload->>'template_code'
              AND purpose='marketing' AND status='available' AND authorized_at<=server_now AND expires_at>server_now
              ORDER BY authorized_at LIMIT 1 FOR UPDATE;
            IF grant_id IS NULL THEN delivery_status:='authorization_missing';reason_value:='wechat_authorization_missing';
            ELSE delivery_status:='pending';due_at:=CASE
              WHEN extract(hour FROM server_now AT TIME ZONE 'Asia/Shanghai')>=21 THEN
                ((server_now AT TIME ZONE 'Asia/Shanghai')::date+interval '1 day 8 hours') AT TIME ZONE 'Asia/Shanghai'
              WHEN extract(hour FROM server_now AT TIME ZONE 'Asia/Shanghai')<8 THEN
                ((server_now AT TIME ZONE 'Asia/Shanghai')::date+interval '8 hours') AT TIME ZONE 'Asia/Shanghai'
              ELSE server_now END; END IF;
          END IF;
          INSERT INTO public.member_notification_deliveries(id,tenant_id,notification_id,channel_grant_id,channel,
            template_code,template_version,status,suppression_reason,next_attempt_at,consent_snapshot)
          VALUES((payload->>'delivery_id')::uuid,requested_tenant_id,notification_id,grant_id,'wechat_subscription',
            payload->>'template_code',payload->>'template_version',delivery_status,reason_value,due_at,
            jsonb_build_object('marketing_consent_id',preference.marketing_consent_id,
              'preference_version',preference.version,'grant_id',grant_id));
          IF delivery_status='pending' THEN UPDATE public.member_channel_grants SET status='consumed',consumed_at=server_now
            WHERE tenant_id=requested_tenant_id AND id=grant_id; END IF;
          RETURN notification_id;
        END $f$"""
    )


def _restrict_delivery_results() -> None:
    op.execute(
        r"""CREATE FUNCTION public.lease_member_notification_delivery_authority(
          requested_tenant_id uuid,requested_delivery_id uuid,requested_lease_token uuid) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member notification delivery lease denied'; END IF;
          IF EXISTS(SELECT 1 FROM public.member_notification_deliveries delivery
            JOIN public.member_notifications notification ON notification.tenant_id=delivery.tenant_id
              AND notification.id=delivery.notification_id
            WHERE delivery.tenant_id=requested_tenant_id AND delivery.id=requested_delivery_id
              AND delivery.next_attempt_at<=statement_timestamp()
              AND (delivery.status IN ('pending','failed')
                OR (delivery.status='delivering' AND delivery.lease_expires_at<=statement_timestamp()))
              AND notification.notification_class='marketing'
              AND NOT EXISTS(SELECT 1 FROM public.member_notification_preferences preference
                JOIN public.consent_records consent ON consent.tenant_id=preference.tenant_id
                  AND consent.id=preference.marketing_consent_id
                WHERE preference.tenant_id=notification.tenant_id
                  AND preference.membership_id=notification.membership_id
                  AND preference.marketing_enabled AND preference.marketing_opted_out_at IS NULL
                  AND consent.status='granted' AND consent.consent_type='marketing'
                  AND consent.purpose IN ('marketing','lead_capture') AND consent.withdrawn_at IS NULL)) THEN
            UPDATE public.member_notification_deliveries SET status='suppressed',
              suppression_reason='marketing_consent_unavailable',next_attempt_at=NULL,
              lease_token=NULL,lease_expires_at=NULL,updated_at=statement_timestamp()
              WHERE tenant_id=requested_tenant_id AND id=requested_delivery_id;
            RETURN false;
          END IF;
          UPDATE public.member_notification_deliveries SET status='delivering',lease_token=requested_lease_token,
            lease_expires_at=statement_timestamp()+interval '30 seconds',updated_at=statement_timestamp()
          WHERE tenant_id=requested_tenant_id AND id=requested_delivery_id
            AND next_attempt_at<=statement_timestamp()
            AND (status IN ('pending','failed') OR (status='delivering' AND lease_expires_at<=statement_timestamp()));
          RETURN FOUND;
        END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.lease_member_notification_delivery_authority(uuid,uuid,uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.lease_member_notification_delivery_authority(uuid,uuid,uuid) TO yimatong_app")
    op.execute(
        r"""CREATE OR REPLACE FUNCTION public.mutate_member_notification_delivery_authority(
          requested_tenant_id uuid,payload jsonb) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
          SET search_path=pg_catalog,public AS $f$
        DECLARE delivery public.member_notification_deliveries%ROWTYPE;result_value text:=payload->>'result';next_attempt integer;
        BEGIN
          IF session_user<>'yimatong_callback' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member notification delivery authority denied'; END IF;
          IF (result_value='accepted' AND (NULLIF(payload->>'channel_message_ref','') IS NULL
                OR NULLIF(payload->>'error_code','') IS NOT NULL))
             OR (result_value IN ('transient_failure','permanent_failure')
                AND (NULLIF(payload->>'error_code','') IS NULL
                  OR NULLIF(payload->>'channel_message_ref','') IS NOT NULL)) THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='notification delivery evidence invalid'; END IF;
          SELECT * INTO delivery FROM public.member_notification_deliveries WHERE tenant_id=requested_tenant_id
            AND id=(payload->>'delivery_id')::uuid FOR UPDATE;
          IF NOT FOUND OR delivery.status<>'delivering' OR delivery.lease_token IS DISTINCT FROM (payload->>'lease_token')::uuid
             OR delivery.lease_expires_at<=statement_timestamp() THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='notification delivery not attemptable'; END IF;
          next_attempt:=delivery.attempt_count+1;
          IF result_value='accepted' THEN UPDATE public.member_notification_deliveries SET status='accepted',
            attempt_count=next_attempt,accepted_at=statement_timestamp(),next_attempt_at=NULL,
            channel_message_ref=payload->>'channel_message_ref',last_error_code=NULL,lease_token=NULL,lease_expires_at=NULL,
            updated_at=statement_timestamp()
            WHERE tenant_id=requested_tenant_id AND id=delivery.id;
          ELSIF result_value='permanent_failure' OR next_attempt>=3 THEN UPDATE public.member_notification_deliveries
            SET status='exhausted',attempt_count=next_attempt,exhausted_at=statement_timestamp(),next_attempt_at=NULL,
              last_error_code=payload->>'error_code',lease_token=NULL,lease_expires_at=NULL,updated_at=statement_timestamp()
            WHERE tenant_id=requested_tenant_id AND id=delivery.id;
          ELSIF result_value='transient_failure' THEN UPDATE public.member_notification_deliveries SET status='failed',
            attempt_count=next_attempt,next_attempt_at=statement_timestamp()+(interval '5 minutes'*power(2,next_attempt-1)),
            last_error_code=payload->>'error_code',lease_token=NULL,lease_expires_at=NULL,updated_at=statement_timestamp()
            WHERE tenant_id=requested_tenant_id AND id=delivery.id;
          ELSE RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid notification delivery result'; END IF;
          RETURN delivery.id;
        END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.mutate_member_notification_delivery_authority(uuid,jsonb) FROM PUBLIC,yimatong_app")
    op.execute("GRANT EXECUTE ON FUNCTION public.mutate_member_notification_delivery_authority(uuid,jsonb) TO yimatong_callback")


def _cancel_withdrawn_marketing_deliveries() -> None:
    op.execute(
        r"""CREATE FUNCTION public.cancel_member_marketing_deliveries_for_preference() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        BEGIN
          IF OLD.marketing_enabled AND NOT NEW.marketing_enabled THEN
            UPDATE public.member_notification_deliveries delivery SET status='suppressed',
              suppression_reason='marketing_opted_out',next_attempt_at=NULL,lease_token=NULL,lease_expires_at=NULL,
              updated_at=statement_timestamp()
            FROM public.member_notifications notification WHERE notification.tenant_id=NEW.tenant_id
              AND notification.membership_id=NEW.membership_id AND notification.notification_class='marketing'
              AND delivery.tenant_id=notification.tenant_id AND delivery.notification_id=notification.id
              AND delivery.status IN ('pending','failed','delivering');
          END IF;
          RETURN NEW;
        END $f$"""
    )
    op.execute(
        "CREATE TRIGGER trg_cancel_member_marketing_on_opt_out "
        "AFTER UPDATE OF marketing_enabled ON public.member_notification_preferences FOR EACH ROW "
        "EXECUTE FUNCTION public.cancel_member_marketing_deliveries_for_preference()"
    )
    op.execute(
        r"""CREATE FUNCTION public.cancel_member_marketing_deliveries_for_consent() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        BEGIN
          IF OLD.status='granted' AND OLD.withdrawn_at IS NULL
             AND (NEW.status<>'granted' OR NEW.withdrawn_at IS NOT NULL) THEN
            UPDATE public.member_channel_grants grant_row SET status='revoked'
              FROM public.member_notification_preferences preference
              WHERE preference.tenant_id=NEW.tenant_id AND preference.marketing_consent_id=NEW.id
                AND grant_row.tenant_id=preference.tenant_id AND grant_row.membership_id=preference.membership_id
                AND grant_row.purpose='marketing' AND grant_row.status='available';
            UPDATE public.member_notification_deliveries delivery SET status='suppressed',
              suppression_reason='marketing_consent_withdrawn',next_attempt_at=NULL,
              lease_token=NULL,lease_expires_at=NULL,updated_at=statement_timestamp()
            FROM public.member_notifications notification,public.member_notification_preferences preference
            WHERE preference.tenant_id=NEW.tenant_id AND preference.marketing_consent_id=NEW.id
              AND notification.tenant_id=preference.tenant_id
              AND notification.membership_id=preference.membership_id
              AND notification.notification_class='marketing' AND delivery.tenant_id=notification.tenant_id
              AND delivery.notification_id=notification.id AND delivery.status IN ('pending','failed','delivering');
          END IF;
          RETURN NEW;
        END $f$"""
    )
    op.execute(
        "CREATE TRIGGER trg_cancel_member_marketing_on_consent_withdrawal "
        "AFTER UPDATE OF status,withdrawn_at ON public.consent_records FOR EACH ROW "
        "EXECUTE FUNCTION public.cancel_member_marketing_deliveries_for_consent()"
    )


def _restore_notification_authorities() -> None:
    op.execute(
        r"""CREATE OR REPLACE FUNCTION public.create_member_marketing_notification_authority(
          requested_tenant_id uuid,payload jsonb) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
          SET search_path=pg_catalog,public AS $f$
        DECLARE notification_id uuid;grant_id uuid;preference public.member_notification_preferences%ROWTYPE;
          daily_count integer;weekly_count integer;delivery_status text;reason_value text;due_at timestamptz;
          occurred timestamptz:=(payload->>'occurred_at')::timestamptz;
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
             OR payload->>'notification_type' NOT IN ('coupon_expiry','repurchase_invite','gift_coupon') THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member marketing notification authority denied'; END IF;
          SELECT * INTO preference FROM public.member_notification_preferences WHERE tenant_id=requested_tenant_id
            AND membership_id=(payload->>'membership_id')::uuid FOR UPDATE;
          IF NOT FOUND OR NOT preference.marketing_enabled OR NOT EXISTS(SELECT 1 FROM public.consent_records consent
            WHERE consent.tenant_id=requested_tenant_id AND consent.id=preference.marketing_consent_id
              AND consent.status='granted' AND consent.consent_type='marketing'
              AND consent.purpose IN ('marketing','lead_capture') AND consent.withdrawn_at IS NULL) THEN RETURN NULL; END IF;
          SELECT id INTO notification_id FROM public.member_notifications WHERE tenant_id=requested_tenant_id
            AND source_product=payload->>'source_product' AND source_event_id=payload->>'source_event_id'
            AND source_event_version=(payload->>'source_event_version')::integer
            AND notification_type=payload->>'notification_type';
          IF FOUND THEN RETURN notification_id; END IF;
          SELECT count(*) FILTER(WHERE (notification.occurred_at AT TIME ZONE 'Asia/Shanghai')::date=
              (occurred AT TIME ZONE 'Asia/Shanghai')::date),
            count(*) FILTER(WHERE notification.occurred_at>occurred-interval '7 days') INTO daily_count,weekly_count
          FROM public.member_notifications notification WHERE notification.tenant_id=requested_tenant_id
            AND notification.membership_id=(payload->>'membership_id')::uuid
            AND notification.notification_class='marketing';
          notification_id:=(payload->>'notification_id')::uuid;
          INSERT INTO public.member_notifications(id,tenant_id,membership_id,notification_class,notification_type,
            source_product,source_event_id,source_event_version,object_ref,title,body,action_path,facts,occurred_at)
          VALUES(notification_id,requested_tenant_id,(payload->>'membership_id')::uuid,'marketing',
            payload->>'notification_type',payload->>'source_product',payload->>'source_event_id',
            (payload->>'source_event_version')::integer,payload->>'object_ref',payload->>'title',payload->>'body',
            NULLIF(payload->>'action_path',''),COALESCE(payload->'facts','{}'::jsonb),occurred);
          IF daily_count>=1 OR weekly_count>=3 THEN
            delivery_status:='suppressed';reason_value:='marketing_frequency_limited';
          ELSE
            SELECT id INTO grant_id FROM public.member_channel_grants WHERE tenant_id=requested_tenant_id
              AND membership_id=(payload->>'membership_id')::uuid AND template_code=payload->>'template_code'
              AND purpose='marketing' AND status='available' AND expires_at>statement_timestamp()
              ORDER BY authorized_at LIMIT 1 FOR UPDATE;
            IF grant_id IS NULL THEN delivery_status:='authorization_missing';reason_value:='wechat_authorization_missing';
            ELSE delivery_status:='pending';due_at:=CASE
              WHEN extract(hour FROM statement_timestamp() AT TIME ZONE 'Asia/Shanghai')>=21 THEN
                ((statement_timestamp() AT TIME ZONE 'Asia/Shanghai')::date+interval '1 day 8 hours') AT TIME ZONE 'Asia/Shanghai'
              WHEN extract(hour FROM statement_timestamp() AT TIME ZONE 'Asia/Shanghai')<8 THEN
                ((statement_timestamp() AT TIME ZONE 'Asia/Shanghai')::date+interval '8 hours') AT TIME ZONE 'Asia/Shanghai'
              ELSE statement_timestamp() END; END IF;
          END IF;
          INSERT INTO public.member_notification_deliveries(id,tenant_id,notification_id,channel_grant_id,channel,
            template_code,template_version,status,suppression_reason,next_attempt_at,consent_snapshot)
          VALUES((payload->>'delivery_id')::uuid,requested_tenant_id,notification_id,grant_id,'wechat_subscription',
            payload->>'template_code',payload->>'template_version',delivery_status,reason_value,due_at,
            jsonb_build_object('marketing_consent_id',preference.marketing_consent_id,
              'preference_version',preference.version,'grant_id',grant_id));
          IF delivery_status='pending' THEN UPDATE public.member_channel_grants SET status='consumed',
            consumed_at=statement_timestamp() WHERE tenant_id=requested_tenant_id AND id=grant_id; END IF;
          RETURN notification_id;
        END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.create_member_marketing_notification_authority(uuid,jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.create_member_marketing_notification_authority(uuid,jsonb) TO yimatong_app")
    op.execute(
        r"""CREATE OR REPLACE FUNCTION public.mutate_member_notification_delivery_authority(
          requested_tenant_id uuid,payload jsonb) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
          SET search_path=pg_catalog,public AS $f$
        DECLARE delivery public.member_notification_deliveries%ROWTYPE;result_value text:=payload->>'result';next_attempt integer;
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='member notification delivery authority denied'; END IF;
          SELECT * INTO delivery FROM public.member_notification_deliveries WHERE tenant_id=requested_tenant_id
            AND id=(payload->>'delivery_id')::uuid FOR UPDATE;
          IF NOT FOUND OR delivery.status NOT IN ('pending','failed') THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='notification delivery not attemptable'; END IF;
          next_attempt:=delivery.attempt_count+1;
          IF result_value='accepted' THEN UPDATE public.member_notification_deliveries SET status='accepted',
            attempt_count=next_attempt,accepted_at=statement_timestamp(),next_attempt_at=NULL,
            channel_message_ref=payload->>'channel_message_ref',last_error_code=NULL,updated_at=statement_timestamp()
            WHERE tenant_id=requested_tenant_id AND id=delivery.id;
          ELSIF result_value='permanent_failure' OR next_attempt>=3 THEN UPDATE public.member_notification_deliveries
            SET status='exhausted',attempt_count=next_attempt,exhausted_at=statement_timestamp(),next_attempt_at=NULL,
              last_error_code=payload->>'error_code',updated_at=statement_timestamp()
            WHERE tenant_id=requested_tenant_id AND id=delivery.id;
          ELSIF result_value='transient_failure' THEN UPDATE public.member_notification_deliveries SET status='failed',
            attempt_count=next_attempt,next_attempt_at=statement_timestamp()+(interval '5 minutes'*power(2,next_attempt-1)),
            last_error_code=payload->>'error_code',updated_at=statement_timestamp()
            WHERE tenant_id=requested_tenant_id AND id=delivery.id;
          ELSE RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid notification delivery result'; END IF;
          RETURN delivery.id;
        END $f$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.mutate_member_notification_delivery_authority(uuid,jsonb) FROM PUBLIC,yimatong_callback")
    op.execute("GRANT EXECUTE ON FUNCTION public.mutate_member_notification_delivery_authority(uuid,jsonb) TO yimatong_app")


def downgrade() -> None:
    op.execute(
        r"""DO $f$ BEGIN
          IF EXISTS(SELECT 1 FROM public.member_notification_deliveries WHERE status='delivering') THEN
            RAISE EXCEPTION 'notification deliveries are in flight; wait for leases before downgrade'; END IF;
          IF EXISTS(SELECT 1 FROM public.repurchase_coupon_events WHERE event_type='refund_recorded') THEN
            RAISE EXCEPTION 'partial refund receipts exist; archive before downgrade'; END IF;
          IF EXISTS(SELECT 1 FROM public.member_coupons source_coupon JOIN public.member_coupons target_coupon
            ON target_coupon.tenant_id=source_coupon.tenant_id AND target_coupon.membership_id=source_coupon.membership_id
              AND target_coupon.rule_version_id=source_coupon.rule_version_id AND target_coupon.id<>source_coupon.id
            WHERE source_coupon.status IN ('available','reserved') AND target_coupon.status IN ('available','reserved')) THEN
            RAISE EXCEPTION 'merged duplicate active coupon assets exist; cannot restore unique index'; END IF;
        END $f$"""
    )
    signature = "(uuid,uuid,uuid,uuid,uuid,uuid,text,text,uuid,uuid,text,jsonb)"
    op.execute(f"DROP FUNCTION public.merge_brand_memberships_authority{signature}")
    op.execute(f"ALTER FUNCTION public.merge_brand_memberships_authority_v1{signature} RENAME TO merge_brand_memberships_authority")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.merge_brand_memberships_authority{signature} TO yimatong_app")
    op.execute("DROP FUNCTION public.mutate_member_coupon_authority(uuid,text,jsonb)")
    op.execute("ALTER FUNCTION public.mutate_member_coupon_authority_v1(uuid,text,jsonb) RENAME TO mutate_member_coupon_authority")
    op.execute("GRANT EXECUTE ON FUNCTION public.mutate_member_coupon_authority(uuid,text,jsonb) TO yimatong_app")
    op.drop_constraint("ck_member_channel_grants_server_window", "member_channel_grants", type_="check")
    op.execute("DROP TRIGGER trg_cancel_member_marketing_on_consent_withdrawal ON public.consent_records")
    op.execute("DROP FUNCTION public.cancel_member_marketing_deliveries_for_consent()")
    op.execute("DROP TRIGGER trg_cancel_member_marketing_on_opt_out ON public.member_notification_preferences")
    op.execute("DROP FUNCTION public.cancel_member_marketing_deliveries_for_preference()")
    _restore_notification_authorities()
    op.execute("DROP FUNCTION public.lease_member_notification_delivery_authority(uuid,uuid,uuid)")
    op.drop_constraint("ck_member_notification_deliveries_lease", "member_notification_deliveries", type_="check")
    op.drop_constraint("ck_member_notification_deliveries_status", "member_notification_deliveries", type_="check")
    op.create_check_constraint(
        "ck_member_notification_deliveries_status",
        "member_notification_deliveries",
        "status IN ('authorization_missing','pending','accepted','failed','exhausted','suppressed')",
    )
    op.drop_column("member_notification_deliveries", "lease_expires_at")
    op.drop_column("member_notification_deliveries", "lease_token")
    op.drop_constraint("ck_coupon_events_type", "repurchase_coupon_events", type_="check")
    op.create_check_constraint(
        "ck_coupon_events_type",
        "repurchase_coupon_events",
        "event_type IN ('rule_created','rule_published','rule_paused','rule_resumed','rule_ended',"
        "'issued','reserved','committed','released','reversed','expired','revoked','store_redeemed',"
        "'external_sync_pending','external_sync_confirmed','external_sync_error')",
    )
    op.create_index(
        "uq_member_coupons_active_rule",
        "member_coupons",
        ["tenant_id", "membership_id", "rule_version_id"],
        unique=True,
        postgresql_where="status IN ('available','reserved')",
    )
