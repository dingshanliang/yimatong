"""bind pilot fact authority

Revision ID: u9a2d3e4f5a6
Revises: u9a1c2d3e4f5
"""

from collections.abc import Sequence

from alembic import op

revision: str = "u9a2d3e4f5a6"
down_revision: str | Sequence[str] | None = "u9a1c2d3e4f5"
branch_labels = None
depends_on = None

_ASSERT = "assert_pilot_tenant_actor(uuid,uuid,text,text,uuid)"
_MATERIALIZE = "materialize_pilot_milestones(uuid,uuid,jsonb)"
_CORRECT = "append_pilot_milestone_correction(uuid,uuid,uuid,text,timestamptz,text,text,text,text)"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.create_index(
        "ix_pilot_receipts_tenant_auth_session_u09",
        "pilot_authority_receipts",
        ["actor_tenant_id", "auth_session_id"],
    )
    op.create_index(
        "ix_pilot_receipts_platform_session_u09",
        "pilot_authority_receipts",
        ["platform_auth_session_id"],
    )
    op.create_index(
        "ix_pilot_corrections_platform_session_u09",
        "pilot_milestone_corrections",
        ["platform_auth_session_id"],
    )
    op.create_unique_constraint(
        "uq_pilot_receipts_tenant_id_u09", "pilot_authority_receipts", ["tenant_id", "id"]
    )
    op.execute(
        "ALTER TABLE public.pilot_authority_receipts ADD CONSTRAINT fk_pilot_receipts_actor_account_u09 "
        "FOREIGN KEY(actor_tenant_id,actor_account_id) REFERENCES public.accounts(tenant_id,id) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.pilot_authority_receipts ADD CONSTRAINT fk_pilot_receipts_auth_session_u09 "
        "FOREIGN KEY(actor_tenant_id,auth_session_id) REFERENCES public.auth_sessions(tenant_id,id) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.pilot_authority_receipts ADD CONSTRAINT fk_pilot_receipts_platform_session_u09 "
        "FOREIGN KEY(platform_auth_session_id) REFERENCES public.platform_auth_sessions(id) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.pilot_authority_receipts ADD CONSTRAINT fk_pilot_receipts_agency_auth_u09 "
        "FOREIGN KEY(agency_authorization_id) REFERENCES public.agency_authorizations(id) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.pilot_milestone_corrections ADD CONSTRAINT fk_pilot_correction_platform_session_u09 "
        "FOREIGN KEY(platform_auth_session_id) REFERENCES public.platform_auth_sessions(id) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.retrospectives ADD CONSTRAINT fk_retrospective_completed_account_u09 "
        "FOREIGN KEY(completed_actor_tenant_id,completed_by) REFERENCES public.accounts(tenant_id,id) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.retrospectives ADD CONSTRAINT fk_retrospective_completed_session_u09 "
        "FOREIGN KEY(completed_actor_tenant_id,completed_auth_session_id) "
        "REFERENCES public.auth_sessions(tenant_id,id) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.retrospectives ADD CONSTRAINT fk_retrospective_completed_agency_u09 "
        "FOREIGN KEY(completed_agency_authorization_id) REFERENCES public.agency_authorizations(id) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.retrospectives ADD CONSTRAINT fk_retrospective_completion_receipt_u09 "
        "FOREIGN KEY(tenant_id,completion_request_id) REFERENCES public.pilot_authority_receipts(tenant_id,id) "
        "DEFERRABLE INITIALLY DEFERRED NOT VALID"
    )

    op.execute(
        r"""CREATE FUNCTION public.assert_pilot_tenant_actor(
          requested_tenant_id uuid,requested_auth_session_id uuid,requested_permission text,
          requested_scope text,requested_retrospective_id uuid DEFAULT NULL)
        RETURNS TABLE(actor_tenant_id uuid,actor_account_id uuid,agency_authorization_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE actor_tenant uuid;actor uuid;resolved_auth uuid;now_at timestamptz:=statement_timestamp();
        BEGIN
          IF session_user<>'yimatong_app' OR requested_tenant_id IS NULL OR requested_auth_session_id IS NULL
             OR requested_permission NOT IN ('analytics:view','campaign:manage')
             OR requested_scope NOT IN ('analytics','campaigns')
             OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='pilot authority denied'; END IF;
          IF NOT pg_try_advisory_xact_lock_shared(hashtextextended(
             'auth-session:'||requested_auth_session_id::text,0)) THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='pilot auth session busy'; END IF;
          SELECT s.tenant_id,s.account_id INTO actor_tenant,actor
          FROM public.auth_sessions s JOIN public.accounts a
            ON a.tenant_id=s.tenant_id AND a.id=s.account_id
          WHERE s.id=requested_auth_session_id AND s.revoked_at IS NULL AND s.expires_at>now_at
            AND s.auth_version=a.auth_version AND a.is_active FOR SHARE OF s,a;
          IF actor IS NULL OR NOT EXISTS(
            SELECT 1 FROM public.tenants target WHERE target.id=requested_tenant_id
              AND target.status::text='active' AND target.tenant_type::text='brand'
          ) OR NOT EXISTS(
            SELECT 1 FROM public.account_roles ar JOIN public.roles role
              ON role.tenant_id=ar.tenant_id AND role.id=ar.role_id
            JOIN public.role_permissions rp ON rp.tenant_id=role.tenant_id AND rp.role_id=role.id
            JOIN public.permissions permission
              ON permission.tenant_id=rp.tenant_id AND permission.id=rp.permission_id
            WHERE ar.tenant_id=actor_tenant AND ar.account_id=actor
              AND role.name IN ('admin','operator') AND permission.code=requested_permission
          ) THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='pilot authority denied'; END IF;
          IF actor_tenant<>requested_tenant_id THEN
            SELECT authz.id INTO resolved_auth FROM public.agency_authorizations authz
            WHERE authz.agency_tenant_id=actor_tenant AND authz.client_tenant_id=requested_tenant_id
              AND authz.status::text='active' AND authz.revoked_at IS NULL
              AND (authz.expires_at IS NULL OR authz.expires_at>now_at)
              AND (authz.scope::jsonb ? requested_scope
                OR (requested_scope='analytics' AND authz.scope::jsonb ? 'campaigns'))
            ORDER BY authz.granted_at DESC,authz.id DESC LIMIT 1 FOR SHARE;
            IF resolved_auth IS NULL THEN
              RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='pilot acting authority denied'; END IF;
            IF requested_retrospective_id IS NOT NULL AND NOT EXISTS(
              SELECT 1 FROM public.retrospectives retro JOIN public.ops_tasks task
                ON task.tenant_id=retro.tenant_id AND task.id=retro.ops_task_id
              WHERE retro.tenant_id=requested_tenant_id AND retro.id=requested_retrospective_id
                AND task.assigned_to=actor
            ) THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='pilot acting owner denied'; END IF;
          END IF;
          RETURN QUERY SELECT actor_tenant,actor,resolved_auth;
        END $fn$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ASSERT} FROM PUBLIC")

    op.execute(
        r"""CREATE FUNCTION public.materialize_pilot_milestones(
          requested_tenant_id uuid,requested_auth_session_id uuid,requested_ids jsonb)
        RETURNS TABLE(milestone_id uuid,milestone_type text,achieved_at timestamptz,created boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE fact record;wanted_id uuid;inserted_id uuid;digest_value text;
        BEGIN
          PERFORM public.assert_pilot_tenant_actor(
            requested_tenant_id,requested_auth_session_id,'analytics:view','analytics',NULL);
          IF requested_ids IS NULL OR jsonb_typeof(requested_ids)<>'object' THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid pilot milestone ids'; END IF;
          FOR fact IN
            SELECT * FROM (
              SELECT 'onboarding'::text type,t.created_at at,'tenant.created_at'::text source
                FROM public.tenants t WHERE t.id=requested_tenant_id
              UNION ALL SELECT 'brand_confirmed',min(l.brand_confirmed_at),'launch_releases.brand_confirmed_at'
                FROM public.launch_releases l WHERE l.tenant_id=requested_tenant_id AND l.brand_confirmed_at IS NOT NULL
              UNION ALL SELECT 'launched',min(l.launched_at),'launch_releases.launched_at'
                FROM public.launch_releases l WHERE l.tenant_id=requested_tenant_id AND l.launched_at IS NOT NULL
              UNION ALL SELECT 'first_valid_scan',min(s.created_at),'scan_events.first_valid_visit_created_at'
                FROM public.scan_events s WHERE s.tenant_id=requested_tenant_id AND s.is_valid_visit IS TRUE
              UNION ALL SELECT 'first_campaign_published',min(c.published_at),'campaigns.published_at'
                FROM public.campaigns c WHERE c.tenant_id=requested_tenant_id AND c.published_at IS NOT NULL
            ) facts WHERE facts.at IS NOT NULL
          LOOP
            BEGIN wanted_id:=(requested_ids->>fact.type)::uuid;
            EXCEPTION WHEN OTHERS THEN
              RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid pilot milestone id'; END;
            IF wanted_id IS NULL OR (get_byte(uuid_send(wanted_id),6)>>4)<>7 THEN
              RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='pilot milestone id must be UUIDv7'; END IF;
            digest_value:=encode(digest(convert_to(jsonb_build_array(
              requested_tenant_id,fact.type,fact.at,fact.source)::text,'UTF8'),'sha256'),'hex');
            inserted_id:=NULL;
            INSERT INTO public.pilot_milestones(
              id,tenant_id,milestone_type,achieved_at,source,fact_digest,authority_version,created_at,updated_at)
            VALUES(wanted_id,requested_tenant_id,fact.type,fact.at,fact.source,digest_value,1,now(),now())
            ON CONFLICT(tenant_id,milestone_type) DO NOTHING RETURNING id INTO inserted_id;
            RETURN QUERY SELECT existing.id,existing.milestone_type::text,existing.achieved_at,
              inserted_id IS NOT NULL FROM public.pilot_milestones existing
              WHERE existing.tenant_id=requested_tenant_id AND existing.milestone_type=fact.type;
          END LOOP;
        END $fn$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_MATERIALIZE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_MATERIALIZE} TO yimatong_app")

    op.execute(
        r"""CREATE FUNCTION public.append_pilot_milestone_correction(
          requested_tenant_id uuid,requested_platform_session_id uuid,requested_id uuid,
          requested_milestone_type text,requested_corrected_at timestamptz,requested_source text,
          requested_reason text,requested_idempotency_key text,requested_payload_digest text)
        RETURNS TABLE(correction_id uuid,replayed boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE principal_value text;milestone uuid;prior public.pilot_authority_receipts%ROWTYPE;
        BEGIN
          IF session_user='yimatong_app' OR requested_id IS NULL
             OR (get_byte(uuid_send(requested_id),6)>>4)<>7
             OR requested_corrected_at IS NULL OR requested_corrected_at>statement_timestamp()
             OR requested_milestone_type NOT IN
               ('onboarding','brand_confirmed','launched','first_valid_scan','first_campaign_published')
             OR btrim(COALESCE(requested_source,''))='' OR length(btrim(requested_source))>120
             OR btrim(COALESCE(requested_reason,''))='' OR length(btrim(requested_reason))>2000
             OR btrim(COALESCE(requested_idempotency_key,''))='' OR length(requested_idempotency_key)>128
             OR requested_payload_digest !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid pilot correction'; END IF;
          IF NOT pg_try_advisory_xact_lock_shared(hashtextextended(
             'platform-auth-session:'||requested_platform_session_id::text,0)) THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='platform auth session busy'; END IF;
          SELECT session.principal INTO principal_value FROM public.platform_auth_sessions session
          WHERE session.id=requested_platform_session_id AND session.revoked_at IS NULL
            AND session.expires_at>statement_timestamp() FOR SHARE;
          IF principal_value IS DISTINCT FROM 'platform-admin' OR NOT EXISTS(
            SELECT 1 FROM public.tenants tenant WHERE tenant.id=requested_tenant_id
              AND tenant.status::text='active' AND tenant.tenant_type::text='brand'
          ) THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='platform pilot correction denied'; END IF;
          SELECT * INTO prior FROM public.pilot_authority_receipts receipt
          WHERE receipt.tenant_id=requested_tenant_id AND receipt.operation='append_milestone_correction'
            AND receipt.idempotency_key=btrim(requested_idempotency_key) FOR UPDATE;
          IF FOUND THEN
            IF prior.payload_digest<>requested_payload_digest THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='pilot correction idempotency conflict'; END IF;
            RETURN QUERY SELECT prior.resource_id,true;RETURN;
          END IF;
          SELECT id INTO milestone FROM public.pilot_milestones
          WHERE tenant_id=requested_tenant_id AND milestone_type=requested_milestone_type FOR SHARE;
          IF milestone IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='pilot milestone unavailable'; END IF;
          INSERT INTO public.pilot_milestone_corrections(
            id,tenant_id,milestone_id,milestone_type,corrected_at,source,reason,corrected_by,created_at,
            request_id,actor_type,actor_principal,platform_auth_session_id,idempotency_key,payload_digest,
            authority_version)
          VALUES(requested_id,requested_tenant_id,milestone,requested_milestone_type,requested_corrected_at,
            btrim(requested_source),btrim(requested_reason),NULL,statement_timestamp(),requested_id,'platform',
            principal_value,requested_platform_session_id,btrim(requested_idempotency_key),
            requested_payload_digest,1);
          INSERT INTO public.pilot_authority_receipts(
            id,tenant_id,operation,idempotency_key,payload_digest,resource_id,actor_type,
            platform_auth_session_id,result)
          VALUES(requested_id,requested_tenant_id,'append_milestone_correction',
            btrim(requested_idempotency_key),requested_payload_digest,requested_id,'platform',
            requested_platform_session_id,json_build_object('correction_id',requested_id,'replayed',false));
          RETURN QUERY SELECT requested_id,false;
        END $fn$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_CORRECT} FROM PUBLIC")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION public.{_CORRECT}")
    op.execute(f"DROP FUNCTION public.{_MATERIALIZE}")
    op.execute(f"DROP FUNCTION public.{_ASSERT}")
    for table, constraint in (
        ("retrospectives", "fk_retrospective_completion_receipt_u09"),
        ("retrospectives", "fk_retrospective_completed_agency_u09"),
        ("retrospectives", "fk_retrospective_completed_session_u09"),
        ("retrospectives", "fk_retrospective_completed_account_u09"),
        ("pilot_milestone_corrections", "fk_pilot_correction_platform_session_u09"),
        ("pilot_authority_receipts", "fk_pilot_receipts_agency_auth_u09"),
        ("pilot_authority_receipts", "fk_pilot_receipts_platform_session_u09"),
        ("pilot_authority_receipts", "fk_pilot_receipts_auth_session_u09"),
        ("pilot_authority_receipts", "fk_pilot_receipts_actor_account_u09"),
        ("pilot_authority_receipts", "uq_pilot_receipts_tenant_id_u09"),
    ):
        op.drop_constraint(constraint, table, type_="foreignkey" if constraint.startswith("fk_") else "unique")
    op.drop_index("ix_pilot_corrections_platform_session_u09", table_name="pilot_milestone_corrections")
    op.drop_index("ix_pilot_receipts_platform_session_u09", table_name="pilot_authority_receipts")
    op.drop_index("ix_pilot_receipts_tenant_auth_session_u09", table_name="pilot_authority_receipts")
