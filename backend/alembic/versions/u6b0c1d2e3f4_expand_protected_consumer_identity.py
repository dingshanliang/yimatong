"""Expand protected consumer identity and install recall authority.

Revision ID: u6b0c1d2e3f4
Revises: u6a3d4e5f6a7
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u6b0c1d2e3f4"
down_revision: str | None = "u6a3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RECALL_SIGNATURE = "recall_production_batch(uuid,uuid,uuid,uuid,text)"
_BATCH_MUTATION_SIGNATURES = (
    "create_production_batch(uuid,uuid,uuid,uuid,uuid,uuid,text,date,date,text)",
    "update_production_batch(uuid,uuid,uuid,uuid,text,date,date,text,boolean)",
    "delete_production_batch(uuid,uuid,uuid,uuid)",
)


def _role_exists() -> bool:
    return bool(
        op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')")).scalar()
    )


def _install_code_item_guard(*, allow_authoritative_legacy_recall: bool) -> None:
    legacy_recall = (
        """
                IF TG_OP='UPDATE'
                   AND OLD.status::text IN ('activated','bound') AND NEW.status::text='frozen'
                   AND NEW.tenant_id IS NOT DISTINCT FROM OLD.tenant_id
                   AND NEW.code_batch_id IS NOT DISTINCT FROM OLD.code_batch_id
                   AND NEW.public_id IS NOT DISTINCT FROM OLD.public_id
                   AND NEW.code_type IS NOT DISTINCT FROM OLD.code_type
                   AND NEW.pair_id IS NOT DISTINCT FROM OLD.pair_id
                   AND NEW.frozen_from_status IS NOT DISTINCT FROM OLD.status::text
                   AND NEW.frozen_at IS NOT NULL AND NULLIF(trim(NEW.frozen_by),'') IS NOT NULL
                   AND NEW.freeze_provenance_version=1
                   AND EXISTS (
                       SELECT 1 FROM public.production_batches AS pb
                       WHERE pb.tenant_id=batch_row.tenant_id AND pb.id=batch_row.production_batch_id
                         AND pb.product_id=batch_row.product_id AND pb.sku_id=batch_row.sku_id
                         AND pb.status::text='recalled' AND pb.recalled_by=NEW.frozen_by
                         AND NEW.freeze_reason=left('production batch recall: '||pb.id::text,200)
                   ) THEN
                    RETURN NEW;
                END IF;
    """
        if allow_authoritative_legacy_recall
        else ""
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.guard_code_item_batch_contract()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE batch_row public.code_batches%ROWTYPE;
        DECLARE rollout_finalized boolean;
        DECLARE privileged_legacy boolean:=has_parameter_privilege(session_user,'app.bypass_rls','SET');
        BEGIN
            IF TG_OP='DELETE' THEN
                SELECT * INTO batch_row FROM public.code_batches
                WHERE tenant_id=OLD.tenant_id AND id=OLD.code_batch_id FOR UPDATE;
                IF batch_row.contract_version=0 AND privileged_legacy THEN RETURN OLD; END IF;
                RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='code item deletion is forbidden';
            END IF;
            SELECT * INTO batch_row FROM public.code_batches
            WHERE tenant_id=NEW.tenant_id AND id=NEW.code_batch_id FOR UPDATE NOWAIT;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='code item batch is unavailable';
            END IF;
            IF batch_row.contract_version=0 THEN
                SELECT EXISTS(SELECT 1 FROM public.code_delivery_contract_rollout_state
                    WHERE id=1 AND phase='finalized') INTO rollout_finalized;
                {legacy_recall}
                IF rollout_finalized AND NOT privileged_legacy THEN
                    RAISE EXCEPTION USING ERRCODE='42501',
                        MESSAGE='legacy code batch items are read-only after rollout finalization';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP='INSERT' THEN
                IF batch_row.status::text<>'generating' OR NEW.status::text<>'created' THEN
                    RAISE EXCEPTION USING ERRCODE='23514',
                        MESSAGE='code items may be created only in a generating batch';
                END IF;
                IF batch_row.code_type='paired' THEN
                    IF NEW.code_type NOT IN ('outer','inner') OR NEW.pair_id IS NULL THEN
                        RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='paired code item metadata is invalid';
                    END IF;
                ELSIF NEW.code_type<>'single' OR NEW.pair_id IS NOT NULL THEN
                    RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='single code item metadata is invalid';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.code_batch_id IS DISTINCT FROM OLD.code_batch_id
               OR NEW.public_id IS DISTINCT FROM OLD.public_id
               OR NEW.code_type IS DISTINCT FROM OLD.code_type
               OR NEW.pair_id IS DISTINCT FROM OLD.pair_id THEN
                RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='code item identity is immutable';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                (OLD.status::text='created' AND NEW.status::text IN ('activated','revoked'))
                OR (OLD.status::text='activated' AND NEW.status::text IN ('bound','revoked','frozen'))
                OR (OLD.status::text='bound' AND NEW.status::text IN ('expired','revoked','frozen'))
                OR (OLD.status::text='frozen' AND NEW.status::text IN ('activated','bound','revoked'))
            ) THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='invalid code item lifecycle transition';
            END IF;
            IF OLD.status::text='created' AND NEW.status::text='activated'
               AND batch_row.status::text<>'delivered' THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='code item activation requires a delivered code batch';
            END IF;
            IF OLD.status::text='activated' AND NEW.status::text='bound'
               AND batch_row.status::text<>'activated' THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='code item binding requires an activated code batch';
            END IF;
            IF OLD.status::text='frozen' AND NEW.status::text IN ('activated','bound')
               AND batch_row.status::text<>'activated' THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='code item recovery requires an activated code batch';
            END IF;
            RETURN NEW;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',
                MESSAGE='authoritative code batch is concurrently changing';
        END
        $function$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_code_item_batch_contract() FROM PUBLIC")


def _install_recall_authority() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.recall_production_batch(
            requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
            requested_production_batch_id uuid,requested_reason text
        ) RETURNS TABLE(
            production_batch_id uuid,prior_status text,current_status text,frozen_item_count integer,
            recalled_at timestamptz,actor_id uuid
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE batch_row record; DECLARE resolved_actor text; DECLARE resolved_principal_tenant uuid;
        DECLARE matched_permission_id uuid;
        DECLARE now_at timestamptz:=CURRENT_TIMESTAMP; DECLARE freeze_count integer;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
               OR requested_audit_id IS NULL OR requested_production_batch_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='production batch recall context invalid';
            END IF;
            IF NULLIF(trim(requested_reason),'') IS NULL OR length(requested_reason)>500 THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='bounded production batch recall reason is required';
            END IF;
            SELECT authority.actor_id,authority.principal_tenant_id
            INTO resolved_actor,resolved_principal_tenant
            FROM public.authorize_code_lifecycle_actor(
                requested_tenant_id,requested_auth_session_id
            ) AS authority;
            SELECT permission.id INTO matched_permission_id
                FROM public.account_roles AS account_role
                JOIN public.role_permissions AS role_permission
                  ON role_permission.tenant_id=account_role.tenant_id
                 AND role_permission.role_id=account_role.role_id
                JOIN public.permissions AS permission
                  ON permission.tenant_id=role_permission.tenant_id
                 AND permission.id=role_permission.permission_id
                WHERE account_role.tenant_id=resolved_principal_tenant
                  AND account_role.account_id=resolved_actor::uuid
                  AND permission.code='code:manage'
                ORDER BY permission.id::text
                LIMIT 1 FOR SHARE OF account_role,role_permission,permission;
            IF matched_permission_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='production batch recall permission denied';
            END IF;
            SELECT * INTO batch_row FROM public.production_batches AS batch
            WHERE batch.tenant_id=requested_tenant_id AND batch.id=requested_production_batch_id
            FOR UPDATE NOWAIT;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='production batch is unavailable';
            END IF;
            prior_status:=batch_row.status::text;
            IF prior_status<>'active'
               OR batch_row.expiry_date<(now_at AT TIME ZONE 'Asia/Shanghai')::date THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='only an active production batch may be recalled';
            END IF;
            SELECT count(*)::integer INTO freeze_count
            FROM public.code_batches AS code_batch
            JOIN public.code_items AS item
              ON item.tenant_id=code_batch.tenant_id AND item.code_batch_id=code_batch.id
            WHERE code_batch.tenant_id=requested_tenant_id
              AND code_batch.product_id=batch_row.product_id AND code_batch.sku_id=batch_row.sku_id
              AND code_batch.production_batch_id=batch_row.id
              AND item.status::text IN ('activated','bound');
            UPDATE public.production_batches AS batch SET status='recalled',
                recall_reason=trim(requested_reason),recalled_at=now_at,recalled_by=resolved_actor,
                updated_at=now_at
            WHERE batch.tenant_id=requested_tenant_id AND batch.id=requested_production_batch_id;
            INSERT INTO public.platform_audit_log(
                id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
            ) VALUES(
                requested_audit_id,resolved_actor,requested_tenant_id::text,'production_batch_recalled',
                'production_batch:'||requested_production_batch_id::text,
                jsonb_build_object('resource_name',batch_row.batch_code,'reason',trim(requested_reason),
                    'result','success','frozen_item_count',freeze_count),now_at,now_at,now_at
            );
            production_batch_id:=requested_production_batch_id; current_status:='recalled';
            frozen_item_count:=freeze_count; recalled_at:=now_at; actor_id:=resolved_actor::uuid;
            RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='production batch recall is busy';
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_RECALL_SIGNATURE} FROM PUBLIC")
    if _role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_RECALL_SIGNATURE} TO yimatong_app")


def _install_batch_mutation_authority() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.authorize_production_batch_actor(
            requested_target_tenant_id uuid,requested_auth_session_id uuid,requested_permission_code text
        ) RETURNS TABLE(actor_id uuid,principal_tenant_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE probed_principal_tenant_id uuid; DECLARE matched_role_id uuid;
        DECLARE matched_authorization_id uuid; DECLARE matched_permission_id uuid;
        DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
            IF requested_permission_code NOT IN ('product:create','product:update','product:delete') THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='production batch permission is invalid';
            END IF;
            IF public.current_tenant_id() IS DISTINCT FROM requested_target_tenant_id THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='production batch tenant context mismatch';
            END IF;
            SELECT session.tenant_id INTO probed_principal_tenant_id FROM public.auth_sessions AS session
            WHERE session.id=requested_auth_session_id;
            IF probed_principal_tenant_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='production batch auth session is not live';
            END IF;
            PERFORM tenant.id FROM public.tenants AS tenant
            WHERE tenant.id IN (probed_principal_tenant_id,requested_target_tenant_id)
            ORDER BY tenant.id::text FOR UPDATE;
            PERFORM pg_advisory_xact_lock(hashtextextended('auth-session:'||requested_auth_session_id::text,0));
            SELECT account.id,session.tenant_id INTO actor_id,principal_tenant_id
            FROM public.auth_sessions AS session
            JOIN public.accounts AS account
              ON account.tenant_id=session.tenant_id AND account.id=session.account_id
            JOIN public.tenants AS principal ON principal.id=session.tenant_id
            WHERE session.id=requested_auth_session_id AND session.revoked_at IS NULL
              AND session.expires_at>now_at AND session.auth_version=account.auth_version
              AND account.is_active AND principal.status='active';
            IF actor_id IS NULL OR principal_tenant_id IS DISTINCT FROM probed_principal_tenant_id THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='production batch auth session is not live';
            END IF;
            SELECT role.id INTO matched_role_id FROM public.account_roles AS account_role
            JOIN public.roles AS role ON role.tenant_id=account_role.tenant_id AND role.id=account_role.role_id
            WHERE account_role.tenant_id=principal_tenant_id AND account_role.account_id=actor_id
              AND role.name IN ('admin','operator') ORDER BY role.id::text LIMIT 1
            FOR SHARE OF account_role,role;
            IF matched_role_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='production batch role denied';
            END IF;
            SELECT permission.id INTO matched_permission_id
            FROM public.account_roles AS account_role
            JOIN public.role_permissions AS role_permission
              ON role_permission.tenant_id=account_role.tenant_id
             AND role_permission.role_id=account_role.role_id
            JOIN public.permissions AS permission
              ON permission.tenant_id=role_permission.tenant_id
             AND permission.id=role_permission.permission_id
            WHERE account_role.tenant_id=principal_tenant_id AND account_role.account_id=actor_id
              AND permission.code=requested_permission_code
            ORDER BY permission.id::text LIMIT 1
            FOR SHARE OF account_role,role_permission,permission;
            IF matched_permission_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='production batch permission denied';
            END IF;
            IF principal_tenant_id IS DISTINCT FROM requested_target_tenant_id THEN
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(principal_tenant_id::text||':'||requested_target_tenant_id::text,0)
                );
                SELECT authz.id INTO matched_authorization_id FROM public.agency_authorizations AS authz
                JOIN public.tenants AS agency ON agency.id=authz.agency_tenant_id
                JOIN public.tenants AS client ON client.id=authz.client_tenant_id
                WHERE authz.agency_tenant_id=principal_tenant_id
                  AND authz.client_tenant_id=requested_target_tenant_id
                  AND agency.tenant_type='agency' AND agency.status='active'
                  AND client.tenant_type='brand' AND client.status='active'
                  AND authz.status='active' AND (authz.expires_at IS NULL OR authz.expires_at>now_at)
                  AND authz.scope::jsonb ? 'products' FOR UPDATE OF authz;
                IF matched_authorization_id IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE='42501',
                        MESSAGE='production batch lacks a live products authorization';
                END IF;
            END IF;
            RETURN NEXT;
        END
        $function$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.authorize_production_batch_actor(uuid,uuid,text) FROM PUBLIC")
    if _role_exists():
        op.execute("REVOKE ALL ON FUNCTION public.authorize_production_batch_actor(uuid,uuid,text) FROM yimatong_app")
    op.execute(
        r"""
        CREATE FUNCTION public.create_production_batch(
            requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
            requested_production_batch_id uuid,requested_product_id uuid,requested_sku_id uuid,
            requested_batch_code text,requested_production_date date,requested_expiry_date date,
            requested_origin text
        ) RETURNS TABLE(production_batch_id uuid,actor_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE resolved_actor uuid; DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
            SELECT authority.actor_id INTO resolved_actor
            FROM public.authorize_production_batch_actor(
                requested_tenant_id,requested_auth_session_id,'product:create'
            ) authority;
            IF requested_audit_id IS NULL OR requested_production_batch_id IS NULL
               OR requested_product_id IS NULL OR requested_sku_id IS NULL
               OR NULLIF(trim(requested_batch_code),'') IS NULL OR length(requested_batch_code)>100
               OR requested_production_date IS NULL OR requested_expiry_date IS NULL
               OR requested_expiry_date<requested_production_date
               OR length(requested_origin)>200 THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='production batch input is invalid';
            END IF;
            PERFORM product.id FROM public.products AS product
            WHERE product.tenant_id=requested_tenant_id AND product.id=requested_product_id FOR SHARE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='product is unavailable'; END IF;
            PERFORM sku.id FROM public.skus AS sku WHERE sku.tenant_id=requested_tenant_id
              AND sku.product_id=requested_product_id AND sku.id=requested_sku_id FOR SHARE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='SKU is unavailable'; END IF;
            INSERT INTO public.production_batches(
                id,tenant_id,product_id,sku_id,batch_code,production_date,expiry_date,origin,status,
                created_at,updated_at
            ) VALUES(requested_production_batch_id,requested_tenant_id,requested_product_id,requested_sku_id,
                trim(requested_batch_code),requested_production_date,requested_expiry_date,
                NULLIF(trim(requested_origin),''),'active',now_at,now_at);
            INSERT INTO public.platform_audit_log(
                id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
            ) VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,
                'production_batch_created','production_batch:'||requested_production_batch_id::text,
                jsonb_build_object('resource_name',trim(requested_batch_code),'product_id',requested_product_id,
                    'result','success'),now_at,now_at,now_at);
            production_batch_id:=requested_production_batch_id; actor_id:=resolved_actor; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='production batch parent is busy';
        END
        $function$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.update_production_batch(
            requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
            requested_production_batch_id uuid,requested_batch_code text,requested_production_date date,
            requested_expiry_date date,requested_origin text,requested_origin_present boolean
        ) RETURNS TABLE(production_batch_id uuid,actor_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE resolved_actor uuid; DECLARE batch_row record; DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        DECLARE next_code text; DECLARE next_production_date date; DECLARE next_expiry_date date;
        DECLARE next_origin text; DECLARE changed_fields text[]:=ARRAY[]::text[];
        BEGIN
            SELECT authority.actor_id INTO resolved_actor
            FROM public.authorize_production_batch_actor(
                requested_tenant_id,requested_auth_session_id,'product:update'
            ) authority;
            IF requested_audit_id IS NULL OR requested_production_batch_id IS NULL
               OR requested_origin_present IS NULL OR length(requested_batch_code)>100
               OR (requested_batch_code IS NOT NULL AND NULLIF(trim(requested_batch_code),'') IS NULL)
               OR length(requested_origin)>200 THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='production batch patch is invalid';
            END IF;
            SELECT * INTO batch_row FROM public.production_batches AS batch
            WHERE batch.tenant_id=requested_tenant_id AND batch.id=requested_production_batch_id
            FOR UPDATE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='production batch is unavailable'; END IF;
            IF batch_row.status::text<>'active'
               OR batch_row.expiry_date<(now_at AT TIME ZONE 'Asia/Shanghai')::date THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='only an active production batch may be updated';
            END IF;
            next_code:=COALESCE(trim(requested_batch_code),batch_row.batch_code);
            next_production_date:=COALESCE(requested_production_date,batch_row.production_date);
            next_expiry_date:=COALESCE(requested_expiry_date,batch_row.expiry_date);
            next_origin:=CASE WHEN requested_origin_present
                THEN NULLIF(trim(requested_origin),'') ELSE batch_row.origin END;
            IF next_expiry_date<next_production_date THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='production batch date range is invalid';
            END IF;
            IF next_code IS DISTINCT FROM batch_row.batch_code THEN
                changed_fields:=array_append(changed_fields,'batch_code');
            END IF;
            IF next_production_date IS DISTINCT FROM batch_row.production_date THEN
                changed_fields:=array_append(changed_fields,'production_date');
            END IF;
            IF next_expiry_date IS DISTINCT FROM batch_row.expiry_date THEN
                changed_fields:=array_append(changed_fields,'expiry_date');
            END IF;
            IF next_origin IS DISTINCT FROM batch_row.origin THEN
                changed_fields:=array_append(changed_fields,'origin');
            END IF;
            UPDATE public.production_batches AS batch SET batch_code=next_code,
                production_date=next_production_date,expiry_date=next_expiry_date,origin=next_origin,updated_at=now_at
            WHERE batch.tenant_id=requested_tenant_id AND batch.id=requested_production_batch_id;
            INSERT INTO public.platform_audit_log(
                id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
            ) VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,
                'production_batch_updated','production_batch:'||requested_production_batch_id::text,
                jsonb_build_object('resource_name',next_code,'changed_fields',to_jsonb(changed_fields),
                    'result','success'),now_at,now_at,now_at);
            production_batch_id:=requested_production_batch_id; actor_id:=resolved_actor; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='production batch is busy';
        END
        $function$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.delete_production_batch(
            requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
            requested_production_batch_id uuid
        ) RETURNS TABLE(production_batch_id uuid,actor_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE resolved_actor uuid; DECLARE batch_row record; DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
            SELECT authority.actor_id INTO resolved_actor
            FROM public.authorize_production_batch_actor(
                requested_tenant_id,requested_auth_session_id,'product:delete'
            ) authority;
            IF requested_audit_id IS NULL OR requested_production_batch_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='production batch delete input is invalid';
            END IF;
            SELECT * INTO batch_row FROM public.production_batches AS batch
            WHERE batch.tenant_id=requested_tenant_id AND batch.id=requested_production_batch_id
            FOR UPDATE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='production batch is unavailable'; END IF;
            IF EXISTS(SELECT 1 FROM public.code_batches AS code_batch
                WHERE code_batch.tenant_id=requested_tenant_id
                  AND code_batch.production_batch_id=requested_production_batch_id FOR SHARE) THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='production batch has associated code batches';
            END IF;
            INSERT INTO public.platform_audit_log(
                id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
            ) VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,
                'production_batch_deleted','production_batch:'||requested_production_batch_id::text,
                jsonb_build_object('resource_name',batch_row.batch_code,'result','success'),now_at,now_at,now_at);
            DELETE FROM public.production_batches AS batch
            WHERE batch.tenant_id=requested_tenant_id AND batch.id=requested_production_batch_id;
            production_batch_id:=requested_production_batch_id; actor_id:=resolved_actor; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='production batch is busy';
        END
        $function$
        """
    )
    for signature in _BATCH_MUTATION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _role_exists():
            op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO yimatong_app")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.add_column("consumer_profiles", sa.Column("wechat_openid_hash", sa.String(64), nullable=True))
    op.add_column("consumer_profiles", sa.Column("wechat_openid_ciphertext", sa.LargeBinary(), nullable=True))
    op.add_column("consumer_profiles", sa.Column("wechat_openid_nonce", sa.LargeBinary(), nullable=True))
    op.add_column("consumer_profiles", sa.Column("wechat_openid_key_id", sa.String(32), nullable=True))
    op.execute(
        "ALTER TABLE public.consumer_profiles ADD CONSTRAINT ck_consumer_profiles_wechat_openid_envelope "
        "CHECK ((wechat_openid_hash IS NULL AND wechat_openid_ciphertext IS NULL "
        "AND wechat_openid_nonce IS NULL AND wechat_openid_key_id IS NULL) OR "
        "(length(wechat_openid_hash)=64 AND wechat_openid_ciphertext IS NOT NULL "
        "AND length(wechat_openid_nonce)=12 AND NULLIF(trim(wechat_openid_key_id),'') IS NOT NULL)) NOT VALID"
    )
    op.execute("ALTER TABLE public.consumer_profiles VALIDATE CONSTRAINT ck_consumer_profiles_wechat_openid_envelope")
    op.execute(
        "ALTER TABLE public.consumer_profiles ADD CONSTRAINT "
        "ck_consumer_profiles_wechat_openid_envelope_format CHECK (wechat_openid_hash IS NULL OR "
        "(wechat_openid_hash ~ '^[0-9a-f]{64}$' AND octet_length(wechat_openid_ciphertext)>=16 "
        "AND wechat_openid_key_id ~ '^aes-master-v[1-9][0-9]*$')) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.consumer_profiles VALIDATE CONSTRAINT ck_consumer_profiles_wechat_openid_envelope_format"
    )
    op.execute(
        "ALTER TABLE public.connectors ADD CONSTRAINT ck_connectors_config_has_no_plaintext_secrets "
        "CHECK (NOT (config::jsonb ?| ARRAY['oa_appsecret','cert_private_key','api_v3_key','api_key',"
        "'api_secret','callback_secret','mch_key','secret'])) NOT VALID"
    )
    op.create_table(
        "connector_secret_migration_backups",
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("moved_secrets_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("preexisting_secret_keys", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("connector_id"),
    )
    _install_code_item_guard(allow_authoritative_legacy_recall=True)
    _install_batch_mutation_authority()
    _install_recall_authority()


def _restore_connector_secrets() -> None:
    from app.services.connectors.secrets import decrypt_secrets, encrypt_secrets

    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT backup.connector_id,backup.tenant_id,backup.moved_secrets_encrypted,"
                "backup.preexisting_secret_keys,connector.config,connector.secrets_encrypted "
                "FROM public.connector_secret_migration_backups AS backup JOIN public.connectors AS connector "
                "ON connector.tenant_id=backup.tenant_id AND connector.id=backup.connector_id "
                "ORDER BY backup.tenant_id,backup.connector_id FOR UPDATE OF connector"
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        moved = decrypt_secrets(bytes(row["moved_secrets_encrypted"]))
        current = decrypt_secrets(bytes(row["secrets_encrypted"])) if row["secrets_encrypted"] else {}
        if any(current.get(key) != value for key, value in moved.items()):
            raise RuntimeError(f"connector secret changed after migration for connector_id={row['connector_id']}")
        preexisting = set(row["preexisting_secret_keys"] or [])
        restored_config = {**dict(row["config"] or {}), **moved}
        restored_secrets = {key: value for key, value in current.items() if key not in moved or key in preexisting}
        op.get_bind().execute(
            sa.text(
                "UPDATE public.connectors SET config=:config,secrets_encrypted=:secrets,updated_at=CURRENT_TIMESTAMP "
                "WHERE tenant_id=:tenant_id AND id=:connector_id"
            ).bindparams(sa.bindparam("config", type_=sa.JSON())),
            {
                "config": restored_config,
                "secrets": encrypt_secrets(restored_secrets) if restored_secrets else None,
                "tenant_id": row["tenant_id"],
                "connector_id": row["connector_id"],
            },
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP FUNCTION IF EXISTS public.{_RECALL_SIGNATURE}")
    for signature in reversed(_BATCH_MUTATION_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
    op.execute("DROP FUNCTION IF EXISTS public.authorize_production_batch_actor(uuid,uuid,text)")
    _install_code_item_guard(allow_authoritative_legacy_recall=False)
    op.drop_constraint("ck_connectors_config_has_no_plaintext_secrets", "connectors", type_="check")
    _restore_connector_secrets()
    op.drop_table("connector_secret_migration_backups")
    op.drop_constraint(
        "ck_consumer_profiles_wechat_openid_envelope_format",
        "consumer_profiles",
        type_="check",
    )
    op.drop_constraint("ck_consumer_profiles_wechat_openid_envelope", "consumer_profiles", type_="check")
    op.drop_column("consumer_profiles", "wechat_openid_key_id")
    op.drop_column("consumer_profiles", "wechat_openid_nonce")
    op.drop_column("consumer_profiles", "wechat_openid_ciphertext")
    op.drop_column("consumer_profiles", "wechat_openid_hash")
