"""Finalize function-only campaign, benefit and claim authority.

Revision ID: u6a3d4e5f6a7
Revises: u6a2c3d4e5f6
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u6a3d4e5f6a7"
down_revision: str | None = "u6a2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_ROLE = "yimatong_app"
_PUBLIC_FUNCTIONS = (
    "create_campaign(uuid,uuid,uuid,uuid,uuid,text,text,timestamptz,timestamptz,jsonb,text)",
    "update_campaign(uuid,uuid,uuid,uuid,boolean,uuid,text,text,timestamptz,timestamptz,jsonb,text)",
    "transition_campaign(uuid,uuid,uuid,uuid,text)",
    "create_benefit(uuid,uuid,uuid,uuid,uuid,text,text,jsonb,integer,integer,uuid)",
    "update_benefit(uuid,uuid,uuid,uuid,text,text,jsonb,integer,integer,boolean,uuid,text)",
    "attach_benefit(uuid,uuid,uuid,uuid,uuid)",
    "detach_benefit(uuid,uuid,uuid,uuid,uuid)",
    "delete_campaign(uuid,uuid,uuid,uuid)",
    "delete_benefit(uuid,uuid,uuid,uuid)",
    "claim_campaign_benefit(uuid,uuid,uuid,uuid,uuid,text,text,text)",
    "lease_campaign_claim_outbox(uuid,text,integer,integer)",
    "record_campaign_claim_delivery_result(uuid,uuid,uuid,uuid,uuid,text,text,jsonb,integer)",
    "complete_campaign_claim_outbox(uuid,uuid,uuid)",
    "fail_campaign_claim_outbox(uuid,uuid,uuid,text,integer)",
    "redeem_campaign_benefit_claim(uuid,uuid,uuid,uuid)",
)
_INTERNAL_FUNCTIONS = (
    "authorize_campaign_actor(uuid,uuid,text)",
    "validate_campaign_benefit_config(text,jsonb)",
    "mutate_campaign_benefit(uuid,uuid,uuid,text,uuid,uuid,text,text,jsonb,integer,integer,boolean,uuid,text)",
)
_CALLBACK_FUNCTIONS = (
    "settle_campaign_claim_callback(uuid,uuid,uuid,uuid,uuid,text,text,jsonb)",
)


def _role_exists() -> bool:
    return bool(
        op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": _RUNTIME_ROLE}).scalar()
    )


def _callback_role_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_callback')"))
        .scalar()
    )


def _install_authorizer() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.authorize_campaign_actor(
            requested_tenant_id uuid, requested_auth_session_id uuid, requested_permission text
        ) RETURNS TABLE(actor_id uuid,principal_tenant_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE probed_tenant uuid;
        DECLARE authorization_id uuid;
        DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
            IF requested_permission NOT IN ('campaign:create','campaign:manage') THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='unknown campaign permission';
            END IF;
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='campaign tenant context mismatch';
            END IF;
            SELECT session.tenant_id INTO probed_tenant FROM public.auth_sessions AS session
            WHERE session.id=requested_auth_session_id;
            IF probed_tenant IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='campaign auth session is not live';
            END IF;
            PERFORM pg_advisory_xact_lock_shared(6434892150882653249);
            PERFORM tenant.id FROM public.tenants AS tenant
            WHERE tenant.id IN (probed_tenant,requested_tenant_id)
            ORDER BY tenant.id::text FOR SHARE;
            PERFORM pg_advisory_xact_lock_shared(
                hashtextextended('auth-session:'||requested_auth_session_id::text,0)
            );
            SELECT session.tenant_id,account.id INTO principal_tenant_id,actor_id
            FROM public.auth_sessions AS session
            JOIN public.accounts AS account
              ON account.tenant_id=session.tenant_id AND account.id=session.account_id
            JOIN public.tenants AS tenant ON tenant.id=session.tenant_id
            WHERE session.id=requested_auth_session_id
              AND session.revoked_at IS NULL AND session.expires_at>now_at
              AND session.auth_version=account.auth_version AND account.is_active
              AND tenant.status='active';
            IF actor_id IS NULL OR principal_tenant_id IS DISTINCT FROM probed_tenant THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='campaign auth session is not live';
            END IF;
            IF principal_tenant_id IS DISTINCT FROM requested_tenant_id THEN
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(principal_tenant_id::text||':'||requested_tenant_id::text,0)
                );
                SELECT auth.id INTO authorization_id
                FROM public.agency_authorizations AS auth
                JOIN public.tenants AS agency ON agency.id=auth.agency_tenant_id
                JOIN public.tenants AS client ON client.id=auth.client_tenant_id
                WHERE auth.agency_tenant_id=principal_tenant_id
                  AND auth.client_tenant_id=requested_tenant_id
                  AND auth.status='active'
                  AND (auth.expires_at IS NULL OR auth.expires_at>now_at)
                  AND auth.scope::jsonb ? 'campaigns'
                  AND agency.status='active' AND agency.tenant_type='agency'
                  AND client.status='active' AND client.tenant_type='brand'
                FOR SHARE OF auth;
                IF authorization_id IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='campaign actor lacks campaigns authorization';
                END IF;
            END IF;
            IF NOT EXISTS(
                SELECT 1 FROM public.account_roles AS ar
                JOIN public.role_permissions AS rp
                  ON rp.tenant_id=ar.tenant_id AND rp.role_id=ar.role_id
                JOIN public.permissions AS permission
                  ON permission.tenant_id=rp.tenant_id AND permission.id=rp.permission_id
                WHERE ar.tenant_id=principal_tenant_id AND ar.account_id=actor_id
                  AND permission.code=requested_permission
            ) THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='campaign actor lacks required permission';
            END IF;
            RETURN NEXT;
        END
        $function$
        """
    )


def _install_campaign_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.create_campaign(
            requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
            requested_campaign_id uuid,requested_product_id uuid,requested_name text,requested_type text,
            requested_start_at timestamptz,requested_end_at timestamptz,requested_rules jsonb,
            requested_description text
        ) RETURNS TABLE(campaign_id uuid,current_status text,published_at timestamptz,updated_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor uuid; DECLARE row_value record; DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
            IF requested_campaign_id IS NULL OR requested_audit_id IS NULL
               OR NULLIF(trim(requested_name),'') IS NULL OR length(requested_name)>200
               OR NULLIF(trim(requested_type),'') IS NULL OR length(requested_type)>50
               OR requested_start_at IS NULL OR requested_end_at<=requested_start_at
               OR requested_rules IS NULL OR jsonb_typeof(requested_rules)<>'object'
               OR length(COALESCE(requested_description,''))>500 THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='campaign create parameters are invalid';
            END IF;
            SELECT authorized.actor_id INTO actor FROM public.authorize_campaign_actor(
                requested_tenant_id,requested_auth_session_id,'campaign:create'
            ) AS authorized;
            IF NOT pg_try_advisory_xact_lock(hashtextextended(
                'campaign:'||requested_tenant_id::text||':'||requested_campaign_id::text,0
            )) THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='campaign authority is busy'; END IF;
            IF requested_product_id IS NOT NULL THEN
                PERFORM product.id FROM public.products AS product
                WHERE product.tenant_id=requested_tenant_id AND product.id=requested_product_id FOR SHARE NOWAIT;
                IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='campaign product unavailable'; END IF;
                requested_rules:=jsonb_set(requested_rules,'{product_id}',to_jsonb(requested_product_id::text),true);
            ELSE requested_rules:=requested_rules-'product_id'; END IF;
            INSERT INTO public.campaigns(
                id,tenant_id,name,campaign_type,status,product_id,start_at,end_at,rules_json,
                description,published_at,created_at,updated_at
            ) VALUES(
                requested_campaign_id,requested_tenant_id,trim(requested_name),trim(requested_type),'draft',
                requested_product_id,requested_start_at,requested_end_at,requested_rules,
                NULLIF(trim(requested_description),''),NULL,now_at,now_at
            ) RETURNING * INTO row_value;
            INSERT INTO public.platform_audit_log(
                id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
            ) VALUES(
                requested_audit_id,actor::text,requested_tenant_id::text,'campaign_created',
                'campaign:'||requested_campaign_id::text,
                jsonb_build_object('after',jsonb_build_object('status','draft','product_id',requested_product_id)),
                now_at,now_at,now_at
            );
            campaign_id:=row_value.id; current_status:=row_value.status;
            published_at:=row_value.published_at; updated_at:=row_value.updated_at; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='campaign authority is busy';
        END
        $function$
        """
    )


def _install_benefit_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.validate_campaign_benefit_config(requested_type text,requested_config jsonb)
        RETURNS void LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE validity text; DECLARE amount_type text;
        BEGIN
            IF requested_type NOT IN (
                'platform_coupon','external_link','private_domain','form_benefit','cash_red_packet'
            ) OR requested_config IS NULL OR jsonb_typeof(requested_config)<>'object' THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='benefit type/config invalid';
            END IF;
            validity:=requested_config->>'validity_type';
            IF validity IS NOT NULL AND validity NOT IN ('campaign_period','after_claim_days','fixed_range') THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='benefit validity type invalid';
            END IF;
            IF validity='after_claim_days' AND COALESCE(requested_config->>'validity_days','') !~ '^[1-9][0-9]*$' THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='benefit validity days invalid';
            END IF;
            IF validity='fixed_range' THEN
                IF NULLIF(requested_config->>'validity_start_at','') IS NULL
                   OR NULLIF(requested_config->>'validity_end_at','') IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='benefit fixed validity timestamp invalid';
                END IF;
                BEGIN
                    IF (requested_config->>'validity_start_at')::timestamptz
                       >=(requested_config->>'validity_end_at')::timestamptz THEN
                        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='benefit fixed validity window invalid';
                    END IF;
                EXCEPTION WHEN invalid_datetime_format OR null_value_not_allowed THEN
                    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='benefit fixed validity timestamp invalid';
                END;
            END IF;
            IF requested_type='cash_red_packet' THEN
                amount_type:=requested_config->>'amount_type';
                IF amount_type NOT IN ('fixed','random','lucky')
                   OR COALESCE(requested_config->>'budget','') !~ '^[1-9][0-9]*$'
                   OR COALESCE(requested_config->>'claimed_budget','0') !~ '^[0-9]+$'
                   OR (requested_config->>'claimed_budget')::integer>(requested_config->>'budget')::integer THEN
                    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='cash benefit budget config invalid';
                END IF;
                IF amount_type='fixed' AND COALESCE(requested_config->>'fixed_amount','') !~ '^[1-9][0-9]*$' THEN
                    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='cash fixed amount invalid';
                ELSIF amount_type='random' AND (
                    COALESCE(requested_config->>'min_amount','') !~ '^[1-9][0-9]*$'
                    OR COALESCE(requested_config->>'max_amount','') !~ '^[1-9][0-9]*$'
                    OR (requested_config->>'max_amount')::integer<(requested_config->>'min_amount')::integer
                ) THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='cash random amount invalid';
                ELSIF amount_type='lucky' AND COALESCE(requested_config->>'lucky_min_per','') !~ '^[1-9][0-9]*$' THEN
                    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='cash lucky amount invalid';
                END IF;
            END IF;
        END
        $function$
        """
    )


