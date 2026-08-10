"""Harden code-item lifecycle mutations behind actor-bound interfaces.

Revision ID: d297eb0518da
Revises: c186daf407c9
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

revision: str = "d297eb0518da"
down_revision: str | None = "c186daf407c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_ROLE = "yimatong_app"
_AUDIT_FUNCTION = "append_authenticated_audit_event"
_AUDIT_INTERNAL = "append_authenticated_audit_event_lifecycle_internal"
_ITEM_GUARD = "guard_code_item_batch_contract"
_ITEM_FINAL_GUARD = "enforce_code_item_parent_final_state"
_RECALL_FUNCTION = "freeze_recalled_production_batch_codes"
_LIFECYCLE_ACTOR_FUNCTION = "authorize_code_lifecycle_actor"
_DELIVERY_CONTRACT_REVISION = "3f91c0d2e4a6"
_DELIVERY_STATUS_REVISION = "c7d8e9f0a1b2"


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"),
            {"role": _RUNTIME_ROLE},
        )
        .scalar()
    )


def _install_interception_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION public.guard_interception_code_item_binding()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP='DELETE' THEN
                IF has_parameter_privilege(session_user, 'app.bypass_rls', 'SET') THEN RETURN OLD; END IF;
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='risk interception deletion is forbidden';
            END IF;
            IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.risk_rule_id IS DISTINCT FROM OLD.risk_rule_id
               OR NEW.code_item_id IS DISTINCT FROM OLD.code_item_id
               OR NEW.action IS DISTINCT FROM OLD.action
               OR NEW.auto_triggered IS DISTINCT FROM OLD.auto_triggered THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='risk interception authority is immutable';
            END IF;
            IF OLD.action_taken IS NOT NULL AND NEW.action_taken IS DISTINCT FROM OLD.action_taken THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='risk interception result is immutable once set';
            END IF;
            IF OLD.action_detail IS NOT NULL
               AND NEW.action_detail::jsonb IS DISTINCT FROM OLD.action_detail::jsonb THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='risk interception detail is immutable once set';
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_interception_code_item_binding() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_interception_code_item_binding "
        "BEFORE UPDATE OF tenant_id,risk_rule_id,code_item_id,action,auto_triggered,"
        "action_taken,action_detail OR DELETE "
        "ON public.interception_records FOR EACH ROW "
        "EXECUTE FUNCTION public.guard_interception_code_item_binding()"
    )


def _install_item_contract_guards() -> None:
    op.execute("DROP TRIGGER trg_guard_code_item_batch_contract ON public.code_items")
    op.execute(f"DROP FUNCTION public.{_ITEM_GUARD}()")
    op.execute(
        f"""
        CREATE FUNCTION public.{_ITEM_GUARD}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE batch_row public.code_batches%ROWTYPE;
        DECLARE rollout_finalized boolean;
        DECLARE privileged_legacy boolean := has_parameter_privilege(session_user, 'app.bypass_rls', 'SET');
        BEGIN
            IF TG_OP = 'DELETE' THEN
                SELECT * INTO batch_row FROM public.code_batches
                WHERE tenant_id = OLD.tenant_id AND id = OLD.code_batch_id FOR UPDATE;
                IF batch_row.contract_version = 0 AND privileged_legacy THEN RETURN OLD; END IF;
                RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'code item deletion is forbidden';
            END IF;
            SELECT * INTO batch_row FROM public.code_batches
            WHERE tenant_id = NEW.tenant_id AND id = NEW.code_batch_id FOR UPDATE NOWAIT;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'code item batch is unavailable';
            END IF;
            IF batch_row.contract_version = 0 THEN
                SELECT EXISTS (
                    SELECT 1 FROM public.code_delivery_contract_rollout_state
                    WHERE id = 1 AND phase = 'finalized'
                ) INTO rollout_finalized;
                IF rollout_finalized AND NOT privileged_legacy THEN
                    RAISE EXCEPTION USING ERRCODE = '42501',
                        MESSAGE = 'legacy code batch items are read-only after rollout finalization';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF batch_row.status::text <> 'generating' OR NEW.status::text <> 'created' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514',
                        MESSAGE = 'code items may be created only in a generating batch';
                END IF;
                IF batch_row.code_type = 'paired' THEN
                    IF NEW.code_type NOT IN ('outer', 'inner') OR NEW.pair_id IS NULL THEN
                        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'paired code item metadata is invalid';
                    END IF;
                ELSIF NEW.code_type <> 'single' OR NEW.pair_id IS NOT NULL THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'single code item metadata is invalid';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.code_batch_id IS DISTINCT FROM OLD.code_batch_id
               OR NEW.public_id IS DISTINCT FROM OLD.public_id
               OR NEW.code_type IS DISTINCT FROM OLD.code_type
               OR NEW.pair_id IS DISTINCT FROM OLD.pair_id THEN
                RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'code item identity is immutable';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                (OLD.status::text = 'created' AND NEW.status::text IN ('activated', 'revoked'))
                OR (OLD.status::text = 'activated' AND NEW.status::text IN ('bound', 'revoked', 'frozen'))
                OR (OLD.status::text = 'bound' AND NEW.status::text IN ('expired', 'revoked', 'frozen'))
                OR (OLD.status::text = 'frozen' AND NEW.status::text IN ('activated', 'bound', 'revoked'))
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'invalid code item lifecycle transition';
            END IF;
            IF OLD.status::text = 'created' AND NEW.status::text = 'activated'
               AND batch_row.status::text <> 'delivered' THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'code item activation requires a delivered code batch';
            END IF;
            IF OLD.status::text = 'activated' AND NEW.status::text = 'bound'
               AND batch_row.status::text <> 'activated' THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'code item binding requires an activated code batch';
            END IF;
            IF OLD.status::text = 'frozen' AND NEW.status::text IN ('activated', 'bound')
               AND batch_row.status::text <> 'activated' THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'code item recovery requires an activated code batch';
            END IF;
            RETURN NEW;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE = '55P03',
                MESSAGE = 'authoritative code batch is concurrently changing';
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ITEM_GUARD}() FROM PUBLIC")
    op.execute(
        f"CREATE TRIGGER trg_guard_code_item_batch_contract_insert_delete "
        f"BEFORE INSERT OR DELETE ON public.code_items FOR EACH ROW EXECUTE FUNCTION public.{_ITEM_GUARD}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_guard_code_item_batch_contract_update "
        "BEFORE UPDATE OF tenant_id, code_batch_id, public_id, code_type, pair_id, status, "
        "activated_at, bound_at, revoked_at, frozen_from_status, frozen_at, frozen_by, freeze_reason, "
        "freeze_provenance_version ON public.code_items "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_ITEM_GUARD}()"
    )

    op.execute("DROP TRIGGER trg_enforce_code_item_parent_final_state ON public.code_items")
    op.execute(f"DROP FUNCTION public.{_ITEM_FINAL_GUARD}()")
    op.execute(
        f"""
        CREATE FUNCTION public.{_ITEM_FINAL_GUARD}()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
        AS $function$
        DECLARE parent_status text;
        BEGIN
            IF (OLD.status::text = 'created' AND NEW.status::text = 'activated')
               OR (OLD.status::text = 'frozen' AND NEW.status::text IN ('activated', 'bound')) THEN
                SELECT batch.status::text INTO parent_status
                FROM public.code_batches AS batch
                WHERE batch.tenant_id = NEW.tenant_id AND batch.id = NEW.code_batch_id;
                IF parent_status IS DISTINCT FROM 'activated' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514',
                        MESSAGE = 'active code item requires its code batch to finish activated';
                END IF;
            END IF;
            RETURN NULL;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ITEM_FINAL_GUARD}() FROM PUBLIC")
    op.execute(
        f"CREATE CONSTRAINT TRIGGER trg_enforce_code_item_parent_final_state "
        "AFTER UPDATE OF status ON public.code_items DEFERRABLE INITIALLY DEFERRED "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_ITEM_FINAL_GUARD}()"
    )


def _install_provenance_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION public.guard_code_item_lifecycle_provenance()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF OLD.status::text IN ('revoked', 'expired') AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'terminal code item status is absorbing';
            END IF;
            IF OLD.activated_at IS NOT NULL AND NEW.activated_at IS DISTINCT FROM OLD.activated_at THEN
                RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'activated_at is immutable once set';
            END IF;
            IF OLD.bound_at IS NOT NULL AND NEW.bound_at IS DISTINCT FROM OLD.bound_at THEN
                RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'bound_at is immutable once set';
            END IF;
            IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
                RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'revoked_at is immutable once set';
            END IF;
            IF OLD.status::text = 'created' AND NEW.status::text = 'activated' AND NEW.activated_at IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'activation timestamp is required';
            END IF;
            IF OLD.status::text = 'activated' AND NEW.status::text = 'bound' AND NEW.bound_at IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'binding timestamp is required';
            END IF;
            IF NEW.status::text = 'revoked' AND OLD.status::text <> 'revoked' AND NEW.revoked_at IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'revocation timestamp is required';
            END IF;
            IF OLD.status::text = 'frozen' AND NEW.status::text IN ('activated', 'bound')
               AND NEW.status::text IS DISTINCT FROM OLD.frozen_from_status THEN
                RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'frozen item must recover to its exact prior status';
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_code_item_lifecycle_provenance() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_code_item_lifecycle_provenance "
        "BEFORE UPDATE OF status, activated_at, bound_at, revoked_at, frozen_from_status, frozen_at, frozen_by, "
        "freeze_reason, freeze_provenance_version ON public.code_items "
        "FOR EACH ROW EXECUTE FUNCTION public.guard_code_item_lifecycle_provenance()"
    )


def _install_recall_freeze() -> None:
    op.execute("DROP TRIGGER trg_freeze_recalled_production_batch_codes ON public.production_batches")
    op.execute(f"DROP FUNCTION public.{_RECALL_FUNCTION}()")
    op.execute(
        f"""
        CREATE FUNCTION public.{_RECALL_FUNCTION}()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
        AS $function$
        DECLARE code_batch_row record;
        DECLARE code_item_row record;
        BEGIN
            IF OLD.status::text <> 'active' OR NEW.status::text <> 'recalled' THEN RETURN NEW; END IF;
            FOR code_batch_row IN
                SELECT code_batch.id FROM public.code_batches AS code_batch
                WHERE code_batch.tenant_id = NEW.tenant_id
                  AND code_batch.product_id = NEW.product_id
                  AND code_batch.sku_id = NEW.sku_id
                  AND code_batch.production_batch_id = NEW.id
                ORDER BY code_batch.id::text FOR UPDATE
            LOOP
                FOR code_item_row IN
                    SELECT code_item.id, code_item.status::text AS prior_status
                    FROM public.code_items AS code_item
                    WHERE code_item.tenant_id = NEW.tenant_id
                      AND code_item.code_batch_id = code_batch_row.id
                      AND code_item.status::text IN ('activated', 'bound')
                    ORDER BY code_item.id::text FOR UPDATE
                LOOP
                    UPDATE public.code_items
                    SET status = 'frozen', frozen_from_status = code_item_row.prior_status,
                        frozen_at = CURRENT_TIMESTAMP, frozen_by = NEW.recalled_by::text,
                        freeze_reason = left('production batch recall: ' || NEW.id::text, 200),
                        freeze_provenance_version = 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = code_item_row.id;
                END LOOP;
            END LOOP;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_RECALL_FUNCTION}() FROM PUBLIC")
    op.execute(
        f"CREATE TRIGGER trg_freeze_recalled_production_batch_codes "
        f"AFTER UPDATE OF status ON public.production_batches FOR EACH ROW EXECUTE FUNCTION public.{_RECALL_FUNCTION}()"
    )