def _install_delete_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.delete_campaign(
            requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
            requested_campaign_id uuid
        ) RETURNS TABLE(campaign_id uuid,deleted_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor uuid; DECLARE row_value record; DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
            IF requested_campaign_id IS NULL OR requested_audit_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='campaign delete parameters invalid';
            END IF;
            SELECT authorized.actor_id INTO actor FROM public.authorize_campaign_actor(
                requested_tenant_id,requested_auth_session_id,'campaign:manage'
            ) AS authorized;
            IF NOT pg_try_advisory_xact_lock(hashtextextended(
                'campaign:'||requested_tenant_id::text||':'||requested_campaign_id::text,0
            )) THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='campaign authority is busy'; END IF;
            SELECT * INTO row_value FROM public.campaigns AS campaign
            WHERE campaign.tenant_id=requested_tenant_id AND campaign.id=requested_campaign_id FOR UPDATE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='campaign unavailable'; END IF;
            IF row_value.status<>'draft' OR row_value.published_at IS NOT NULL OR EXISTS(
                SELECT 1 FROM public.benefits AS benefit
                WHERE benefit.tenant_id=requested_tenant_id AND benefit.campaign_id=requested_campaign_id
            ) OR EXISTS(
                SELECT 1 FROM public.benefit_claims AS claim
                WHERE claim.tenant_id=requested_tenant_id AND claim.campaign_id=requested_campaign_id
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='campaign with lifecycle facts cannot be deleted'; END IF;
            DELETE FROM public.campaigns AS mutable
            WHERE mutable.tenant_id=requested_tenant_id AND mutable.id=requested_campaign_id;
            INSERT INTO public.platform_audit_log(
                id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
            ) VALUES(requested_audit_id,actor::text,requested_tenant_id::text,'campaign_deleted',
                'campaign:'||requested_campaign_id::text,jsonb_build_object('name',row_value.name),
                now_at,now_at,now_at);
            campaign_id:=requested_campaign_id; deleted_at:=now_at; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='campaign authority is busy';
        END
        $function$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.delete_benefit(
            requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
            requested_benefit_id uuid
        ) RETURNS TABLE(benefit_id uuid,deleted_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor uuid; DECLARE row_value record; DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
            IF requested_benefit_id IS NULL OR requested_audit_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='benefit delete parameters invalid';
            END IF;
            SELECT authorized.actor_id INTO actor FROM public.authorize_campaign_actor(
                requested_tenant_id,requested_auth_session_id,'campaign:manage'
            ) AS authorized;
            IF NOT pg_try_advisory_xact_lock(hashtextextended(
                'benefit:'||requested_tenant_id::text||':'||requested_benefit_id::text,0
            )) THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='benefit authority is busy'; END IF;
            SELECT * INTO row_value FROM public.benefits AS benefit
            WHERE benefit.tenant_id=requested_tenant_id AND benefit.id=requested_benefit_id FOR UPDATE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='benefit unavailable'; END IF;
            IF row_value.campaign_id IS NOT NULL OR row_value.stock_used<>0 OR EXISTS(
                SELECT 1 FROM public.benefit_claims AS claim
                WHERE claim.tenant_id=requested_tenant_id AND claim.benefit_id=requested_benefit_id
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='benefit with relationship facts cannot be deleted'; END IF;
            DELETE FROM public.benefits AS mutable
            WHERE mutable.tenant_id=requested_tenant_id AND mutable.id=requested_benefit_id;
            INSERT INTO public.platform_audit_log(
                id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
            ) VALUES(requested_audit_id,actor::text,requested_tenant_id::text,'benefit_deleted',
                'benefit:'||requested_benefit_id::text,jsonb_build_object('name',row_value.name),
                now_at,now_at,now_at);
            benefit_id:=requested_benefit_id; deleted_at:=now_at; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='benefit authority is busy';
        END
        $function$
        """
    )


def _install_claim_function() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.claim_campaign_benefit(
            requested_tenant_id uuid,requested_claim_id uuid,requested_benefit_id uuid,
            requested_scan_event_id uuid,requested_scanned_product_id uuid,requested_public_id text,
            requested_consumer_id text,requested_idempotency_key text
        ) RETURNS TABLE(
            outcome text,claim_id uuid,campaign_id uuid,stock_used integer,outbox_id uuid,
            created boolean,reserved_amount integer,reservation_status text
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE benefit_row record; DECLARE campaign_row record; DECLARE existing_row record;
        DECLARE item_probe record; DECLARE item_row record; DECLARE batch_row record; DECLARE pb_row record;
        DECLARE event_row record; DECLARE amount integer; DECLARE budget integer; DECLARE claimed integer;
        DECLARE min_amount integer; DECLARE max_amount integer; DECLARE remaining_slots integer;
        DECLARE new_outbox_id uuid:=gen_random_uuid(); DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        DECLARE participation text; DECLARE campaign_limit integer;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
               OR requested_claim_id IS NULL OR requested_benefit_id IS NULL OR requested_scan_event_id IS NULL
               OR requested_scanned_product_id IS NULL OR NULLIF(trim(requested_public_id),'') IS NULL
               OR NULLIF(trim(requested_consumer_id),'') IS NULL OR length(requested_consumer_id)>100
               OR NULLIF(trim(requested_idempotency_key),'') IS NULL OR length(requested_idempotency_key)>100 THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='claim authority context invalid';
            END IF;
            SELECT * INTO existing_row FROM public.benefit_claims AS claim
            WHERE claim.tenant_id=requested_tenant_id AND claim.benefit_id=requested_benefit_id
              AND claim.consumer_id=requested_consumer_id AND claim.idempotency_key=requested_idempotency_key;
            IF FOUND THEN
                SELECT box.id INTO new_outbox_id FROM public.campaign_claim_outbox AS box
                WHERE box.tenant_id=requested_tenant_id AND box.claim_id=existing_row.id;
                outcome:='replayed'; claim_id:=existing_row.id; campaign_id:=existing_row.campaign_id;
                SELECT benefit.stock_used INTO stock_used FROM public.benefits AS benefit
                WHERE benefit.tenant_id=requested_tenant_id AND benefit.id=existing_row.benefit_id;
                outbox_id:=new_outbox_id; created:=false; reserved_amount:=existing_row.reserved_amount;
                reservation_status:=existing_row.reservation_status; RETURN NEXT; RETURN;
            END IF;
            SELECT item.id AS item_id,item.code_batch_id,batch.production_batch_id,
                   batch.product_id,batch.sku_id INTO item_probe
            FROM public.code_items AS item
            JOIN public.code_batches AS batch
              ON batch.tenant_id=item.tenant_id AND batch.id=item.code_batch_id
            WHERE item.tenant_id=requested_tenant_id AND item.public_id=requested_public_id;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='code identity unavailable'; END IF;
            SELECT benefit.campaign_id INTO campaign_id FROM public.benefits AS benefit
            WHERE benefit.tenant_id=requested_tenant_id AND benefit.id=requested_benefit_id;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='benefit unavailable'; END IF;
            PERFORM tenant.id FROM public.tenants AS tenant
            WHERE tenant.id=requested_tenant_id FOR SHARE NOWAIT;
            SELECT * INTO pb_row FROM public.production_batches AS pb
            WHERE pb.tenant_id=requested_tenant_id AND pb.id=item_probe.production_batch_id
              AND pb.product_id=item_probe.product_id AND pb.sku_id=item_probe.sku_id FOR SHARE NOWAIT;
            IF NOT FOUND OR pb_row.status<>'active'
               OR (now_at AT TIME ZONE 'Asia/Shanghai')::date<pb_row.production_date
               OR (now_at AT TIME ZONE 'Asia/Shanghai')::date>pb_row.expiry_date THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='production batch is not claimable';
            END IF;
            SELECT * INTO batch_row FROM public.code_batches AS batch
            WHERE batch.tenant_id=requested_tenant_id AND batch.id=item_probe.code_batch_id
              AND batch.production_batch_id=item_probe.production_batch_id
              AND batch.product_id=item_probe.product_id AND batch.sku_id=item_probe.sku_id FOR SHARE NOWAIT;
            IF NOT FOUND OR batch_row.status<>'activated'
               OR batch_row.product_id IS DISTINCT FROM requested_scanned_product_id THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='code batch is not claimable';
            END IF;
            IF campaign_id IS NOT NULL AND NOT pg_try_advisory_xact_lock(hashtextextended(
                'campaign:'||requested_tenant_id::text||':'||campaign_id::text,0
            )) THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='campaign authority is busy'; END IF;
            IF NOT pg_try_advisory_xact_lock(hashtextextended(
                'benefit:'||requested_tenant_id::text||':'||requested_benefit_id::text,0
            )) THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='benefit authority is busy'; END IF;
            IF campaign_id IS NOT NULL THEN
                SELECT * INTO campaign_row FROM public.campaigns AS campaign
                WHERE campaign.tenant_id=requested_tenant_id AND campaign.id=campaign_id FOR SHARE NOWAIT;
                IF NOT FOUND OR campaign_row.status<>'active' OR campaign_row.product_id IS DISTINCT FROM requested_scanned_product_id
                   OR now_at<campaign_row.start_at OR now_at>=campaign_row.end_at THEN
                    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='campaign is not claimable';
                END IF;
            END IF;
            SELECT * INTO benefit_row FROM public.benefits AS benefit
            WHERE benefit.tenant_id=requested_tenant_id AND benefit.id=requested_benefit_id FOR UPDATE NOWAIT;
            IF NOT FOUND OR benefit_row.campaign_id IS DISTINCT FROM campaign_id THEN
                RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='benefit relationship changed';
            END IF;
            SELECT * INTO existing_row FROM public.benefit_claims AS claim
            WHERE claim.tenant_id=requested_tenant_id AND claim.benefit_id=requested_benefit_id
              AND claim.consumer_id=requested_consumer_id AND claim.idempotency_key=requested_idempotency_key;
            IF FOUND THEN
                SELECT box.id INTO new_outbox_id FROM public.campaign_claim_outbox AS box
                WHERE box.tenant_id=requested_tenant_id AND box.claim_id=existing_row.id;
                outcome:='replayed'; claim_id:=existing_row.id; campaign_id:=existing_row.campaign_id;
                stock_used:=benefit_row.stock_used; outbox_id:=new_outbox_id; created:=false;
                reserved_amount:=existing_row.reserved_amount;
                reservation_status:=existing_row.reservation_status; RETURN NEXT; RETURN;
            END IF;
            IF benefit_row.status<>'active' OR benefit_row.stock_used>=benefit_row.stock_total THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='benefit stock unavailable';
            END IF;
            IF benefit_row.campaign_id IS NULL AND benefit_row.config_json->>'validity_type'='campaign_period' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='standalone campaign-period benefit is not claimable';
            END IF;
            IF benefit_row.config_json->>'validity_type'='fixed_range' AND NOT (
                now_at >= (benefit_row.config_json->>'validity_start_at')::timestamptz
                AND now_at < (benefit_row.config_json->>'validity_end_at')::timestamptz
            ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='benefit validity window is closed'; END IF;
            SELECT * INTO item_row FROM public.code_items AS item
            WHERE item.tenant_id=requested_tenant_id AND item.id=item_probe.item_id
              AND item.code_batch_id=item_probe.code_batch_id AND item.public_id=requested_public_id
            FOR SHARE NOWAIT;
            IF NOT FOUND OR item_row.status NOT IN ('activated','bound') THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='code item is not claimable';
            END IF;
            SELECT scan.is_first_scan,scan.is_valid_visit INTO event_row
            FROM public.scan_events AS scan
            WHERE scan.tenant_id=requested_tenant_id AND scan.id=requested_scan_event_id
              AND scan.public_id=requested_public_id AND scan.is_valid_visit
            ;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='scan evidence is not claimable'; END IF;
            PERFORM alert.id FROM public.risk_alerts AS alert
            WHERE alert.tenant_id=requested_tenant_id AND alert.public_id=requested_public_id
              AND NOT alert.resolved AND alert.risk_level IN ('medium','high') FOR SHARE;
            IF FOUND THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='active risk blocks benefit claim'; END IF;
            IF campaign_id IS NOT NULL THEN
                participation:=COALESCE(campaign_row.rules_json->>'participation_condition_type','any_scan');
                IF participation='first_scan' AND NOT event_row.is_first_scan THEN
                    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='first scan is required';
                ELSIF participation='member_only' AND NOT EXISTS(
                    SELECT 1 FROM public.consumer_profiles AS consumer
                    WHERE consumer.tenant_id=requested_tenant_id AND consumer.id::text=requested_consumer_id
                ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='member identity is required';
                ELSIF participation NOT IN ('any_scan','first_scan','member_only') THEN
                    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='campaign participation rule invalid';
                END IF;
                IF COALESCE(campaign_row.rules_json->>'claim_limit_count','1') !~ '^[1-9][0-9]*$'
                   OR length(COALESCE(campaign_row.rules_json->>'claim_limit_count','1'))>6 THEN
                    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='campaign consumer limit invalid';
                END IF;
                campaign_limit:=COALESCE((campaign_row.rules_json->>'claim_limit_count')::integer,1);
                IF campaign_limit>100000 THEN
                    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='campaign consumer limit invalid';
                END IF;
                IF (SELECT count(*) FROM public.benefit_claims AS claim
                    WHERE claim.tenant_id=requested_tenant_id AND claim.campaign_id=campaign_row.id
                      AND claim.consumer_id=requested_consumer_id
                      AND claim.status IN ('success','claimed','delivered','used'))>=campaign_limit THEN
                    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='campaign consumer limit reached';
                END IF;
            END IF;
            IF (SELECT count(*) FROM public.benefit_claims AS claim
                WHERE claim.tenant_id=requested_tenant_id AND claim.benefit_id=requested_benefit_id
                  AND claim.consumer_id=requested_consumer_id
                  AND claim.status IN ('success','claimed','delivered','used'))>=benefit_row.per_person_limit THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='benefit consumer limit reached';
            END IF;
            IF benefit_row.benefit_type='cash_red_packet' THEN
                PERFORM public.validate_campaign_benefit_config(
                    benefit_row.benefit_type::text,benefit_row.config_json::jsonb
                );
                budget:=(benefit_row.config_json->>'budget')::integer;
                claimed:=COALESCE((benefit_row.config_json->>'claimed_budget')::integer,0);
                IF benefit_row.config_json->>'amount_type'='fixed' THEN
                    amount:=(benefit_row.config_json->>'fixed_amount')::integer;
                ELSIF benefit_row.config_json->>'amount_type'='random' THEN
                    min_amount:=(benefit_row.config_json->>'min_amount')::integer;
                    max_amount:=(benefit_row.config_json->>'max_amount')::integer;
                    amount:=min_amount+mod(abs(hashtextextended(requested_claim_id::text,0)::numeric),
                        max_amount-min_amount+1)::integer;
                ELSE
                    min_amount:=(benefit_row.config_json->>'lucky_min_per')::integer;
                    remaining_slots:=benefit_row.stock_total-benefit_row.stock_used;
                    amount:=GREATEST(min_amount,(budget-claimed)/remaining_slots);
                END IF;
                IF amount<=0 OR claimed+amount>budget THEN
                    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='cash benefit budget unavailable';
                END IF;
                benefit_row.config_json:=jsonb_set(
                    benefit_row.config_json::jsonb,'{claimed_budget}',to_jsonb(claimed+amount),true
                );
                reservation_status:='reserved'; reserved_amount:=amount;
            ELSE
                amount:=NULL; reservation_status:='not_required'; reserved_amount:=NULL;
            END IF;
            UPDATE public.benefits AS mutable SET stock_used=mutable.stock_used+1,
                config_json=benefit_row.config_json,updated_at=now_at
            WHERE mutable.tenant_id=requested_tenant_id AND mutable.id=requested_benefit_id
            RETURNING mutable.stock_used INTO stock_used;
            INSERT INTO public.benefit_claims(
                id,tenant_id,benefit_id,campaign_id,consumer_id,idempotency_key,claim_type,status,
                delivery_status,reserved_amount,reservation_status,created_at,updated_at
            ) VALUES(requested_claim_id,requested_tenant_id,requested_benefit_id,campaign_id,
                requested_consumer_id,requested_idempotency_key,'claim','success','pending',
                amount,reservation_status,now_at,now_at);
            INSERT INTO public.campaign_claim_outbox(
                id,tenant_id,claim_id,event_type,payload,status,attempt_count,max_attempts,next_attempt_at,
                created_at,updated_at
            ) VALUES(new_outbox_id,requested_tenant_id,requested_claim_id,'claim_committed',
                jsonb_build_object('claim_id',requested_claim_id,'benefit_id',requested_benefit_id,
                    'campaign_id',campaign_id,'consumer_ref',requested_consumer_id,
                    'reserved_amount',amount),'pending',0,8,now_at,now_at,now_at);
            outcome:='success'; claim_id:=requested_claim_id; outbox_id:=new_outbox_id; created:=true; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='campaign claim authority is busy';
        END
        $function$
        """
    )


def _install_outbox_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.lease_campaign_claim_outbox(
            requested_tenant_id uuid,requested_worker_id text,requested_limit integer,requested_lease_seconds integer
        ) RETURNS TABLE(outbox_id uuid,claim_id uuid,event_type text,payload jsonb,attempt_count integer,
            max_attempts integer,lease_token uuid,leased_until timestamptz,delivery_id uuid,
            external_id text,callback_timed_out boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
               OR NULLIF(trim(requested_worker_id),'') IS NULL OR length(requested_worker_id)>100
               OR requested_limit NOT BETWEEN 1 AND 100 OR requested_lease_seconds NOT BETWEEN 5 AND 3600 THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='outbox lease authority invalid';
            END IF;
            RETURN QUERY WITH candidate AS (
                SELECT box.id,box.status AS prior_status FROM public.campaign_claim_outbox AS box
                WHERE box.tenant_id=requested_tenant_id
                  AND ((box.status='pending' AND box.attempt_count<box.max_attempts
                        AND box.next_attempt_at<=CURRENT_TIMESTAMP)
                    OR (box.status='processing' AND box.attempt_count<box.max_attempts
                        AND box.leased_until<=CURRENT_TIMESTAMP)
                    OR (box.status='awaiting_callback' AND box.attempt_count<=box.max_attempts
                        AND box.next_attempt_at<=CURRENT_TIMESTAMP))
                ORDER BY box.next_attempt_at,box.id FOR UPDATE SKIP LOCKED LIMIT requested_limit
            ), mutable AS (
                UPDATE public.campaign_claim_outbox AS box SET status='processing',
                    attempt_count=box.attempt_count+CASE
                        WHEN candidate.prior_status='awaiting_callback' AND box.attempt_count>=box.max_attempts THEN 0
                        ELSE 1 END,lease_token=gen_random_uuid(),
                    last_lease_token=box.lease_token,leased_until=CURRENT_TIMESTAMP+make_interval(secs=>requested_lease_seconds),
                    worker_id=trim(requested_worker_id),updated_at=CURRENT_TIMESTAMP
                FROM candidate WHERE box.id=candidate.id AND box.tenant_id=requested_tenant_id
                RETURNING box.*,candidate.prior_status
            ) SELECT mutable.id,mutable.claim_id,mutable.event_type::text,mutable.payload::jsonb,mutable.attempt_count,
                mutable.max_attempts,mutable.lease_token,mutable.leased_until,delivery.id,delivery.external_id::text,
                mutable.prior_status='awaiting_callback'
            FROM mutable LEFT JOIN public.benefit_deliveries AS delivery
              ON delivery.tenant_id=requested_tenant_id AND delivery.campaign_outbox_id=mutable.id;
        END
        $function$
        """
    )


def _install_redeem_function() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.redeem_campaign_benefit_claim(
            requested_tenant_id uuid,requested_api_key_id uuid,requested_audit_id uuid,requested_claim_id uuid
        ) RETURNS TABLE(claim_id uuid,current_status text,updated_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE context_value text; DECLARE context_key uuid; DECLARE provided_token bytea;
        DECLARE expected_token bytea; DECLARE context_secret bytea; DECLARE context_payload text;
        DECLARE token_difference integer:=0; DECLARE token_offset integer; DECLARE row_value record;
        DECLARE now_at timestamptz:=CURRENT_TIMESTAMP; DECLARE prior_status text;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
               OR requested_api_key_id IS NULL OR requested_audit_id IS NULL OR requested_claim_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='coupon redeem authority context invalid';
            END IF;
            context_value:=NULLIF(current_setting('app.api_key_id',true),'');
            BEGIN
                context_key:=split_part(context_value,':',1)::uuid;
                provided_token:=decode(split_part(context_value,':',2),'hex');
            EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='coupon redeem request context invalid';
            END;
            SELECT secret.context_secret INTO context_secret
            FROM public.api_key_catalog_audit_context_secrets AS secret WHERE secret.singleton_id=1;
            context_payload:=requested_tenant_id::text||':'||requested_api_key_id::text||':'
                ||pg_current_xact_id()::text||':'||pg_backend_pid()::text;
            expected_token:=hmac(convert_to(context_payload,'UTF8'),context_secret,'sha256');
            IF context_key IS DISTINCT FROM requested_api_key_id
               OR octet_length(provided_token) IS DISTINCT FROM octet_length(expected_token) THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='coupon redeem credential is not request-bound';
            END IF;
            FOR token_offset IN 0..octet_length(expected_token)-1 LOOP
                token_difference:=token_difference
                    |(get_byte(provided_token,token_offset)#get_byte(expected_token,token_offset));
            END LOOP;
            IF token_difference<>0 OR NOT EXISTS(
                SELECT 1 FROM public.api_keys AS api_key
                JOIN public.tenants AS tenant ON tenant.id=api_key.tenant_id
                WHERE api_key.id=requested_api_key_id AND api_key.tenant_id=requested_tenant_id
                  AND NOT api_key.revoked AND api_key.revoked_at IS NULL
                  AND (api_key.expires_at IS NULL OR api_key.expires_at>now_at)
                  AND api_key.permissions::jsonb ? 'coupon:redeem'
                  AND tenant.status='active' AND tenant.tenant_type='brand'
            ) THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='coupon redeem credential is not live'; END IF;
            SELECT * INTO row_value FROM public.benefit_claims AS claim
            WHERE claim.tenant_id=requested_tenant_id AND claim.id=requested_claim_id FOR UPDATE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='coupon claim unavailable'; END IF;
            IF row_value.status='used' THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon claim is already used';
            ELSIF row_value.status NOT IN ('success','claimed','delivered') THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='coupon claim is not redeemable';
            END IF;
            prior_status:=row_value.status;
            UPDATE public.benefit_claims AS claim SET status='used',updated_at=now_at
            WHERE claim.tenant_id=requested_tenant_id AND claim.id=requested_claim_id RETURNING * INTO row_value;
            INSERT INTO public.platform_audit_log(
                id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
            ) VALUES(requested_audit_id,requested_api_key_id::text,requested_tenant_id::text,
                'coupon_redeemed','claim:'||requested_claim_id::text,
                jsonb_build_object('prior_status',prior_status,'status','used'),now_at,now_at,now_at);
            claim_id:=row_value.id; current_status:=row_value.status; updated_at:=row_value.updated_at; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='coupon claim authority is busy';
        END
        $function$
        """
    )


def _install_callback_function() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.settle_campaign_claim_callback(
            requested_tenant_id uuid,requested_audit_id uuid,requested_connector_id uuid,
            requested_delivery_id uuid,requested_claim_id uuid,requested_external_id text,
            requested_callback_status text,requested_external_data jsonb
        ) RETURNS TABLE(
            delivery_id uuid,claim_id uuid,outbox_id uuid,current_status text,replayed boolean,
            settled_at timestamptz
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE delivery_probe record; DECLARE delivery_row record; DECLARE claim_row record;
        DECLARE outbox_row record; DECLARE benefit_row record; DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        DECLARE stored_external_id text; DECLARE stored_callback_status text;
        BEGIN
            IF session_user<>'yimatong_callback'
               OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
               OR requested_audit_id IS NULL OR requested_connector_id IS NULL
               OR requested_delivery_id IS NULL OR requested_claim_id IS NULL
               OR NULLIF(trim(requested_external_id),'') IS NULL OR length(requested_external_id)>200
               OR requested_callback_status NOT IN ('success','failed')
               OR requested_external_data IS NULL OR jsonb_typeof(requested_external_data)<>'object' THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='connector callback authority invalid';
            END IF;
            SELECT delivery.claim_id,delivery.benefit_id,delivery.connector_id,
                delivery.campaign_outbox_id,delivery.external_id INTO delivery_probe
            FROM public.benefit_deliveries AS delivery
            WHERE delivery.tenant_id=requested_tenant_id AND delivery.id=requested_delivery_id;
            IF NOT FOUND OR delivery_probe.claim_id IS DISTINCT FROM requested_claim_id
               OR delivery_probe.connector_id IS DISTINCT FROM requested_connector_id THEN
                RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='connector delivery relationship unavailable';
            END IF;
            IF delivery_probe.campaign_outbox_id IS NULL
               OR delivery_probe.external_id IS DISTINCT FROM trim(requested_external_id) THEN
                RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='connector callback external identity mismatch';
            END IF;
            outbox_id:=delivery_probe.campaign_outbox_id;
            SELECT * INTO outbox_row FROM public.campaign_claim_outbox AS box
            WHERE box.tenant_id=requested_tenant_id AND box.id=outbox_id
              AND box.claim_id=requested_claim_id FOR UPDATE NOWAIT;
            IF NOT FOUND OR outbox_row.status NOT IN ('awaiting_callback','processing','delivered') THEN
                RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='connector callback outbox relationship unavailable';
            END IF;
            SELECT * INTO claim_row FROM public.benefit_claims AS claim
            WHERE claim.tenant_id=requested_tenant_id AND claim.id=requested_claim_id FOR UPDATE NOWAIT;
            IF NOT FOUND OR claim_row.benefit_id IS DISTINCT FROM delivery_probe.benefit_id THEN
                RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='connector claim relationship unavailable';
            END IF;
            SELECT * INTO benefit_row FROM public.benefits AS benefit
            WHERE benefit.tenant_id=requested_tenant_id AND benefit.id=claim_row.benefit_id
              AND benefit.connector_id=requested_connector_id FOR SHARE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='connector benefit relationship unavailable'; END IF;
            SELECT * INTO delivery_row FROM public.benefit_deliveries AS delivery
            WHERE delivery.tenant_id=requested_tenant_id AND delivery.id=requested_delivery_id
              AND delivery.claim_id=requested_claim_id AND delivery.benefit_id=claim_row.benefit_id
              AND delivery.connector_id=requested_connector_id AND delivery.consumer_id=claim_row.consumer_id
            FOR UPDATE NOWAIT;
            IF NOT FOUND OR delivery_row.campaign_outbox_id IS DISTINCT FROM outbox_id
               OR delivery_row.external_id IS DISTINCT FROM trim(requested_external_id)
               OR delivery_row.benefit_config->>'out_bill_no' IS DISTINCT FROM trim(requested_external_id) THEN
                RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='connector callback external identity mismatch';
            END IF;
            stored_external_id:=delivery_row.external_data->>'_callback_external_id';
            stored_callback_status:=delivery_row.external_data->>'_callback_status';
            IF stored_external_id IS NOT NULL THEN
                IF stored_external_id=requested_external_id AND stored_callback_status=requested_callback_status THEN
                    delivery_id:=delivery_row.id; claim_id:=claim_row.id; current_status:=delivery_row.status;
                    replayed:=true; settled_at:=COALESCE(outbox_row.delivered_at,delivery_row.updated_at); RETURN NEXT; RETURN;
                END IF;
                RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='conflicting connector callback replay';
            END IF;
            UPDATE public.benefit_deliveries AS delivery SET status=requested_callback_status,
                external_data=COALESCE(delivery.external_data::jsonb,'{}'::jsonb)||requested_external_data
                    ||jsonb_build_object('_callback_external_id',requested_external_id,
                        '_callback_status',requested_callback_status),
                next_retry_at=CASE WHEN requested_callback_status='success' THEN NULL ELSE now_at END,
                updated_at=now_at
            WHERE delivery.tenant_id=requested_tenant_id AND delivery.id=requested_delivery_id
            RETURNING * INTO delivery_row;
            IF requested_callback_status='success' THEN
                IF claim_row.status='failed' OR claim_row.reservation_status='refunded' THEN
                    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='failed or refunded claim cannot settle';
                END IF;
                UPDATE public.benefit_claims AS claim SET
                    status=CASE WHEN claim.status='claimed' THEN 'delivered' ELSE claim.status END,
                    delivery_status='success',
                    reservation_status=CASE WHEN claim.reservation_status='reserved' THEN 'settled'
                        ELSE claim.reservation_status END,updated_at=now_at
                WHERE claim.tenant_id=requested_tenant_id AND claim.id=requested_claim_id;
                UPDATE public.campaign_claim_outbox AS box SET status='delivered',delivered_at=now_at,
                    last_lease_token=COALESCE(box.lease_token,box.last_lease_token),lease_token=NULL,
                    leased_until=NULL,worker_id=NULL,updated_at=now_at
                WHERE box.tenant_id=requested_tenant_id AND box.id=outbox_id;
            ELSE
                UPDATE public.benefit_claims AS claim SET delivery_status='pending',updated_at=now_at
                WHERE claim.tenant_id=requested_tenant_id AND claim.id=requested_claim_id;
                UPDATE public.campaign_claim_outbox AS box SET status='pending',next_attempt_at=now_at,
                    last_error='connector_callback_failed',
                    last_lease_token=COALESCE(box.lease_token,box.last_lease_token),lease_token=NULL,
                    leased_until=NULL,worker_id=NULL,delivered_at=NULL,updated_at=now_at
                WHERE box.tenant_id=requested_tenant_id AND box.id=outbox_id;
            END IF;
            INSERT INTO public.platform_audit_log(
                id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
            ) VALUES(requested_audit_id,'connector:'||requested_connector_id::text,requested_tenant_id::text,
                'benefit_delivery_callback','claim:'||requested_claim_id::text,
                jsonb_build_object('delivery_id',requested_delivery_id,'outbox_id',outbox_id,
                    'external_id',requested_external_id,'status',requested_callback_status),
                now_at,now_at,now_at);
            delivery_id:=delivery_row.id; claim_id:=claim_row.id; current_status:=delivery_row.status;
            replayed:=false; settled_at:=now_at; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='connector callback authority is busy';
        END
        $function$
        """
    )
    signature = _CALLBACK_FUNCTIONS[0]
    op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
    if _role_exists():
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM yimatong_app")
    if _callback_role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO yimatong_callback")
    op.execute(
        r"""
        CREATE FUNCTION public.record_campaign_claim_delivery_result(
            requested_tenant_id uuid,requested_outbox_id uuid,requested_lease_token uuid,
            requested_delivery_id uuid,requested_connector_id uuid,requested_result_status text,
            requested_external_id text,requested_external_data jsonb,requested_callback_timeout_seconds integer
        ) RETURNS TABLE(
            outbox_id uuid,delivery_id uuid,current_status text,claim_delivery_status text,
            attempt_count integer,next_attempt_at timestamptz,delivered_at timestamptz
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE outbox_row record; DECLARE claim_row record; DECLARE benefit_row record;
        DECLARE delivery_row record; DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
               OR requested_outbox_id IS NULL OR requested_lease_token IS NULL
               OR requested_delivery_id IS NULL OR requested_connector_id IS NULL
               OR requested_result_status NOT IN ('success','pending')
               OR NULLIF(trim(requested_external_id),'') IS NULL OR length(requested_external_id)>200
               OR requested_external_data IS NULL OR jsonb_typeof(requested_external_data)<>'object'
               OR requested_callback_timeout_seconds NOT BETWEEN 30 AND 604800 THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='delivery result authority invalid';
            END IF;
            SELECT * INTO outbox_row FROM public.campaign_claim_outbox AS box
            WHERE box.tenant_id=requested_tenant_id AND box.id=requested_outbox_id
              AND box.status='processing' AND box.lease_token=requested_lease_token
              AND box.leased_until>now_at FOR UPDATE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='outbox lease is stale'; END IF;
            SELECT * INTO claim_row FROM public.benefit_claims AS claim
            WHERE claim.tenant_id=requested_tenant_id AND claim.id=outbox_row.claim_id FOR UPDATE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='delivery claim unavailable'; END IF;
            SELECT * INTO benefit_row FROM public.benefits AS benefit
            WHERE benefit.tenant_id=requested_tenant_id AND benefit.id=claim_row.benefit_id
              AND benefit.connector_id=requested_connector_id FOR SHARE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='delivery benefit unavailable'; END IF;
            PERFORM connector.id FROM public.connectors AS connector
            WHERE connector.tenant_id=requested_tenant_id AND connector.id=requested_connector_id
              AND connector.enabled FOR SHARE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='delivery connector unavailable'; END IF;
            SELECT * INTO delivery_row FROM public.benefit_deliveries AS delivery
            WHERE delivery.tenant_id=requested_tenant_id
              AND delivery.campaign_outbox_id=requested_outbox_id FOR UPDATE NOWAIT;
            IF FOUND THEN
                IF delivery_row.id IS DISTINCT FROM requested_delivery_id
                   OR delivery_row.claim_id IS DISTINCT FROM claim_row.id
                   OR delivery_row.benefit_id IS DISTINCT FROM benefit_row.id
                   OR delivery_row.connector_id IS DISTINCT FROM requested_connector_id
                   OR delivery_row.external_id IS DISTINCT FROM trim(requested_external_id) THEN
                    RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='conflicting provider delivery identity';
                END IF;
                UPDATE public.benefit_deliveries AS delivery SET
                    status=requested_result_status,external_data=COALESCE(delivery.external_data::jsonb,'{}'::jsonb)
                        ||requested_external_data||jsonb_build_object('_provider_external_id',trim(requested_external_id)),
                    next_retry_at=CASE WHEN requested_result_status='pending'
                        THEN now_at+make_interval(secs=>requested_callback_timeout_seconds) ELSE NULL END,
                    updated_at=now_at
                WHERE delivery.tenant_id=requested_tenant_id AND delivery.id=delivery_row.id
                RETURNING * INTO delivery_row;
            ELSE
                INSERT INTO public.benefit_deliveries(
                    id,tenant_id,connector_id,benefit_id,claim_id,campaign_outbox_id,consumer_id,
                    benefit_type,benefit_config,status,retry_count,max_retries,external_data,external_id,
                    next_retry_at,created_at,updated_at
                ) VALUES(
                    requested_delivery_id,requested_tenant_id,requested_connector_id,benefit_row.id,claim_row.id,
                    requested_outbox_id,claim_row.consumer_id,benefit_row.benefit_type,
                    jsonb_build_object('out_bill_no',trim(requested_external_id),
                        'idempotency_key',claim_row.id::text),requested_result_status,0,outbox_row.max_attempts,
                    requested_external_data||jsonb_build_object('_provider_external_id',trim(requested_external_id)),
                    trim(requested_external_id),CASE WHEN requested_result_status='pending'
                        THEN now_at+make_interval(secs=>requested_callback_timeout_seconds) ELSE NULL END,
                    now_at,now_at
                ) RETURNING * INTO delivery_row;
            END IF;
            IF requested_result_status='success' THEN
                UPDATE public.campaign_claim_outbox AS box SET status='delivered',delivered_at=now_at,
                    last_lease_token=box.lease_token,lease_token=NULL,leased_until=NULL,worker_id=NULL,
                    updated_at=now_at
                WHERE box.tenant_id=requested_tenant_id AND box.id=requested_outbox_id
                RETURNING * INTO outbox_row;
                UPDATE public.benefit_claims AS claim SET delivery_status='success',
                    reservation_status=CASE WHEN claim.reservation_status='reserved' THEN 'settled'
                        ELSE claim.reservation_status END,updated_at=now_at
                WHERE claim.tenant_id=requested_tenant_id AND claim.id=claim_row.id
                RETURNING claim.delivery_status INTO claim_delivery_status;
            ELSE
                UPDATE public.campaign_claim_outbox AS box SET status='awaiting_callback',
                    next_attempt_at=now_at+make_interval(secs=>requested_callback_timeout_seconds),
                    last_lease_token=box.lease_token,lease_token=NULL,leased_until=NULL,worker_id=NULL,
                    last_error=NULL,updated_at=now_at
                WHERE box.tenant_id=requested_tenant_id AND box.id=requested_outbox_id
                RETURNING * INTO outbox_row;
                UPDATE public.benefit_claims AS claim SET delivery_status='processing',updated_at=now_at
                WHERE claim.tenant_id=requested_tenant_id AND claim.id=claim_row.id
                RETURNING claim.delivery_status INTO claim_delivery_status;
            END IF;
            outbox_id:=outbox_row.id; delivery_id:=delivery_row.id; current_status:=outbox_row.status;
            attempt_count:=outbox_row.attempt_count; next_attempt_at:=outbox_row.next_attempt_at;
            delivered_at:=outbox_row.delivered_at; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='delivery result authority is busy';
        END
        $function$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.complete_campaign_claim_outbox(
            requested_tenant_id uuid,requested_outbox_id uuid,requested_lease_token uuid
        ) RETURNS TABLE(outbox_id uuid,current_status text,attempt_count integer,
            next_attempt_at timestamptz,delivered_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE row_value record;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='outbox tenant context mismatch';
            END IF;
            UPDATE public.campaign_claim_outbox AS box SET status='delivered',delivered_at=CURRENT_TIMESTAMP,
                last_lease_token=box.lease_token,lease_token=NULL,leased_until=NULL,worker_id=NULL,
                updated_at=CURRENT_TIMESTAMP
            WHERE box.tenant_id=requested_tenant_id AND box.id=requested_outbox_id
              AND box.status='processing' AND box.lease_token=requested_lease_token
              AND box.leased_until>CURRENT_TIMESTAMP RETURNING box.* INTO row_value;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='outbox lease is stale'; END IF;
            UPDATE public.benefit_claims AS claim SET delivery_status='success',
                reservation_status=CASE WHEN claim.reservation_status='reserved' THEN 'settled'
                    ELSE claim.reservation_status END,updated_at=CURRENT_TIMESTAMP
            WHERE claim.tenant_id=requested_tenant_id AND claim.id=row_value.claim_id;
            outbox_id:=row_value.id; current_status:=row_value.status; attempt_count:=row_value.attempt_count;
            next_attempt_at:=row_value.next_attempt_at; delivered_at:=row_value.delivered_at; RETURN NEXT;
        END
        $function$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.fail_campaign_claim_outbox(
            requested_tenant_id uuid,requested_outbox_id uuid,requested_lease_token uuid,
            requested_error text,requested_retry_seconds integer
        ) RETURNS TABLE(outbox_id uuid,current_status text,attempt_count integer,
            next_attempt_at timestamptz,delivered_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE row_value record; DECLARE claim_row record; DECLARE terminal boolean;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
               OR requested_retry_seconds NOT BETWEEN 0 AND 86400 THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='outbox failure authority invalid';
            END IF;
            SELECT * INTO row_value FROM public.campaign_claim_outbox AS box
            WHERE box.tenant_id=requested_tenant_id AND box.id=requested_outbox_id
              AND box.status='processing' AND box.lease_token=requested_lease_token
              AND box.leased_until>CURRENT_TIMESTAMP FOR UPDATE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='outbox lease is stale'; END IF;
            terminal:=row_value.attempt_count>=row_value.max_attempts;
            UPDATE public.campaign_claim_outbox AS box SET status=CASE WHEN terminal THEN 'dead_letter' ELSE 'pending' END,
                last_error=left(COALESCE(requested_error,'delivery failed'),4000),last_lease_token=box.lease_token,
                lease_token=NULL,leased_until=NULL,worker_id=NULL,
                next_attempt_at=CURRENT_TIMESTAMP+make_interval(secs=>requested_retry_seconds),updated_at=CURRENT_TIMESTAMP
            WHERE box.tenant_id=requested_tenant_id AND box.id=requested_outbox_id RETURNING box.* INTO row_value;
            SELECT * INTO claim_row FROM public.benefit_claims AS claim
            WHERE claim.tenant_id=requested_tenant_id AND claim.id=row_value.claim_id FOR UPDATE;
            IF terminal AND claim_row.reservation_status='reserved'
               AND claim_row.status IN ('success','claimed') THEN
                UPDATE public.benefits AS benefit SET stock_used=benefit.stock_used-1,
                    config_json=jsonb_set(benefit.config_json::jsonb,'{claimed_budget}',
                        to_jsonb((benefit.config_json->>'claimed_budget')::integer-claim_row.reserved_amount),true),
                    updated_at=CURRENT_TIMESTAMP
                WHERE benefit.tenant_id=requested_tenant_id AND benefit.id=claim_row.benefit_id;
                UPDATE public.benefit_claims AS claim SET status='failed',delivery_status='dead_letter',
                    reservation_status='refunded',updated_at=CURRENT_TIMESTAMP
                WHERE claim.tenant_id=requested_tenant_id AND claim.id=claim_row.id;
            ELSE
                UPDATE public.benefit_claims AS claim SET delivery_status=CASE WHEN terminal THEN 'dead_letter' ELSE 'pending' END,
                    status=CASE WHEN terminal AND claim.status IN ('success','claimed') THEN 'failed'
                        ELSE claim.status END,updated_at=CURRENT_TIMESTAMP
                WHERE claim.tenant_id=requested_tenant_id AND claim.id=claim_row.id;
            END IF;
            outbox_id:=row_value.id; current_status:=row_value.status; attempt_count:=row_value.attempt_count;
            next_attempt_at:=row_value.next_attempt_at; delivered_at:=row_value.delivered_at; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='outbox authority is busy';
        END
        $function$
        """
    )


def _cutover_runtime_acl() -> None:
    for signature in (*_PUBLIC_FUNCTIONS, *_INTERNAL_FUNCTIONS):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
    if not _role_exists():
        return
    op.execute(
        "REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON "
        "public.campaigns,public.benefits,public.benefit_claims,public.benefit_deliveries,"
        "public.campaign_claim_outbox FROM yimatong_app"
    )
    op.execute(
        "GRANT SELECT ON public.campaigns,public.benefits,public.benefit_claims,"
        "public.benefit_deliveries,public.campaign_claim_outbox TO yimatong_app"
    )
    for signature in _PUBLIC_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO yimatong_app")
    for signature in _INTERNAL_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM yimatong_app")


def _install_remaining_mutations() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.mutate_campaign_benefit(
            requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
            requested_action text,requested_benefit_id uuid,requested_campaign_id uuid,
            requested_name text,requested_type text,requested_config jsonb,requested_stock integer,
            requested_limit integer,requested_connector_present boolean,requested_connector_id uuid,
            requested_status text
        ) RETURNS TABLE(
            benefit_id uuid,campaign_id uuid,current_status text,stock_total integer,
            stock_used integer,updated_at timestamptz
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor uuid; DECLARE row_value record; DECLARE campaign_row record;
        DECLARE prior_campaign uuid; DECLARE claim_count bigint; DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        DECLARE audit_action text; DECLARE permission text;
        BEGIN
            IF requested_action NOT IN ('create','update','attach','detach')
               OR requested_benefit_id IS NULL OR requested_audit_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='benefit mutation parameters invalid';
            END IF;
            permission:=CASE WHEN requested_action='create' THEN 'campaign:create' ELSE 'campaign:manage' END;
            SELECT authorized.actor_id INTO actor FROM public.authorize_campaign_actor(
                requested_tenant_id,requested_auth_session_id,permission
            ) AS authorized;
            IF requested_action<>'create' THEN
                SELECT benefit.campaign_id INTO prior_campaign FROM public.benefits AS benefit
                WHERE benefit.tenant_id=requested_tenant_id AND benefit.id=requested_benefit_id;
                IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='benefit unavailable'; END IF;
            END IF;
            IF requested_action IN ('create','attach') THEN prior_campaign:=requested_campaign_id; END IF;
            IF prior_campaign IS NOT NULL AND NOT pg_try_advisory_xact_lock(hashtextextended(
                'campaign:'||requested_tenant_id::text||':'||prior_campaign::text,0
            )) THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='campaign authority is busy'; END IF;
            IF NOT pg_try_advisory_xact_lock(hashtextextended(
                'benefit:'||requested_tenant_id::text||':'||requested_benefit_id::text,0
            )) THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='benefit authority is busy'; END IF;
            IF prior_campaign IS NOT NULL THEN
                SELECT * INTO campaign_row FROM public.campaigns AS campaign
                WHERE campaign.tenant_id=requested_tenant_id AND campaign.id=prior_campaign FOR UPDATE NOWAIT;
                IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='campaign unavailable'; END IF;
                IF campaign_row.status NOT IN ('draft','paused') THEN
                    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='live or ended campaign benefits are immutable';
                END IF;
            END IF;
            IF requested_action='create' THEN
                IF NULLIF(trim(requested_name),'') IS NULL OR length(requested_name)>200
                   OR requested_stock IS NULL OR requested_stock<0 OR requested_limit IS NULL OR requested_limit<1
                   OR requested_status IS DISTINCT FROM 'active' THEN
                    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='benefit create parameters invalid';
                END IF;
                PERFORM public.validate_campaign_benefit_config(requested_type,requested_config);
                IF requested_connector_id IS NOT NULL THEN
                    PERFORM connector.id FROM public.connectors AS connector
                    WHERE connector.tenant_id=requested_tenant_id AND connector.id=requested_connector_id FOR SHARE NOWAIT;
                    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='benefit connector unavailable'; END IF;
                END IF;
                IF requested_type='cash_red_packet' AND requested_connector_id IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='cash benefit connector required';
                END IF;
                INSERT INTO public.benefits(
                    id,tenant_id,campaign_id,name,benefit_type,config_json,connector_id,
                    stock_total,stock_used,per_person_limit,status,created_at,updated_at
                ) VALUES(
                    requested_benefit_id,requested_tenant_id,requested_campaign_id,trim(requested_name),
                    requested_type,requested_config,requested_connector_id,requested_stock,0,
                    requested_limit,'active',now_at,now_at
                ) RETURNING * INTO row_value;
                audit_action:='benefit_created';
            ELSE
                SELECT * INTO row_value FROM public.benefits AS benefit
                WHERE benefit.tenant_id=requested_tenant_id AND benefit.id=requested_benefit_id FOR UPDATE NOWAIT;
                IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='benefit unavailable'; END IF;
                SELECT count(*) INTO claim_count FROM public.benefit_claims AS claim
                WHERE claim.tenant_id=requested_tenant_id AND claim.benefit_id=requested_benefit_id;
                IF requested_action='attach' THEN
                    IF requested_campaign_id IS NULL OR row_value.campaign_id IS NOT NULL OR claim_count>0 THEN
                        RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='benefit cannot be attached';
                    END IF;
                    UPDATE public.benefits AS mutable SET campaign_id=requested_campaign_id,updated_at=now_at
                    WHERE mutable.tenant_id=requested_tenant_id AND mutable.id=requested_benefit_id
                    RETURNING * INTO row_value; audit_action:='benefit_attached';
                ELSIF requested_action='detach' THEN
                    IF row_value.campaign_id IS DISTINCT FROM requested_campaign_id OR claim_count>0 THEN
                        RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='benefit cannot be detached';
                    END IF;
                    UPDATE public.benefits AS mutable SET campaign_id=NULL,updated_at=now_at
                    WHERE mutable.tenant_id=requested_tenant_id AND mutable.id=requested_benefit_id
                    RETURNING * INTO row_value; audit_action:='benefit_detached';
                ELSE
                    IF (requested_name IS NOT NULL AND (NULLIF(trim(requested_name),'') IS NULL
                        OR length(requested_name)>200)) OR (requested_limit IS NOT NULL AND requested_limit<1) THEN
                        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='benefit update parameters invalid';
                    END IF;
                    IF requested_stock IS NOT NULL AND requested_stock<row_value.stock_used THEN
                        RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='benefit stock cannot fall below used stock';
                    END IF;
                    IF requested_status IS NOT NULL AND requested_status NOT IN ('active','inactive') THEN
                        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='benefit status invalid';
                    END IF;
                    IF claim_count>0 AND (
                        requested_type IS NOT NULL OR requested_config IS NOT NULL OR requested_connector_present
                    ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='claimed benefit delivery contract is immutable'; END IF;
                    PERFORM public.validate_campaign_benefit_config(
                        COALESCE(requested_type,row_value.benefit_type),
                        COALESCE(requested_config,row_value.config_json::jsonb)
                    );
                    IF requested_connector_present AND requested_connector_id IS NOT NULL THEN
                        PERFORM connector.id FROM public.connectors AS connector
                        WHERE connector.tenant_id=requested_tenant_id AND connector.id=requested_connector_id FOR SHARE NOWAIT;
                        IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='benefit connector unavailable'; END IF;
                    END IF;
                    UPDATE public.benefits AS mutable SET
                        name=COALESCE(trim(requested_name),mutable.name),
                        benefit_type=COALESCE(requested_type,mutable.benefit_type),
                        config_json=COALESCE(requested_config,mutable.config_json::jsonb),
                        stock_total=COALESCE(requested_stock,mutable.stock_total),
                        per_person_limit=COALESCE(requested_limit,mutable.per_person_limit),
                        connector_id=CASE WHEN requested_connector_present THEN requested_connector_id
                            ELSE mutable.connector_id END,
                        status=COALESCE(requested_status,mutable.status),updated_at=now_at
                    WHERE mutable.tenant_id=requested_tenant_id AND mutable.id=requested_benefit_id
                    RETURNING * INTO row_value; audit_action:='benefit_updated';
                    IF row_value.benefit_type='cash_red_packet' AND row_value.connector_id IS NULL THEN
                        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='cash benefit connector required';
                    END IF;
                END IF;
            END IF;
            INSERT INTO public.platform_audit_log(
                id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
            ) VALUES(requested_audit_id,actor::text,requested_tenant_id::text,audit_action,
                'benefit:'||requested_benefit_id::text,
                jsonb_build_object('campaign_id',row_value.campaign_id,'status',row_value.status,
                    'stock_total',row_value.stock_total,'stock_used',row_value.stock_used),
                now_at,now_at,now_at);
            benefit_id:=row_value.id; campaign_id:=row_value.campaign_id; current_status:=row_value.status;
            stock_total:=row_value.stock_total; stock_used:=row_value.stock_used;
            updated_at:=row_value.updated_at; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='campaign benefit authority is busy';
        END
        $function$
        """
    )
    wrappers = (
        r"""
        CREATE FUNCTION public.create_benefit(
            uuid,uuid,uuid,uuid,uuid,text,text,jsonb,integer,integer,uuid
        ) RETURNS TABLE(benefit_id uuid,campaign_id uuid,current_status text,stock_total integer,
            stock_used integer,updated_at timestamptz)
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$ SELECT * FROM public.mutate_campaign_benefit(
            $1,$2,$3,'create',$4,$5,$6,$7,$8,$9,$10,true,$11,'active') $function$
        """,
        r"""
        CREATE FUNCTION public.update_benefit(
            uuid,uuid,uuid,uuid,text,text,jsonb,integer,integer,boolean,uuid,text
        ) RETURNS TABLE(benefit_id uuid,campaign_id uuid,current_status text,stock_total integer,
            stock_used integer,updated_at timestamptz)
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$ SELECT * FROM public.mutate_campaign_benefit(
            $1,$2,$3,'update',$4,NULL,$5,$6,$7,$8,$9,$10,$11,$12) $function$
        """,
        r"""
        CREATE FUNCTION public.attach_benefit(uuid,uuid,uuid,uuid,uuid)
        RETURNS TABLE(benefit_id uuid,campaign_id uuid,current_status text,stock_total integer,
            stock_used integer,updated_at timestamptz)
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$ SELECT * FROM public.mutate_campaign_benefit(
            $1,$2,$3,'attach',$4,$5,NULL,NULL,NULL,NULL,NULL,false,NULL,NULL) $function$
        """,
        r"""
        CREATE FUNCTION public.detach_benefit(uuid,uuid,uuid,uuid,uuid)
        RETURNS TABLE(benefit_id uuid,campaign_id uuid,current_status text,stock_total integer,
            stock_used integer,updated_at timestamptz)
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$ SELECT * FROM public.mutate_campaign_benefit(
            $1,$2,$3,'detach',$4,$5,NULL,NULL,NULL,NULL,NULL,false,NULL,NULL) $function$
        """,
    )
    for statement in wrappers:
        op.execute(statement)
    op.execute(
        r"""
        CREATE FUNCTION public.update_campaign(
            requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
            requested_campaign_id uuid,requested_product_present boolean,requested_product_id uuid,
            requested_name text,requested_type text,requested_start_at timestamptz,
            requested_end_at timestamptz,requested_rules jsonb,requested_description text
        ) RETURNS TABLE(campaign_id uuid,current_status text,published_at timestamptz,updated_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor uuid; DECLARE row_value record; DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        DECLARE next_product uuid; DECLARE next_start timestamptz; DECLARE next_end timestamptz;
        DECLARE next_rules jsonb;
        BEGIN
            IF requested_campaign_id IS NULL OR requested_audit_id IS NULL
               OR (requested_name IS NOT NULL AND (NULLIF(trim(requested_name),'') IS NULL OR length(requested_name)>200))
               OR (requested_type IS NOT NULL AND (NULLIF(trim(requested_type),'') IS NULL OR length(requested_type)>50))
               OR (requested_rules IS NOT NULL AND jsonb_typeof(requested_rules)<>'object')
               OR length(COALESCE(requested_description,''))>500 THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='campaign update parameters are invalid';
            END IF;
            SELECT authorized.actor_id INTO actor FROM public.authorize_campaign_actor(
                requested_tenant_id,requested_auth_session_id,'campaign:manage'
            ) AS authorized;
            IF NOT pg_try_advisory_xact_lock(hashtextextended(
                'campaign:'||requested_tenant_id::text||':'||requested_campaign_id::text,0
            )) THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='campaign authority is busy'; END IF;
            SELECT * INTO row_value FROM public.campaigns AS campaign
            WHERE campaign.tenant_id=requested_tenant_id AND campaign.id=requested_campaign_id
            FOR UPDATE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='campaign unavailable'; END IF;
            IF row_value.status NOT IN ('draft','paused') THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='live or ended campaign content is immutable';
            END IF;
            next_product:=CASE WHEN requested_product_present THEN requested_product_id ELSE row_value.product_id END;
            next_start:=COALESCE(requested_start_at,row_value.start_at);
            next_end:=COALESCE(requested_end_at,row_value.end_at);
            next_rules:=COALESCE(requested_rules,row_value.rules_json::jsonb);
            IF next_end<=next_start THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='campaign time window invalid'; END IF;
            IF next_product IS NOT NULL THEN
                PERFORM product.id FROM public.products AS product
                WHERE product.tenant_id=requested_tenant_id AND product.id=next_product FOR SHARE NOWAIT;
                IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='campaign product unavailable'; END IF;
                next_rules:=jsonb_set(next_rules,'{product_id}',to_jsonb(next_product::text),true);
            ELSE next_rules:=next_rules-'product_id'; END IF;
            UPDATE public.campaigns AS mutable SET
                product_id=next_product,name=COALESCE(trim(requested_name),mutable.name),
                campaign_type=COALESCE(trim(requested_type),mutable.campaign_type),start_at=next_start,end_at=next_end,
                rules_json=next_rules,
                description=CASE WHEN requested_description IS NULL THEN mutable.description
                    ELSE NULLIF(trim(requested_description),'') END,updated_at=now_at
            WHERE mutable.tenant_id=requested_tenant_id AND mutable.id=requested_campaign_id
            RETURNING * INTO row_value;
            INSERT INTO public.platform_audit_log(
                id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
            ) VALUES(requested_audit_id,actor::text,requested_tenant_id::text,'campaign_updated',
                'campaign:'||requested_campaign_id::text,jsonb_build_object('status',row_value.status),
                now_at,now_at,now_at);
            campaign_id:=row_value.id; current_status:=row_value.status;
            published_at:=row_value.published_at; updated_at:=row_value.updated_at; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='campaign authority is busy';
        END
        $function$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.transition_campaign(
            requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
            requested_campaign_id uuid,requested_status text
        ) RETURNS TABLE(campaign_id uuid,current_status text,published_at timestamptz,updated_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE actor uuid; DECLARE row_value record; DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        DECLARE event_name text;
        BEGIN
            IF requested_campaign_id IS NULL OR requested_audit_id IS NULL
               OR requested_status NOT IN ('active','paused','ended') THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='campaign transition parameters invalid';
            END IF;
            SELECT authorized.actor_id INTO actor FROM public.authorize_campaign_actor(
                requested_tenant_id,requested_auth_session_id,'campaign:manage'
            ) AS authorized;
            IF NOT pg_try_advisory_xact_lock(hashtextextended(
                'campaign:'||requested_tenant_id::text||':'||requested_campaign_id::text,0
            )) THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='campaign authority is busy'; END IF;
            SELECT * INTO row_value FROM public.campaigns AS campaign
            WHERE campaign.tenant_id=requested_tenant_id AND campaign.id=requested_campaign_id
            FOR UPDATE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='campaign unavailable'; END IF;
            IF NOT ((row_value.status='draft' AND requested_status='active')
                OR (row_value.status='active' AND requested_status IN ('paused','ended'))
                OR (row_value.status='paused' AND requested_status IN ('active','ended'))) THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='campaign lifecycle transition is invalid';
            END IF;
            IF requested_status='active' THEN
                IF row_value.product_id IS NULL OR row_value.end_at<=now_at OR NOT EXISTS(
                    SELECT 1 FROM public.benefits AS benefit
                    WHERE benefit.tenant_id=requested_tenant_id AND benefit.campaign_id=requested_campaign_id
                      AND benefit.status='active' AND benefit.stock_total>benefit.stock_used
                ) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='campaign activation requirements not met'; END IF;
            END IF;
            UPDATE public.campaigns AS mutable SET status=requested_status,
                published_at=CASE WHEN requested_status='active' THEN COALESCE(mutable.published_at,now_at)
                    ELSE mutable.published_at END,updated_at=now_at
            WHERE mutable.tenant_id=requested_tenant_id AND mutable.id=requested_campaign_id
            RETURNING * INTO row_value;
            INSERT INTO public.platform_audit_log(
                id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
            ) VALUES(requested_audit_id,actor::text,requested_tenant_id::text,'campaign_status_changed',
                'campaign:'||requested_campaign_id::text,
                jsonb_build_object('status',requested_status,'published_at',row_value.published_at),
                now_at,now_at,now_at);
            event_name:='campaign.'||requested_status;
            INSERT INTO public.webhook_deliveries(
                id,tenant_id,endpoint_id,event_id,event_type,payload,status,retry_count,
                next_retry_at,created_at,updated_at
            ) SELECT gen_random_uuid(),requested_tenant_id,endpoint.id,requested_audit_id::text,event_name,
                jsonb_build_object('campaign_id',requested_campaign_id,'status',requested_status,
                    'published_at',row_value.published_at),
                'pending',0,now_at,now_at,now_at
            FROM public.webhook_endpoints AS endpoint
            WHERE endpoint.tenant_id=requested_tenant_id AND endpoint.enabled
              AND endpoint.events::jsonb ? event_name;
            campaign_id:=row_value.id; current_status:=row_value.status;
            published_at:=row_value.published_at; updated_at:=row_value.updated_at; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='campaign authority is busy';
        END
        $function$
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _install_authorizer()
    _install_campaign_functions()
    _install_benefit_functions()
    _install_remaining_mutations()
    _install_delete_functions()
    _install_claim_function()
    _install_outbox_functions()
    _install_redeem_function()
    _install_callback_function()
    _cutover_runtime_acl()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM public.campaign_claim_outbox)")).scalar():
        raise RuntimeError(
            "u6a3 downgrade blocked: campaign claim delivery facts require the function authority; "
            "drain/export and explicitly reconcile them before downgrade"
        )
    for signature in reversed(_PUBLIC_FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
    for signature in reversed(_CALLBACK_FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
    for signature in reversed(_INTERNAL_FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
    if _role_exists():
        op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON public.campaigns,public.benefits TO yimatong_app")
        op.execute("GRANT SELECT,INSERT,UPDATE ON public.benefit_claims TO yimatong_app")
        op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON public.benefit_deliveries TO yimatong_app")