def _install_lifecycle_actor_authority() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_LIFECYCLE_ACTOR_FUNCTION}(
            requested_target_tenant_id uuid, requested_auth_session_id uuid
        ) RETURNS TABLE (actor_id text, principal_tenant_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
        AS $function$
        DECLARE probed_principal_tenant_id uuid;
        DECLARE resolved_account_id uuid;
        DECLARE matched_authorization_id uuid;
        DECLARE now_at timestamptz := CURRENT_TIMESTAMP;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_target_tenant_id THEN
                RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='code lifecycle tenant context mismatch';
            END IF;
            SELECT session.tenant_id INTO probed_principal_tenant_id
            FROM public.auth_sessions AS session WHERE session.id=requested_auth_session_id;
            IF probed_principal_tenant_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='lifecycle auth session is not live';
            END IF;
            PERFORM tenant.id FROM public.tenants AS tenant
            WHERE tenant.id IN (probed_principal_tenant_id,requested_target_tenant_id)
            ORDER BY tenant.id::text FOR UPDATE;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('auth-session:' || requested_auth_session_id::text, 0)
            );
            SELECT session.tenant_id,account.id
            INTO principal_tenant_id,resolved_account_id
            FROM public.auth_sessions AS session
            JOIN public.accounts AS account
              ON account.tenant_id=session.tenant_id AND account.id=session.account_id
            JOIN public.tenants AS principal ON principal.id=session.tenant_id
            WHERE session.id=requested_auth_session_id
              AND session.revoked_at IS NULL AND session.expires_at>now_at
              AND session.auth_version=account.auth_version AND account.is_active
              AND principal.status='active';
            IF resolved_account_id IS NULL OR principal_tenant_id IS DISTINCT FROM probed_principal_tenant_id THEN
                RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='lifecycle auth session is not live';
            END IF;
            IF principal_tenant_id IS DISTINCT FROM requested_target_tenant_id THEN
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(principal_tenant_id::text || ':' || requested_target_tenant_id::text, 0)
                );
                SELECT authz.id INTO matched_authorization_id
                FROM public.agency_authorizations AS authz
                JOIN public.tenants AS agency ON agency.id=authz.agency_tenant_id
                JOIN public.tenants AS client ON client.id=authz.client_tenant_id
                WHERE authz.agency_tenant_id=principal_tenant_id
                  AND authz.client_tenant_id=requested_target_tenant_id
                  AND agency.tenant_type='agency' AND agency.status='active'
                  AND client.tenant_type='brand' AND client.status='active'
                  AND authz.status='active'
                  AND (authz.expires_at IS NULL OR authz.expires_at>now_at)
                  AND authz.scope::jsonb ? 'codes'
                FOR UPDATE OF authz;
                IF matched_authorization_id IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE='42501',
                        MESSAGE='code lifecycle lacks a live codes authorization';
                END IF;
            END IF;
            actor_id:=resolved_account_id::text;
            RETURN NEXT;
        END
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_LIFECYCLE_ACTOR_FUNCTION}(uuid,uuid) FROM PUBLIC"
    )
    if _runtime_role_exists():
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{_LIFECYCLE_ACTOR_FUNCTION}(uuid,uuid) FROM {_RUNTIME_ROLE}"
        )


def _protect_audit_interface() -> None:
    op.execute(f"ALTER FUNCTION public.{_AUDIT_FUNCTION}(uuid,uuid,text,text,text,jsonb) RENAME TO {_AUDIT_INTERNAL}")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_AUDIT_INTERNAL}(uuid,uuid,text,text,text,jsonb) FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{_AUDIT_INTERNAL}(uuid,uuid,text,text,text,jsonb) FROM {_RUNTIME_ROLE}"
        )
    op.execute(
        f"""
        CREATE FUNCTION public.{_AUDIT_FUNCTION}(
            requested_id uuid, requested_auth_session_id uuid, requested_target_tenant text,
            requested_action text, requested_resource text, requested_details jsonb
        ) RETURNS TABLE (audit_id uuid, resolved_operator_id text, recorded_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
        AS $function$
        DECLARE target_uuid uuid;
        DECLARE requested_resource_id uuid;
        DECLARE resolved_actor_id text;
        DECLARE now_at timestamptz := CURRENT_TIMESTAMP;
        BEGIN
            IF requested_action IN (
                'code_activate', 'code_bind', 'code_freeze', 'code_recover',
                'code_unfreeze', 'code_revoke', 'code_void'
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '42501',
                    MESSAGE = 'code lifecycle audit actions require the lifecycle interface';
            END IF;
            IF requested_action='risk_alert_resolved' THEN
                BEGIN
                    target_uuid:=requested_target_tenant::uuid;
                    requested_resource_id:=split_part(requested_resource,':',2)::uuid;
                EXCEPTION WHEN invalid_text_representation THEN
                    RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='risk alert audit resource is invalid';
                END;
                IF requested_resource_id IS NULL
                   OR requested_resource IS DISTINCT FROM 'risk_alert:' || requested_resource_id::text THEN
                    RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='risk alert audit resource is invalid';
                END IF;
                SELECT authority.actor_id INTO resolved_actor_id
                FROM public.{_LIFECYCLE_ACTOR_FUNCTION}(target_uuid,requested_auth_session_id) AS authority;
                PERFORM alert.id FROM public.risk_alerts AS alert
                WHERE alert.tenant_id=target_uuid AND alert.id=requested_resource_id AND alert.resolved
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING ERRCODE='23503',
                        MESSAGE='risk alert audit resource is not tenant-owned and resolved';
                END IF;
                INSERT INTO public.platform_audit_log (
                    id,operator_id,target_tenant_id,action,resource,details,
                    timestamp,created_at,updated_at
                ) VALUES (
                    requested_id,resolved_actor_id,target_uuid::text,requested_action,requested_resource,
                    requested_details,now_at,now_at,now_at
                );
                RETURN QUERY SELECT requested_id,resolved_actor_id,now_at;
                RETURN;
            END IF;
            RETURN QUERY SELECT * FROM public.{_AUDIT_INTERNAL}(
                requested_id, requested_auth_session_id, requested_target_tenant,
                requested_action, requested_resource, requested_details
            );
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_AUDIT_FUNCTION}(uuid,uuid,text,text,text,jsonb) FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(
            f"GRANT EXECUTE ON FUNCTION public.{_AUDIT_FUNCTION}(uuid,uuid,text,text,text,jsonb) TO {_RUNTIME_ROLE}"
        )


def _install_first_scan_interface() -> None:
    op.execute(
        """
        CREATE FUNCTION public.mark_code_item_first_scanned(requested_tenant_id uuid, requested_public_id text)
        RETURNS TABLE (code_item_id uuid, first_scan boolean, first_scanned_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
        AS $function$
        DECLARE current_scope uuid;
        DECLARE item_row record;
        DECLARE now_at timestamptz := CURRENT_TIMESTAMP;
        BEGIN
            current_scope := public.current_tenant_id();
            IF current_scope IS DISTINCT FROM requested_tenant_id THEN
                RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'code item tenant context mismatch';
            END IF;
            SELECT item.id, item.first_scanned_at INTO item_row
            FROM public.code_items AS item
            WHERE item.tenant_id = requested_tenant_id AND item.public_id = requested_public_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'code item is unavailable';
            END IF;
            IF item_row.first_scanned_at IS NULL THEN
                UPDATE public.code_items SET first_scanned_at = now_at, updated_at = now_at
                WHERE tenant_id = requested_tenant_id AND id = item_row.id;
                RETURN QUERY SELECT item_row.id, true, now_at;
            ELSE
                RETURN QUERY SELECT item_row.id, false, item_row.first_scanned_at;
            END IF;
        END
        $function$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.mark_code_item_first_scanned(uuid,text) FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.mark_code_item_first_scanned(uuid,text) TO {_RUNTIME_ROLE}")


def _install_lifecycle_interfaces() -> None:
    # Functions intentionally accept identity UUIDs but never an actor, audit
    # action, resource, or lifecycle timestamp from the caller.
    op.execute(_ITEM_LIFECYCLE_SQL)
    op.execute(_BATCH_LIFECYCLE_SQL)
    op.execute(_RISK_FREEZE_SQL)
    signatures = (
        "transition_code_item_lifecycle(uuid,uuid,uuid,uuid,text,text)",
        "transition_code_batch_lifecycle(uuid,uuid,uuid,uuid,text,text)",
        "freeze_code_item_for_risk(uuid,uuid,uuid,uuid,uuid)",
    )
    for signature in signatures:
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _runtime_role_exists():
            op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_RUNTIME_ROLE}")


_ITEM_LIFECYCLE_SQL = r"""
CREATE FUNCTION public.transition_code_item_lifecycle(
    requested_tenant_id uuid, requested_auth_session_id uuid, requested_audit_id uuid,
    requested_code_item_id uuid, requested_action text, requested_reason text
) RETURNS TABLE (code_item_id uuid, prior_status text, current_status text, recorded_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $function$
DECLARE item_probe record; DECLARE item_row record; DECLARE batch_row record; DECLARE production_batch_row record;
DECLARE actor_id text; DECLARE now_at timestamptz := CURRENT_TIMESTAMP;
DECLARE audit_action text; DECLARE audit_details jsonb;
BEGIN
    IF requested_action NOT IN ('bind', 'freeze', 'recover', 'void') THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'unknown code item lifecycle action';
    END IF;
    IF requested_action IN ('freeze', 'void')
       AND (NULLIF(trim(requested_reason), '') IS NULL OR length(requested_reason) > 200) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'bounded lifecycle reason is required';
    END IF;
    SELECT authority.actor_id INTO actor_id
    FROM public.authorize_code_lifecycle_actor(requested_tenant_id,requested_auth_session_id) AS authority;
    SELECT item.code_batch_id, batch.production_batch_id, batch.product_id, batch.sku_id INTO item_probe
    FROM public.code_items item JOIN public.code_batches batch
      ON batch.tenant_id=item.tenant_id AND batch.id=item.code_batch_id
    WHERE item.tenant_id=requested_tenant_id AND item.id=requested_code_item_id;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='code item is unavailable'; END IF;
    SELECT * INTO production_batch_row FROM public.production_batches pb
    WHERE pb.tenant_id=requested_tenant_id AND pb.id=item_probe.production_batch_id
      AND pb.product_id=item_probe.product_id AND pb.sku_id=item_probe.sku_id FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='production batch is unavailable'; END IF;
    SELECT * INTO batch_row FROM public.code_batches batch
    WHERE batch.tenant_id=requested_tenant_id AND batch.id=item_probe.code_batch_id
      AND batch.production_batch_id=item_probe.production_batch_id
      AND batch.product_id=item_probe.product_id AND batch.sku_id=item_probe.sku_id FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='code batch is unavailable'; END IF;
    SELECT * INTO item_row FROM public.code_items item
    WHERE item.tenant_id=requested_tenant_id AND item.id=requested_code_item_id
      AND item.code_batch_id=item_probe.code_batch_id FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='code item is unavailable'; END IF;
    prior_status := item_row.status::text;
    IF requested_action='bind' THEN
        IF prior_status <> 'activated' OR batch_row.status::text <> 'activated'
           OR production_batch_row.status::text <> 'active'
           OR production_batch_row.expiry_date < (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')::date THEN
            RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='code item is not bindable';
        END IF;
        UPDATE public.code_items SET status='bound', bound_at=now_at, updated_at=now_at WHERE id=item_row.id;
        current_status := 'bound'; audit_action := 'code_bind';
    ELSIF requested_action='freeze' THEN
        IF prior_status NOT IN ('activated','bound') THEN
            RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='code item is not freezable';
        END IF;
        UPDATE public.code_items SET status='frozen', frozen_from_status=prior_status, frozen_at=now_at,
            frozen_by=actor_id, freeze_reason=trim(requested_reason), freeze_provenance_version=1, updated_at=now_at
        WHERE id=item_row.id;
        current_status := 'frozen'; audit_action := 'code_freeze';
    ELSIF requested_action='recover' THEN
        IF prior_status <> 'frozen' OR item_row.freeze_provenance_version <> 1
           OR item_row.frozen_from_status NOT IN ('activated','bound') OR batch_row.status::text <> 'activated' THEN
            RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='code item is not recoverable';
        END IF;
        current_status := item_row.frozen_from_status;
        UPDATE public.code_items SET status=current_status::public.codeitemstatus,
            frozen_from_status=NULL, frozen_at=NULL,
            frozen_by=NULL, freeze_reason=NULL, freeze_provenance_version=NULL, updated_at=now_at WHERE id=item_row.id;
        audit_action := 'code_recover';
    ELSE
        IF prior_status IN ('revoked','expired') THEN
            RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='terminal code item cannot be voided again';
        END IF;
        UPDATE public.code_items SET status='revoked', revoked_at=now_at, frozen_from_status=NULL,
            frozen_at=NULL, frozen_by=NULL, freeze_reason=NULL, freeze_provenance_version=NULL, updated_at=now_at
        WHERE id=item_row.id;
        current_status := 'revoked'; audit_action := 'code_void';
    END IF;
    audit_details := jsonb_build_object(
        'reason', CASE WHEN requested_action IN ('freeze','void') THEN trim(requested_reason) END,
        'before', jsonb_build_object('status', prior_status), 'after', jsonb_build_object('status', current_status));
    INSERT INTO public.platform_audit_log (
        id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
    ) VALUES (
        requested_audit_id,actor_id,requested_tenant_id::text,audit_action,
        'code_item:' || item_row.public_id,audit_details,now_at,now_at,now_at
    );
    code_item_id := item_row.id; recorded_at := now_at; RETURN NEXT;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='code lifecycle authority is concurrently changing';
END
$function$
"""


_BATCH_LIFECYCLE_SQL = r"""
CREATE FUNCTION public.transition_code_batch_lifecycle(
    requested_tenant_id uuid, requested_auth_session_id uuid, requested_audit_id uuid,
    requested_code_batch_id uuid, requested_action text, requested_reason text
) RETURNS TABLE (code_batch_id uuid, affected_item_count integer, current_status text, recorded_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $function$
DECLARE batch_probe record; DECLARE batch_row record; DECLARE item_row record; DECLARE production_batch_row record;
DECLARE actor_id text;
DECLARE now_at timestamptz := CURRENT_TIMESTAMP; DECLARE audit_action text;
BEGIN
    IF requested_action NOT IN ('activate','freeze','void') THEN
        RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='unknown code batch lifecycle action';
    END IF;
    IF requested_action IN ('freeze','void')
       AND (NULLIF(trim(requested_reason), '') IS NULL OR length(requested_reason) > 200) THEN
        RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='bounded lifecycle reason is required';
    END IF;
    SELECT authority.actor_id INTO actor_id
    FROM public.authorize_code_lifecycle_actor(requested_tenant_id,requested_auth_session_id) AS authority;
    SELECT production_batch_id,product_id,sku_id INTO batch_probe FROM public.code_batches
    WHERE tenant_id=requested_tenant_id AND id=requested_code_batch_id;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='code batch is unavailable'; END IF;
    SELECT * INTO production_batch_row FROM public.production_batches pb
    WHERE pb.tenant_id=requested_tenant_id AND pb.id=batch_probe.production_batch_id
      AND pb.product_id=batch_probe.product_id AND pb.sku_id=batch_probe.sku_id FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='production batch is unavailable'; END IF;
    SELECT * INTO batch_row FROM public.code_batches batch
    WHERE batch.tenant_id=requested_tenant_id AND batch.id=requested_code_batch_id
      AND batch.production_batch_id=batch_probe.production_batch_id
      AND batch.product_id=batch_probe.product_id AND batch.sku_id=batch_probe.sku_id FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='code batch is unavailable'; END IF;
    affected_item_count := 0;
    IF requested_action='activate' THEN
        IF batch_row.status::text <> 'delivered' OR production_batch_row.status::text <> 'active'
           OR production_batch_row.expiry_date < (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')::date THEN
            RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='code batch is not activatable';
        END IF;
        IF EXISTS (SELECT 1 FROM public.code_items AS item WHERE item.tenant_id=requested_tenant_id
                   AND item.code_batch_id=batch_row.id AND item.status::text <> 'created') THEN
            RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='code batch contains non-created items';
        END IF;
        FOR item_row IN SELECT item.id FROM public.code_items AS item WHERE item.tenant_id=requested_tenant_id
            AND item.code_batch_id=batch_row.id ORDER BY item.id::text FOR UPDATE NOWAIT
        LOOP
            UPDATE public.code_items SET status='activated', activated_at=now_at, updated_at=now_at
            WHERE id=item_row.id;
            affected_item_count := affected_item_count + 1;
        END LOOP;
        UPDATE public.code_batches SET status='activated', updated_at=now_at WHERE id=batch_row.id;
        current_status := 'activated'; audit_action := 'code_activate';
    ELSIF requested_action='freeze' THEN
        FOR item_row IN SELECT item.id,item.status::text AS prior_status FROM public.code_items AS item
            WHERE item.tenant_id=requested_tenant_id AND item.code_batch_id=batch_row.id
              AND item.status::text IN ('activated','bound') ORDER BY item.id::text FOR UPDATE NOWAIT
        LOOP
            UPDATE public.code_items SET status='frozen', frozen_from_status=item_row.prior_status,
                frozen_at=now_at, frozen_by=actor_id, freeze_reason=trim(requested_reason),
                freeze_provenance_version=1, updated_at=now_at WHERE id=item_row.id;
            affected_item_count := affected_item_count + 1;
        END LOOP;
        IF affected_item_count=0 THEN
            RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='code batch has no freezable items';
        END IF;
        current_status := batch_row.status::text; audit_action := 'code_freeze';
    ELSE
        FOR item_row IN SELECT item.id FROM public.code_items AS item WHERE item.tenant_id=requested_tenant_id
            AND item.code_batch_id=batch_row.id AND item.status::text NOT IN ('revoked','expired')
            ORDER BY item.id::text FOR UPDATE NOWAIT
        LOOP
            UPDATE public.code_items SET status='revoked', revoked_at=now_at, frozen_from_status=NULL,
                frozen_at=NULL, frozen_by=NULL, freeze_reason=NULL, freeze_provenance_version=NULL,
                updated_at=now_at WHERE id=item_row.id;
            affected_item_count := affected_item_count + 1;
        END LOOP;
        IF affected_item_count=0 THEN
            RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='code batch has no voidable items';
        END IF;
        current_status := batch_row.status::text; audit_action := 'code_void';
    END IF;
    INSERT INTO public.platform_audit_log (
        id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
    ) VALUES (
        requested_audit_id,actor_id,requested_tenant_id::text,audit_action,
        'code_batch:' || batch_row.id::text,
        jsonb_build_object('reason', CASE WHEN requested_action IN ('freeze','void') THEN trim(requested_reason) END,
            'affected_item_count', affected_item_count,
            'before', jsonb_build_object('status', batch_row.status::text),
            'after', jsonb_build_object('status', current_status)),now_at,now_at,now_at
    );
    code_batch_id := batch_row.id; recorded_at := now_at; RETURN NEXT;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='code lifecycle authority is concurrently changing';
END
$function$
"""


_RISK_FREEZE_SQL = r"""
CREATE FUNCTION public.freeze_code_item_for_risk(
    requested_tenant_id uuid, requested_interception_id uuid, requested_code_item_id uuid,
    requested_alert_id uuid, requested_audit_id uuid
) RETURNS TABLE (code_item_id uuid, prior_status text, current_status text, risk_alert_id uuid, audit_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $function$
DECLARE item_probe record; DECLARE item_row record; DECLARE rule_row record; DECLARE interception_row record;
DECLARE risk_rule_id uuid;
DECLARE now_at timestamptz := CURRENT_TIMESTAMP; DECLARE derived_reason text;
BEGIN
    IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='risk lifecycle tenant context mismatch';
    END IF;
    PERFORM tenant.id FROM public.tenants tenant WHERE tenant.id=requested_tenant_id FOR UPDATE;
    SELECT item.code_batch_id,batch.production_batch_id,batch.product_id,batch.sku_id INTO item_probe
    FROM public.code_items item
    JOIN public.code_batches batch ON batch.tenant_id=item.tenant_id AND batch.id=item.code_batch_id
    WHERE item.tenant_id=requested_tenant_id AND item.id=requested_code_item_id;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='risk code item is unavailable'; END IF;
    PERFORM pb.id FROM public.production_batches pb
    WHERE pb.tenant_id=requested_tenant_id AND pb.id=item_probe.production_batch_id
      AND pb.product_id=item_probe.product_id AND pb.sku_id=item_probe.sku_id FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='production batch is unavailable'; END IF;
    PERFORM batch.id FROM public.code_batches batch
    WHERE batch.tenant_id=requested_tenant_id AND batch.id=item_probe.code_batch_id
      AND batch.production_batch_id=item_probe.production_batch_id
      AND batch.product_id=item_probe.product_id AND batch.sku_id=item_probe.sku_id FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='code batch is unavailable'; END IF;
    SELECT * INTO item_row FROM public.code_items item
    WHERE item.tenant_id=requested_tenant_id AND item.id=requested_code_item_id
      AND item.code_batch_id=item_probe.code_batch_id FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='risk code item is unavailable'; END IF;
    SELECT interception.risk_rule_id INTO risk_rule_id FROM public.interception_records interception
    WHERE interception.tenant_id=requested_tenant_id AND interception.id=requested_interception_id;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='risk interception is unavailable'; END IF;
    SELECT * INTO rule_row FROM public.risk_rules rule
    WHERE rule.tenant_id=requested_tenant_id AND rule.id=risk_rule_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='risk rule is unavailable'; END IF;
    SELECT * INTO interception_row FROM public.interception_records interception
    WHERE interception.tenant_id=requested_tenant_id AND interception.id=requested_interception_id
      AND interception.risk_rule_id=rule_row.id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='risk interception is unavailable'; END IF;
    IF interception_row.code_item_id IS DISTINCT FROM requested_code_item_id
       OR NOT interception_row.auto_triggered OR interception_row.action <> 'block'
       OR interception_row.action_taken IS NOT NULL OR rule_row.action <> 'block' OR NOT rule_row.enabled THEN
        RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='risk interception is not an executable block';
    END IF;
    prior_status := item_row.status::text;
    IF prior_status NOT IN ('activated','bound') THEN
        RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='risk code item is not freezable';
    END IF;
    derived_reason := left(
        'risk auto block rule=' || rule_row.id::text || ' interception=' || interception_row.id::text,
        200
    );
    UPDATE public.code_items SET status='frozen', frozen_from_status=prior_status, frozen_at=now_at,
        frozen_by='system:risk-auto', freeze_reason=derived_reason, freeze_provenance_version=1,
        updated_at=now_at WHERE id=item_row.id;
    INSERT INTO public.risk_alerts (
        id,tenant_id,alert_type,public_id,code_item_id,detail,ip_hash,resolved,
        risk_level,rule_version,evidence_quality,rule_name,created_at,updated_at
    ) VALUES (
        requested_alert_id,requested_tenant_id,'risk_frozen',item_row.public_id,item_row.id,
        '风控规则自动触发：码已被冻结',NULL,false,'high',
        left(COALESCE(rule_row.config->>'version','v1'),50),'strong',left(rule_row.name,100),now_at,now_at
    );
    INSERT INTO public.platform_audit_log (
        id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
    ) VALUES (
        requested_audit_id,'system:risk-auto',requested_tenant_id::text,'code_freeze',
        'code_item:' || item_row.public_id,
        jsonb_build_object('source','risk_auto','rule_id',rule_row.id::text,
            'interception_id',interception_row.id::text,
            'before',jsonb_build_object('status',prior_status),
            'after',jsonb_build_object('status','frozen')),
        now_at,now_at,now_at
    );
    UPDATE public.interception_records SET action_taken='block', updated_at=now_at
    WHERE id=interception_row.id AND action_taken IS NULL;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='risk interception was already executed'; END IF;
    code_item_id:=item_row.id; current_status:='frozen'; risk_alert_id:=requested_alert_id;
    audit_id:=requested_audit_id; RETURN NEXT;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='risk lifecycle authority is concurrently changing';
END
$function$
"""


def _destination_is_below(owning_revision: str) -> bool:
    """Return whether this downgrade crosses the revision that owns data."""

    destination = context.get_revision_argument()
    if destination is None:
        return True
    if isinstance(destination, tuple):
        raise RuntimeError("code lifecycle downgrade requires a single linear destination")
    script = ScriptDirectory.from_config(context.config)
    try:
        return bool(tuple(script.iterate_revisions(owning_revision, destination)))
    except RangeNotAncestorError:
        return False


def _downgrade_preflight() -> None:
    op.execute(
        """
        DO $block$
        DECLARE protected_ids text;
        DECLARE bound_interception_ids text;
        BEGIN
            SELECT string_agg(id::text, ', ' ORDER BY id::text) INTO protected_ids
            FROM public.code_items WHERE status::text='frozen' AND freeze_provenance_version=1;
            IF protected_ids IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='Cannot discard version-1 frozen provenance; code item ids: ' || protected_ids;
            END IF;
            SELECT string_agg(id::text, ', ' ORDER BY id::text) INTO bound_interception_ids
            FROM public.interception_records WHERE code_item_id IS NOT NULL;
            IF bound_interception_ids IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='Cannot discard risk interception code-item evidence; interception ids: '
                        || bound_interception_ids;
            END IF;
        END
        $block$
        """
    )
    if _destination_is_below(_DELIVERY_CONTRACT_REVISION):
        op.execute(
            """
            DO $block$
            DECLARE protected_delivery_contract text;
            BEGIN
            SELECT concat_ws(', ',
                CASE WHEN EXISTS (SELECT 1 FROM public.code_batches WHERE contract_version = 1)
                     THEN 'contract-v1 batches' END,
                CASE WHEN EXISTS (SELECT 1 FROM public.code_batch_generation_receipts)
                     THEN 'generation receipts' END,
                CASE WHEN EXISTS (SELECT 1 FROM public.export_logs WHERE manifest_version IS NOT NULL)
                     THEN 'export manifests' END)
            INTO protected_delivery_contract;
            IF protected_delivery_contract <> '' THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='Cannot discard atomic code delivery contract: ' || protected_delivery_contract;
            END IF;
            END
            $block$
            """
        )
    if _destination_is_below(_DELIVERY_STATUS_REVISION):
        op.execute(
            """
            DO $block$
            DECLARE protected_delivery_batches text;
            BEGIN
            SELECT string_agg(id::text, ', ' ORDER BY id::text)
            INTO protected_delivery_batches
            FROM public.code_batches
            WHERE status::text IN ('printing', 'delivered', 'exported');
            IF protected_delivery_batches IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='Cannot discard code batch delivery state; batch ids: ' || protected_delivery_batches;
            END IF;
            END
            $block$
            """
        )


def _restore_parent_item_and_recall_guards() -> None:
    """Restore the exact behavioral surface owned by 3f91 and 2ed1."""

    op.execute(_PARENT_ITEM_GUARD_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ITEM_GUARD}() FROM PUBLIC")
    op.execute(
        f"CREATE TRIGGER trg_guard_code_item_batch_contract BEFORE INSERT OR UPDATE OR DELETE "
        f"ON public.code_items FOR EACH ROW EXECUTE FUNCTION public.{_ITEM_GUARD}()"
    )
    op.execute(_PARENT_ITEM_FINAL_GUARD_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ITEM_FINAL_GUARD}() FROM PUBLIC")
    op.execute(
        f"CREATE CONSTRAINT TRIGGER trg_enforce_code_item_parent_final_state "
        "AFTER UPDATE OF status ON public.code_items DEFERRABLE INITIALLY DEFERRED "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_ITEM_FINAL_GUARD}()"
    )
    op.execute(_PARENT_RECALL_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION public.{_RECALL_FUNCTION}() FROM PUBLIC")
    op.execute(
        f"CREATE TRIGGER trg_freeze_recalled_production_batch_codes AFTER UPDATE OF status "
        f"ON public.production_batches FOR EACH ROW EXECUTE FUNCTION public.{_RECALL_FUNCTION}()"
    )


_PARENT_ITEM_GUARD_SQL = r"""
CREATE FUNCTION public.guard_code_item_batch_contract()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $function$
DECLARE batch_row public.code_batches%ROWTYPE;
DECLARE rollout_finalized boolean;
DECLARE privileged_legacy boolean := has_parameter_privilege(session_user, 'app.bypass_rls', 'SET');
BEGIN
    IF TG_OP = 'DELETE' THEN
        SELECT * INTO batch_row FROM public.code_batches
        WHERE tenant_id=OLD.tenant_id AND id=OLD.code_batch_id FOR UPDATE;
        IF batch_row.contract_version=0 AND privileged_legacy THEN RETURN OLD; END IF;
        RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='code item deletion is forbidden';
    END IF;
    SELECT * INTO batch_row FROM public.code_batches
    WHERE tenant_id=NEW.tenant_id AND id=NEW.code_batch_id FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='code item batch is unavailable'; END IF;
    IF batch_row.contract_version=0 THEN
        SELECT EXISTS (SELECT 1 FROM public.code_delivery_contract_rollout_state
                       WHERE id=1 AND phase='finalized') INTO rollout_finalized;
        IF rollout_finalized AND NOT privileged_legacy THEN
            RAISE EXCEPTION USING ERRCODE='42501',
                MESSAGE='legacy code batch items are read-only after rollout finalization';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='INSERT' THEN
        IF batch_row.status::text <> 'generating' OR NEW.status::text <> 'created' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='code items may be created only in a generating batch';
        END IF;
        IF batch_row.code_type='paired' THEN
            IF NEW.code_type NOT IN ('outer','inner') OR NEW.pair_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='paired code item metadata is invalid';
            END IF;
        ELSIF NEW.code_type <> 'single' OR NEW.pair_id IS NOT NULL THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='single code item metadata is invalid';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.code_batch_id IS DISTINCT FROM OLD.code_batch_id
       OR NEW.public_id IS DISTINCT FROM OLD.public_id OR NEW.code_type IS DISTINCT FROM OLD.code_type
       OR NEW.pair_id IS DISTINCT FROM OLD.pair_id THEN
        RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='code item identity is immutable';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
        (OLD.status::text='created' AND NEW.status::text IN ('activated','revoked'))
        OR (OLD.status::text='activated' AND NEW.status::text IN ('bound','revoked','frozen'))
        OR (OLD.status::text='bound' AND NEW.status::text IN ('expired','revoked','frozen'))
        OR (OLD.status::text='frozen' AND NEW.status::text IN ('activated','revoked'))
    ) THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='invalid code item lifecycle transition'; END IF;
    IF OLD.status::text='created' AND NEW.status::text='activated' AND batch_row.status::text <> 'delivered' THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='code item activation requires a delivered code batch';
    END IF;
    IF OLD.status::text='activated' AND NEW.status::text='bound' AND batch_row.status::text <> 'activated' THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='code item binding requires an activated code batch';
    END IF;
    IF OLD.status::text='frozen' AND NEW.status::text='activated' AND batch_row.status::text <> 'activated' THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='code item reactivation requires an activated code batch';
    END IF;
    RETURN NEW;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='authoritative code batch is concurrently changing';
END
$function$
"""


_PARENT_ITEM_FINAL_GUARD_SQL = r"""
CREATE FUNCTION public.enforce_code_item_parent_final_state()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $function$
DECLARE parent_status text;
BEGIN
    IF OLD.status::text='created' AND NEW.status::text='activated' THEN
        SELECT batch.status::text INTO parent_status FROM public.code_batches batch
        WHERE batch.tenant_id=NEW.tenant_id AND batch.id=NEW.code_batch_id;
        IF parent_status IS DISTINCT FROM 'activated' THEN
            RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='activated code item requires its code batch to finish activated';
        END IF;
    END IF;
    RETURN NULL;
END
$function$
"""


_PARENT_RECALL_SQL = r"""
CREATE FUNCTION public.freeze_recalled_production_batch_codes()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $function$
DECLARE code_batch_row record; DECLARE code_item_row record;
BEGIN
    IF OLD.status::text <> 'active' OR NEW.status::text <> 'recalled' THEN RETURN NEW; END IF;
    FOR code_batch_row IN
        SELECT code_batch.id FROM public.code_batches code_batch
        WHERE code_batch.tenant_id=NEW.tenant_id AND code_batch.product_id=NEW.product_id
          AND code_batch.sku_id=NEW.sku_id AND code_batch.production_batch_id=NEW.id
        ORDER BY code_batch.id::text FOR UPDATE
    LOOP
        FOR code_item_row IN
            SELECT code_item.id FROM public.code_items code_item
            WHERE code_item.tenant_id=NEW.tenant_id AND code_item.code_batch_id=code_batch_row.id
              AND code_item.status::text IN ('activated','bound')
            ORDER BY code_item.id::text FOR UPDATE
        LOOP
            UPDATE public.code_items SET status='frozen',updated_at=CURRENT_TIMESTAMP WHERE id=code_item_row.id;
        END LOOP;
    END LOOP;
    RETURN NEW;
END
$function$
"""


_EXPAND_COMPAT_GUARD_SQL = r"""
CREATE FUNCTION public.populate_legacy_frozen_provenance()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public
AS $function$
BEGIN
    IF NEW.status::text = 'frozen' THEN
        IF NEW.freeze_provenance_version IS NULL THEN
            NEW.frozen_from_status := CASE
                WHEN TG_OP = 'UPDATE' AND OLD.status::text = 'bound' THEN 'bound'
                WHEN NEW.bound_at IS NOT NULL THEN 'bound'
                ELSE 'activated'
            END;
            NEW.frozen_at := COALESCE(NEW.updated_at, NEW.created_at, CURRENT_TIMESTAMP);
            NEW.freeze_provenance_version := 0;
        END IF;
    ELSIF TG_OP = 'UPDATE' AND OLD.status::text = 'frozen' THEN
        NEW.frozen_from_status := NULL;
        NEW.frozen_at := NULL;
        NEW.frozen_by := NULL;
        NEW.freeze_reason := NULL;
        NEW.freeze_provenance_version := NULL;
    END IF;
    RETURN NEW;
END
$function$
"""


def _restore_expand_compat_guard() -> None:
    op.execute(_EXPAND_COMPAT_GUARD_SQL)
    op.execute("REVOKE ALL ON FUNCTION public.populate_legacy_frozen_provenance() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_populate_legacy_frozen_provenance "
        "BEFORE INSERT OR UPDATE OF status ON public.code_items FOR EACH ROW "
        "EXECUTE FUNCTION public.populate_legacy_frozen_provenance()"
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    op.execute("DROP TRIGGER trg_populate_legacy_frozen_provenance ON public.code_items")
    op.execute("DROP FUNCTION public.populate_legacy_frozen_provenance()")
    _install_interception_guard()
    _install_item_contract_guards()
    _install_provenance_guard()
    _install_recall_freeze()
    _install_lifecycle_actor_authority()
    _protect_audit_interface()
    _install_first_scan_interface()
    _install_lifecycle_interfaces()
    if _runtime_role_exists():
        op.execute(f"REVOKE UPDATE ON TABLE public.code_items FROM {_RUNTIME_ROLE}")
        op.execute(f"REVOKE DELETE ON TABLE public.interception_records FROM {_RUNTIME_ROLE}")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _downgrade_preflight()
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    for signature in (
        "freeze_code_item_for_risk(uuid,uuid,uuid,uuid,uuid)",
        "transition_code_batch_lifecycle(uuid,uuid,uuid,uuid,text,text)",
        "transition_code_item_lifecycle(uuid,uuid,uuid,uuid,text,text)",
        "mark_code_item_first_scanned(uuid,text)",
    ):
        op.execute(f"DROP FUNCTION public.{signature}")
    op.execute(f"DROP FUNCTION public.{_AUDIT_FUNCTION}(uuid,uuid,text,text,text,jsonb)")
    op.execute(f"DROP FUNCTION public.{_LIFECYCLE_ACTOR_FUNCTION}(uuid,uuid)")
    op.execute(f"ALTER FUNCTION public.{_AUDIT_INTERNAL}(uuid,uuid,text,text,text,jsonb) RENAME TO {_AUDIT_FUNCTION}")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_AUDIT_FUNCTION}(uuid,uuid,text,text,text,jsonb) FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(
            f"GRANT EXECUTE ON FUNCTION public.{_AUDIT_FUNCTION}(uuid,uuid,text,text,text,jsonb) TO {_RUNTIME_ROLE}"
        )
        op.execute(f"GRANT UPDATE ON TABLE public.code_items TO {_RUNTIME_ROLE}")
        op.execute(f"GRANT DELETE ON TABLE public.interception_records TO {_RUNTIME_ROLE}")
    op.execute("DROP TRIGGER trg_guard_interception_code_item_binding ON public.interception_records")
    op.execute("DROP FUNCTION public.guard_interception_code_item_binding()")
    op.execute("DROP TRIGGER trg_guard_code_item_lifecycle_provenance ON public.code_items")
    op.execute("DROP FUNCTION public.guard_code_item_lifecycle_provenance()")
    op.execute("DROP TRIGGER trg_guard_code_item_batch_contract_update ON public.code_items")
    op.execute("DROP TRIGGER trg_guard_code_item_batch_contract_insert_delete ON public.code_items")
    op.execute(f"DROP FUNCTION public.{_ITEM_GUARD}()")
    op.execute("DROP TRIGGER trg_enforce_code_item_parent_final_state ON public.code_items")
    op.execute(f"DROP FUNCTION public.{_ITEM_FINAL_GUARD}()")
    op.execute("DROP TRIGGER trg_freeze_recalled_production_batch_codes ON public.production_batches")
    op.execute(f"DROP FUNCTION public.{_RECALL_FUNCTION}()")
    _restore_parent_item_and_recall_guards()
    _restore_expand_compat_guard()
