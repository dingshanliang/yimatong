"""Finalize authoritative takeover routing, evidence, and worker interfaces.

Revision ID: t3c3d2e6f7a8
Revises: t3b2c1d5e6f7
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import context, op
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

revision: str = "t3c3d2e6f7a8"
down_revision: str | None = "t3b2c1d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TAKEOVER_ROOT_REVISION = "s0b1c2d3e4f5"
_RUNTIME_ROLE = "yimatong_app"

_VALIDATED_CONSTRAINTS = (
    ("takeover_import_jobs", "fk_takeover_import_jobs_tenant_project"),
    ("takeover_import_jobs", "fk_takeover_import_jobs_tenant_parent"),
    ("takeover_import_errors", "fk_takeover_import_errors_tenant_job"),
    ("takeover_aliases", "fk_takeover_aliases_tenant_project"),
    ("takeover_aliases", "fk_takeover_aliases_tenant_source_job"),
    ("takeover_aliases", "fk_takeover_aliases_tenant_code_item"),
    ("takeover_aliases", "fk_takeover_aliases_tenant_product"),
    ("takeover_aliases", "fk_takeover_aliases_tenant_production_batch"),
    ("takeover_domain_checks", "fk_takeover_domain_checks_tenant_project"),
    ("takeover_route_versions", "fk_takeover_routes_tenant_project"),
    ("takeover_cutover_events", "fk_takeover_events_tenant_project"),
    ("takeover_cutover_events", "fk_takeover_events_tenant_route"),
    ("takeover_observations", "fk_takeover_observations_tenant_project"),
    ("takeover_observations", "fk_takeover_observations_tenant_route"),
    ("takeover_observations", "fk_takeover_observations_tenant_event"),
    ("takeover_route_versions", "fk_takeover_routes_tenant_current_event"),
    ("takeover_projects", "fk_takeover_projects_tenant_active_route"),
    ("takeover_import_jobs", "ck_takeover_import_jobs_attempt_count"),
    ("takeover_import_jobs", "ck_takeover_import_jobs_claim_state"),
    ("takeover_observations", "ck_takeover_observations_evidence_source"),
    ("takeover_observations", "ck_takeover_observations_evidence_purpose"),
    ("takeover_observations", "ck_takeover_observations_server_probe"),
)

_LEGACY_FOREIGN_KEYS = (
    ("takeover_import_jobs", "takeover_import_jobs_project_id_fkey"),
    ("takeover_import_jobs", "takeover_import_jobs_parent_job_id_fkey"),
    ("takeover_import_errors", "takeover_import_errors_job_id_fkey"),
    ("takeover_aliases", "takeover_aliases_project_id_fkey"),
    ("takeover_aliases", "takeover_aliases_source_job_id_fkey"),
    ("takeover_aliases", "takeover_aliases_internal_code_id_fkey"),
    ("takeover_aliases", "takeover_aliases_product_id_fkey"),
    ("takeover_aliases", "takeover_aliases_production_batch_id_fkey"),
    ("takeover_domain_checks", "takeover_domain_checks_project_id_fkey"),
    ("takeover_route_versions", "takeover_route_versions_project_id_fkey"),
    ("takeover_cutover_events", "takeover_cutover_events_project_id_fkey"),
    ("takeover_cutover_events", "takeover_cutover_events_route_version_id_fkey"),
    ("takeover_observations", "takeover_observations_project_id_fkey"),
    ("takeover_observations", "takeover_observations_route_version_id_fkey"),
)


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind().exec_driver_sql(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')"
        ).scalar()
    )


def _destination_is_below(owning_revision: str) -> bool:
    destination = context.get_revision_argument()
    if destination is None:
        return True
    if isinstance(destination, tuple):
        raise RuntimeError("takeover downgrade requires a single linear destination")
    script = ScriptDirectory.from_config(context.config)
    try:
        return bool(tuple(script.iterate_revisions(owning_revision, destination)))
    except RangeNotAncestorError:
        return False


def _downgrade_preflight() -> None:
    op.execute(
        """
        DO $block$
        DECLARE protected_facts text;
        BEGIN
            SELECT concat_ws(', ',
                CASE WHEN EXISTS (
                    SELECT 1 FROM public.takeover_projects WHERE domain_verification_token IS NOT NULL
                ) OR EXISTS (
                    SELECT 1 FROM public.takeover_domain_checks
                    WHERE ownership_verified OR observed_ownership_tokens::jsonb <> '[]'::jsonb
                ) THEN 'domain ownership verification' END,
                CASE WHEN EXISTS (SELECT 1 FROM public.takeover_domain_claims) THEN 'domain claims' END,
                CASE WHEN EXISTS (
                    SELECT 1 FROM public.takeover_observations WHERE evidence_source='server_probe'
                ) THEN 'server probes' END,
                CASE WHEN EXISTS (
                    SELECT 1 FROM public.takeover_route_versions WHERE current_event_id IS NOT NULL
                ) THEN 'route epochs' END,
                CASE WHEN EXISTS (
                    SELECT 1 FROM public.takeover_import_jobs
                    WHERE attempt_count<>0 OR next_attempt_at IS NOT NULL
                       OR claim_token IS NOT NULL OR last_error_code IS NOT NULL
                ) THEN 'durable worker state' END,
                CASE WHEN EXISTS (
                    SELECT 1 FROM public.takeover_projects
                    WHERE coalesce(consumer_domain,source_domain) IS NOT NULL
                    GROUP BY lower(rtrim(btrim(coalesce(consumer_domain,source_domain)),'.'))
                    HAVING count(*)>1
                ) THEN 'duplicate prepared domains' END
            ) INTO protected_facts;
            IF protected_facts <> '' THEN
                RAISE EXCEPTION USING ERRCODE='55000',
                    MESSAGE='takeover authority downgrade blocked: ' || protected_facts;
            END IF;
        END
        $block$
        """
    )
    if _destination_is_below(_TAKEOVER_ROOT_REVISION):
        op.execute(
            """
            DO $block$
            BEGIN
                IF EXISTS (SELECT 1 FROM public.takeover_projects)
                   OR EXISTS (SELECT 1 FROM public.takeover_import_jobs)
                   OR EXISTS (SELECT 1 FROM public.takeover_import_errors)
                   OR EXISTS (SELECT 1 FROM public.takeover_aliases)
                   OR EXISTS (SELECT 1 FROM public.takeover_domain_checks)
                   OR EXISTS (SELECT 1 FROM public.takeover_route_versions)
                   OR EXISTS (SELECT 1 FROM public.takeover_cutover_events)
                   OR EXISTS (SELECT 1 FROM public.takeover_observations) THEN
                    RAISE EXCEPTION USING ERRCODE='55000',
                        MESSAGE='takeover control-plane downgrade blocked: durable takeover data exists';
                END IF;
            END
            $block$
            """
        )


def _validate_contracts() -> None:
    for table, name in _VALIDATED_CONSTRAINTS:
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")
    for table, name in _LEGACY_FOREIGN_KEYS:
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {name}")


def _create_domain_claims() -> None:
    op.execute(
        """
        CREATE TABLE public.takeover_domain_claims (
            domain_key varchar(253) PRIMARY KEY,
            tenant_id uuid NOT NULL,
            project_id uuid NOT NULL,
            route_version_id uuid NOT NULL,
            verification_kind varchar(30) NOT NULL,
            domain_check_id uuid,
            verification_event_id uuid,
            current_event_id uuid NOT NULL,
            state varchar(30) NOT NULL,
            verified_at timestamptz NOT NULL,
            claimed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_takeover_domain_claims_tenant_project UNIQUE (tenant_id,project_id),
            CONSTRAINT fk_takeover_domain_claims_tenant_project FOREIGN KEY (tenant_id,project_id)
                REFERENCES public.takeover_projects(tenant_id,id),
            CONSTRAINT fk_takeover_domain_claims_tenant_route
                FOREIGN KEY (tenant_id,project_id,route_version_id)
                REFERENCES public.takeover_route_versions(tenant_id,project_id,id),
            CONSTRAINT fk_takeover_domain_claims_tenant_check
                FOREIGN KEY (tenant_id,project_id,domain_check_id)
                REFERENCES public.takeover_domain_checks(tenant_id,project_id,id),
            CONSTRAINT fk_takeover_domain_claims_tenant_verification_event
                FOREIGN KEY (tenant_id,project_id,route_version_id,verification_event_id)
                REFERENCES public.takeover_cutover_events(tenant_id,project_id,route_version_id,id),
            CONSTRAINT fk_takeover_domain_claims_tenant_event
                FOREIGN KEY (tenant_id,project_id,route_version_id,current_event_id)
                REFERENCES public.takeover_cutover_events(tenant_id,project_id,route_version_id,id),
            CONSTRAINT ck_takeover_domain_claims_domain_key CHECK (
                domain_key=lower(rtrim(trim(domain_key),'.')) AND domain_key<>''
            ),
            CONSTRAINT ck_takeover_domain_claims_state CHECK (
                state IN ('active','completed','rolling_back','rolled_back')
            ),
            CONSTRAINT ck_takeover_domain_claims_verification CHECK (
                (verification_kind='cname_dns_tls' AND domain_check_id IS NOT NULL
                 AND verification_event_id IS NULL)
                OR (verification_kind='legacy_server_redirect' AND domain_check_id IS NULL
                    AND verification_event_id IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE public.takeover_domain_claims IS "
        "'Global public-routing authority; deliberately no RLS and no direct runtime table privileges'"
    )
    op.execute("REVOKE ALL ON TABLE public.takeover_domain_claims FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(f"REVOKE ALL ON TABLE public.takeover_domain_claims FROM {_RUNTIME_ROLE}")


def _install_helpers() -> None:
    op.execute(
        """
        CREATE FUNCTION public.canonical_takeover_domain(requested_domain text) RETURNS text
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
        RETURN lower(rtrim(btrim(requested_domain),'.'))
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.takeover_urls_equal(left_url text,right_url text) RETURNS boolean
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
        RETURN rtrim(btrim(left_url),'/')=rtrim(btrim(right_url),'/')
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.authorize_takeover_actor(
            requested_tenant_id uuid, requested_auth_session_id uuid, requested_permission text
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE resolved_actor_id uuid;
        DECLARE principal_tenant_id uuid;
        BEGIN
            IF requested_permission NOT IN ('takeover:prepare','takeover:approve','takeover:execute','takeover:rollback') THEN
                RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='unknown takeover permission';
            END IF;
            SELECT authority.actor_id::uuid INTO resolved_actor_id
            FROM public.authorize_code_lifecycle_actor(
                requested_tenant_id,requested_auth_session_id
            ) AS authority;
            SELECT auth_session.tenant_id INTO principal_tenant_id
            FROM public.auth_sessions AS auth_session
            WHERE auth_session.id=requested_auth_session_id;
            IF resolved_actor_id IS NULL OR principal_tenant_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM public.account_roles AS account_role
                JOIN public.role_permissions AS role_permission
                  ON role_permission.tenant_id=account_role.tenant_id
                 AND role_permission.role_id=account_role.role_id
                JOIN public.permissions AS permission
                  ON permission.tenant_id=role_permission.tenant_id
                 AND permission.id=role_permission.permission_id
                WHERE account_role.tenant_id=principal_tenant_id
                  AND account_role.account_id=resolved_actor_id
                  AND permission.code=requested_permission
            ) THEN
                RAISE EXCEPTION USING ERRCODE='42501',
                    MESSAGE='takeover actor lacks a live required permission';
            END IF;
            RETURN resolved_actor_id;
        END;
        $function$
        """
    )
    for signature in (
        "canonical_takeover_domain(text)",
        "takeover_urls_equal(text,text)",
        "authorize_takeover_actor(uuid,uuid,text)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _runtime_role_exists():
            op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM {_RUNTIME_ROLE}")


def _install_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION public.guard_takeover_project_domain_claim() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        BEGIN
            IF (OLD.consumer_domain,OLD.source_domain,OLD.domain_verification_token) IS DISTINCT FROM
               (NEW.consumer_domain,NEW.source_domain,NEW.domain_verification_token)
               AND EXISTS (
                   SELECT 1 FROM public.takeover_domain_claims AS claim
                   WHERE claim.tenant_id=OLD.tenant_id AND claim.project_id=OLD.id
               ) THEN
                RAISE EXCEPTION USING ERRCODE='55000',
                    MESSAGE='claimed takeover domain is immutable';
            END IF;
            RETURN NEW;
        END;
        $function$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_guard_takeover_project_domain_claim "
        "BEFORE UPDATE OF consumer_domain,source_domain,domain_verification_token ON public.takeover_projects "
        "FOR EACH ROW EXECUTE FUNCTION public.guard_takeover_project_domain_claim()"
    )
    op.execute(
        """
        CREATE FUNCTION public.guard_takeover_project_operational_state() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public
        AS $function$
        BEGIN
            IF current_user='yimatong_app'
               AND (OLD.active_route_version_id IS DISTINCT FROM NEW.active_route_version_id
                    OR OLD.status IN ('observing','completed','rolling_back','rolled_back')
                    OR NEW.status IN ('observing','completed','rolling_back','rolled_back')) THEN
                RAISE EXCEPTION USING ERRCODE='42501',
                    MESSAGE='takeover project operational state requires the transition interface';
            END IF;
            RETURN NEW;
        END;
        $function$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_guard_takeover_project_operational_state "
        "BEFORE UPDATE ON public.takeover_projects FOR EACH ROW "
        "EXECUTE FUNCTION public.guard_takeover_project_operational_state()"
    )
    op.execute(
        """
        CREATE FUNCTION public.guard_takeover_route_immutability() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        BEGIN
            IF TG_OP='DELETE' THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='takeover route deletion is forbidden';
            END IF;
            IF (OLD.id,OLD.tenant_id,OLD.project_id,OLD.version,OLD.mode,OLD.domain,
                OLD.source_url,OLD.target_url,OLD.extraction_rule::jsonb,OLD.sample_codes::jsonb,
                OLD.code_prefix,OLD.content_digest,OLD.created_by,OLD.created_at)
               IS DISTINCT FROM
               (NEW.id,NEW.tenant_id,NEW.project_id,NEW.version,NEW.mode,NEW.domain,
                NEW.source_url,NEW.target_url,NEW.extraction_rule::jsonb,NEW.sample_codes::jsonb,
                NEW.code_prefix,NEW.content_digest,NEW.created_by,NEW.created_at) THEN
                RAISE EXCEPTION USING ERRCODE='55000',
                    MESSAGE='takeover route business content is immutable';
            END IF;
            RETURN NEW;
        END;
        $function$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_guard_takeover_route_immutability "
        "BEFORE UPDATE OR DELETE ON public.takeover_route_versions "
        "FOR EACH ROW EXECUTE FUNCTION public.guard_takeover_route_immutability()"
    )
    op.execute(
        """
        CREATE FUNCTION public.guard_takeover_append_only_evidence() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        BEGIN
            RAISE EXCEPTION USING ERRCODE='55000',
                MESSAGE='takeover evidence is append-only';
        END;
        $function$
        """
    )
    for table in ("takeover_cutover_events", "takeover_observations", "takeover_domain_checks"):
        op.execute(
            f"CREATE TRIGGER trg_guard_{table}_append_only BEFORE UPDATE OR DELETE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_takeover_append_only_evidence()"
        )
    op.execute(
        """
        CREATE FUNCTION public.guard_takeover_import_claim_state() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public
        AS $function$
        BEGIN
            IF current_user='yimatong_app'
               AND ((OLD.attempt_count,OLD.claimed_at,OLD.claim_token,OLD.next_attempt_at,OLD.last_error_code)
                    IS DISTINCT FROM
                    (NEW.attempt_count,NEW.claimed_at,NEW.claim_token,NEW.next_attempt_at,NEW.last_error_code)
                    OR NEW.status IN ('processing','dead_letter')) THEN
                RAISE EXCEPTION USING ERRCODE='42501',
                    MESSAGE='takeover import claim state requires the worker interface';
            END IF;
            RETURN NEW;
        END;
        $function$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_guard_takeover_import_claim_state "
        "BEFORE UPDATE ON public.takeover_import_jobs FOR EACH ROW "
        "EXECUTE FUNCTION public.guard_takeover_import_claim_state()"
    )
    for signature in (
        "guard_takeover_project_domain_claim()",
        "guard_takeover_project_operational_state()",
        "guard_takeover_route_immutability()",
        "guard_takeover_append_only_evidence()",
        "guard_takeover_import_claim_state()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")


def _install_worker_interfaces() -> None:
    op.execute(
        """
        CREATE FUNCTION public.enqueue_takeover_import_job(
            requested_tenant_id uuid, requested_job_id uuid
        ) RETURNS text
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE job_row public.takeover_import_jobs%ROWTYPE;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
                RAISE EXCEPTION USING ERRCODE='42501',
                    MESSAGE='takeover import tenant context mismatch';
            END IF;
            SELECT * INTO job_row FROM public.takeover_import_jobs AS job
            WHERE job.tenant_id=requested_tenant_id AND job.id=requested_job_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='takeover import job is unavailable';
            END IF;
            IF job_row.status='pending' THEN RETURN 'pending'; END IF;
            IF job_row.status='failed' AND job_row.last_error_code='tenant_plan_expired' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM public.tenants AS tenant
                    WHERE tenant.id=requested_tenant_id AND tenant.status='active'
                      AND (tenant.plan_expires_at IS NULL OR tenant.plan_expires_at>CURRENT_TIMESTAMP)
                ) THEN
                    RAISE EXCEPTION USING ERRCODE='55000',
                        MESSAGE='takeover import tenant plan is not active';
                END IF;
                UPDATE public.takeover_import_jobs SET status='pending',submitted_at=CURRENT_TIMESTAMP,
                    next_attempt_at=CURRENT_TIMESTAMP,claimed_at=NULL,claim_token=NULL,last_error_code=NULL,
                    error_detail=NULL WHERE id=job_row.id;
                RETURN 'pending';
            END IF;
            IF job_row.status<>'dry_run' OR coalesce((job_row.counts->>'failed')::integer,0)<>0 THEN
                RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='takeover import job is not queueable';
            END IF;
            UPDATE public.takeover_import_jobs SET status='pending',submitted_at=CURRENT_TIMESTAMP,
                next_attempt_at=CURRENT_TIMESTAMP,claimed_at=NULL,claim_token=NULL,last_error_code=NULL,
                error_detail=NULL WHERE id=job_row.id;
            RETURN 'pending';
        END;
        $function$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.claim_takeover_import_job(
            requested_tenant_id uuid, requested_job_id uuid, requested_claim_token uuid
        ) RETURNS TABLE (job_id uuid,project_id uuid,created_by uuid,attempt_count integer)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE job_row public.takeover_import_jobs%ROWTYPE;
        DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
                RAISE EXCEPTION USING ERRCODE='42501',
                    MESSAGE='takeover import tenant context mismatch';
            END IF;
            IF requested_claim_token IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='claim token is required';
            END IF;
            SELECT * INTO job_row FROM public.takeover_import_jobs AS job
            WHERE job.tenant_id=requested_tenant_id AND job.id=requested_job_id
              AND (
                  (job.status='pending' AND (job.next_attempt_at IS NULL OR job.next_attempt_at<=now_at))
                  OR (job.status='failed' AND job.next_attempt_at<=now_at)
                  OR (job.status='processing' AND job.claimed_at<now_at-interval '5 minutes')
              )
            FOR UPDATE SKIP LOCKED;
            IF NOT FOUND THEN RETURN; END IF;
            IF job_row.attempt_count>=20 THEN
                UPDATE public.takeover_import_jobs SET status='dead_letter',claim_token=NULL,
                    claimed_at=NULL,next_attempt_at=NULL,last_error_code=coalesce(last_error_code,'attempt_limit'),
                    error_detail='background import exhausted bounded retries'
                WHERE id=job_row.id;
                RETURN;
            END IF;
            UPDATE public.takeover_import_jobs SET status='processing',
                attempt_count=job_row.attempt_count+1,claim_token=requested_claim_token,
                claimed_at=now_at,next_attempt_at=NULL,last_error_code=NULL,error_detail=NULL
            WHERE id=job_row.id;
            job_id:=job_row.id; project_id:=job_row.project_id; created_by:=job_row.created_by;
            attempt_count:=job_row.attempt_count+1;
            RETURN NEXT;
        END;
        $function$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.fail_takeover_import_job(
            requested_tenant_id uuid, requested_job_id uuid,
            requested_claim_token uuid, requested_error_code text
        ) RETURNS text
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE job_row public.takeover_import_jobs%ROWTYPE;
        DECLARE canonical_error text;
        DECLARE retry_seconds integer;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
                RAISE EXCEPTION USING ERRCODE='42501',
                    MESSAGE='takeover import tenant context mismatch';
            END IF;
            canonical_error:=left(lower(regexp_replace(coalesce(requested_error_code,''),'[^a-z0-9_.-]','','g')),50);
            IF canonical_error='' THEN
                RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='stable import error code is required';
            END IF;
            SELECT * INTO job_row FROM public.takeover_import_jobs AS job
            WHERE job.tenant_id=requested_tenant_id AND job.id=requested_job_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='takeover import job is unavailable';
            END IF;
            IF job_row.status<>'processing' OR job_row.claim_token IS DISTINCT FROM requested_claim_token THEN
                RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='takeover import claim token mismatch';
            END IF;
            IF job_row.attempt_count>=20 THEN
                UPDATE public.takeover_import_jobs SET status='dead_letter',claim_token=NULL,
                    claimed_at=NULL,next_attempt_at=NULL,last_error_code=canonical_error,
                    error_detail='background import failed: '||canonical_error
                WHERE id=job_row.id;
                RETURN 'dead_letter';
            END IF;
            retry_seconds:=least(3600,5*(2^greatest(job_row.attempt_count-1,0))::integer);
            UPDATE public.takeover_import_jobs SET status='failed',claim_token=NULL,
                claimed_at=NULL,next_attempt_at=CURRENT_TIMESTAMP+make_interval(secs=>retry_seconds),
                last_error_code=canonical_error,error_detail='background import failed: '||canonical_error
            WHERE id=job_row.id;
            RETURN 'failed';
        END;
        $function$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.complete_takeover_import_job(
            requested_tenant_id uuid, requested_job_id uuid, requested_claim_token uuid,
            requested_status text, requested_counts jsonb
        ) RETURNS text
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE job_row public.takeover_import_jobs%ROWTYPE;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
                RAISE EXCEPTION USING ERRCODE='42501',
                    MESSAGE='takeover import tenant context mismatch';
            END IF;
            IF requested_status NOT IN ('completed','partial_failed') OR requested_counts IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='invalid import completion result';
            END IF;
            SELECT * INTO job_row FROM public.takeover_import_jobs AS job
            WHERE job.tenant_id=requested_tenant_id AND job.id=requested_job_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='takeover import job is unavailable';
            END IF;
            IF job_row.status<>'processing' OR job_row.claim_token IS DISTINCT FROM requested_claim_token THEN
                RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='takeover import claim token mismatch';
            END IF;
            UPDATE public.takeover_import_jobs SET status=requested_status,counts=requested_counts,
                claim_token=NULL,claimed_at=NULL,next_attempt_at=NULL,last_error_code=NULL,
                error_detail=NULL,completed_at=CURRENT_TIMESTAMP
            WHERE id=job_row.id;
            RETURN requested_status;
        END;
        $function$
        """
    )
    signatures = (
        "enqueue_takeover_import_job(uuid,uuid)",
        "claim_takeover_import_job(uuid,uuid,uuid)",
        "fail_takeover_import_job(uuid,uuid,uuid,text)",
        "complete_takeover_import_job(uuid,uuid,uuid,text,jsonb)",
    )
    for signature in signatures:
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _runtime_role_exists():
            op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_RUNTIME_ROLE}")


def _install_domain_check_interface() -> None:
    op.execute(
        """
        CREATE FUNCTION public.record_takeover_domain_check(
            requested_tenant_id uuid, requested_auth_session_id uuid, requested_check_id uuid,
            requested_project_id uuid, requested_domain text, requested_observed_cnames jsonb,
            requested_observed_ips jsonb, requested_observed_ownership_tokens jsonb,
            requested_ttl integer, requested_tls_status text,
            requested_certificate_expires_at timestamptz, requested_status text,
            requested_failure_reason text, requested_raw_observation jsonb
        ) RETURNS TABLE (
            domain_check_id uuid,status text,ownership_verified boolean,recorded_at timestamptz
        )
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE project_row public.takeover_projects%ROWTYPE;
        DECLARE actor_id uuid;
        DECLARE expected_token text;
        DECLARE cname_verified boolean;
        DECLARE derived_status text;
        DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
            IF requested_check_id IS NULL OR requested_project_id IS NULL
               OR requested_status NOT IN ('passed','failed','pending_external')
               OR requested_tls_status NOT IN ('active','invalid','unknown')
               OR jsonb_typeof(requested_observed_cnames)<>'array'
               OR jsonb_typeof(requested_observed_ips)<>'array'
               OR jsonb_typeof(requested_observed_ownership_tokens)<>'array'
               OR jsonb_typeof(requested_raw_observation)<>'object'
               OR (requested_ttl IS NOT NULL AND requested_ttl<0)
               OR length(coalesce(requested_failure_reason,''))>500 THEN
                RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='invalid takeover domain observation';
            END IF;
            actor_id:=public.authorize_takeover_actor(
                requested_tenant_id,requested_auth_session_id,'takeover:prepare'
            );
            SELECT * INTO project_row FROM public.takeover_projects AS project
            WHERE project.tenant_id=requested_tenant_id AND project.id=requested_project_id
            FOR UPDATE NOWAIT;
            IF NOT FOUND OR project_row.mode<>'cname'
               OR public.canonical_takeover_domain(requested_domain) IS DISTINCT FROM
                  public.canonical_takeover_domain(project_row.consumer_domain) THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='takeover CNAME project is unavailable';
            END IF;
            expected_token:='yimatong-verification='||project_row.domain_verification_token;
            ownership_verified:=requested_observed_ownership_tokens @> jsonb_build_array(expected_token);
            cname_verified:=requested_observed_cnames @> jsonb_build_array(project_row.expected_cname);
            derived_status:=CASE
                WHEN ownership_verified AND cname_verified AND requested_tls_status='active'
                     AND requested_certificate_expires_at>now_at THEN 'passed'
                WHEN requested_status='pending_external' THEN 'pending_external'
                ELSE 'failed'
            END;
            INSERT INTO public.takeover_domain_checks (
                id,tenant_id,project_id,domain,expected_cname,observed_cnames,observed_ips,
                observed_ownership_tokens,ownership_verified,ttl,tls_status,certificate_expires_at,
                status,failure_reason,raw_observation,checked_at
            ) VALUES (
                requested_check_id,requested_tenant_id,requested_project_id,
                public.canonical_takeover_domain(requested_domain),project_row.expected_cname,
                requested_observed_cnames,requested_observed_ips,requested_observed_ownership_tokens,
                ownership_verified,requested_ttl,requested_tls_status,requested_certificate_expires_at,
                derived_status,requested_failure_reason,requested_raw_observation,now_at
            );
            INSERT INTO public.platform_audit_log (
                id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
            ) VALUES (
                requested_check_id,actor_id::text,requested_tenant_id::text,'takeover_domain_checked',
                'takeover_project:'||requested_project_id::text,
                jsonb_build_object('domain',public.canonical_takeover_domain(requested_domain),
                                   'status',derived_status,'tls_status',requested_tls_status,
                                   'ownership_verified',ownership_verified),
                now_at,now_at,now_at
            );
            domain_check_id:=requested_check_id; status:=derived_status; recorded_at:=now_at;
            RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='takeover domain authority is concurrently changing';
        END;
        $function$
        """
    )
    signature = (
        "record_takeover_domain_check(uuid,uuid,uuid,uuid,text,jsonb,jsonb,jsonb,integer,text,"
        "timestamp with time zone,text,text,jsonb)"
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_RUNTIME_ROLE}")


def _install_probe_interface() -> None:
    op.execute(
        """
        CREATE FUNCTION public.record_takeover_server_probe(
            requested_tenant_id uuid, requested_auth_session_id uuid,
            requested_observation_id uuid, requested_project_id uuid,
            requested_route_version_id uuid, requested_transition_event_id uuid,
            requested_checked_url text, requested_observed_target_url text,
            requested_status text, requested_latency_ms double precision,
            requested_metrics jsonb, requested_evidence_digest text
        ) RETURNS TABLE (
            observation_id uuid,target_match boolean,recommendation text,recorded_at timestamptz
        )
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE project_row public.takeover_projects%ROWTYPE;
        DECLARE route_row public.takeover_route_versions%ROWTYPE;
        DECLARE event_row public.takeover_cutover_events%ROWTYPE;
        DECLARE expected_target text;
        DECLARE sample_code text;
        DECLARE target_public_id text;
        DECLARE evidence_purpose text;
        DECLARE required_permission text;
        DECLARE extracted_code text;
        DECLARE rule_kind text;
        DECLARE query_key text;
        DECLARE status_code integer;
        DECLARE canonical_evidence text;
        DECLARE derived_evidence_digest text;
        DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
            IF requested_status NOT IN ('passed','failed')
               OR requested_checked_url IS NULL OR requested_observed_target_url IS NULL
               OR requested_evidence_digest !~ '^[0-9a-f]{64}$'
               OR requested_metrics IS NULL OR jsonb_typeof(requested_metrics)<>'object'
               OR requested_latency_ms<0 THEN
                RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='invalid server-probe evidence';
            END IF;
            SELECT * INTO event_row FROM public.takeover_cutover_events AS event
            WHERE event.tenant_id=requested_tenant_id AND event.project_id=requested_project_id
              AND event.route_version_id=requested_route_version_id
              AND event.id=requested_transition_event_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='takeover transition event is unavailable';
            END IF;
            evidence_purpose:=CASE event_row.action
                WHEN 'external_execution' THEN 'pre_cutover'
                WHEN 'cutover' THEN 'cutover'
                WHEN 'rollback_begin' THEN 'rollback'
            END;
            IF evidence_purpose IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='transition event does not accept a server probe';
            END IF;
            required_permission:=CASE WHEN evidence_purpose='rollback'
                THEN 'takeover:rollback' ELSE 'takeover:execute' END;
            PERFORM public.authorize_takeover_actor(
                requested_tenant_id,requested_auth_session_id,required_permission
            );
            SELECT * INTO project_row FROM public.takeover_projects AS project
            WHERE project.tenant_id=requested_tenant_id AND project.id=requested_project_id
            FOR UPDATE NOWAIT;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='takeover project is unavailable';
            END IF;
            SELECT * INTO route_row FROM public.takeover_route_versions AS route
            WHERE route.tenant_id=requested_tenant_id AND route.project_id=requested_project_id
              AND route.id=requested_route_version_id FOR UPDATE NOWAIT;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='takeover route is unavailable';
            END IF;
            IF route_row.current_event_id IS DISTINCT FROM requested_transition_event_id THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='server probe is not bound to the current route epoch';
            END IF;
            IF NOT public.takeover_urls_equal(requested_checked_url,route_row.source_url)
               AND NOT public.takeover_urls_equal(requested_checked_url,project_row.sample_url) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='server probe did not exercise the old entry URL';
            END IF;
            IF evidence_purpose='rollback' THEN
                expected_target:=project_row.fallback_url;
            ELSE
                expected_target:=route_row.target_url;
                SELECT value INTO sample_code FROM json_array_elements_text(route_row.sample_codes) AS item(value)
                LIMIT 1;
                rule_kind:=coalesce(project_row.url_rule->>'kind','path_tail');
                IF rule_kind='fixed' THEN
                    extracted_code:='SHARED-LINK';
                ELSIF rule_kind='query' THEN
                    query_key:=project_row.url_rule->>'key';
                    IF query_key IS NULL OR query_key='' OR position(query_key||'=' IN requested_checked_url)=0 THEN
                        RAISE EXCEPTION USING ERRCODE='23514',
                            MESSAGE='server probe URL does not satisfy the project query rule';
                    END IF;
                    extracted_code:=split_part(split_part(requested_checked_url,query_key||'=',2),'&',1);
                ELSE
                    extracted_code:=regexp_replace(split_part(requested_checked_url,'?',1),'^.*/','');
                END IF;
                extracted_code:=upper(regexp_replace(btrim(extracted_code),'[[:space:]]+',' ','g'));
                IF coalesce(project_row.url_rule->>'prefix','')<>''
                   AND extracted_code NOT LIKE upper(project_row.url_rule->>'prefix')||'%' THEN
                    RAISE EXCEPTION USING ERRCODE='23514',
                        MESSAGE='server probe URL does not satisfy the project code prefix';
                END IF;
                IF sample_code IS NOT NULL AND extracted_code IS DISTINCT FROM sample_code THEN
                    RAISE EXCEPTION USING ERRCODE='23514',
                        MESSAGE='server probe must exercise the route first sample code';
                END IF;
                sample_code:=coalesce(sample_code,extracted_code);
                IF position('{legacy_code}' IN expected_target)>0 THEN
                    expected_target:=replace(expected_target,'{legacy_code}',sample_code);
                END IF;
                IF position('{public_id}' IN expected_target)>0 THEN
                    SELECT item.public_id INTO target_public_id
                    FROM public.takeover_aliases AS alias
                    JOIN public.code_items AS item
                      ON item.tenant_id=alias.tenant_id AND item.id=alias.internal_code_id
                    WHERE alias.tenant_id=requested_tenant_id AND alias.project_id=requested_project_id
                      AND alias.normalized_code=sample_code;
                    IF target_public_id IS NULL THEN
                        RAISE EXCEPTION USING ERRCODE='23514',
                            MESSAGE='server probe target cannot be rendered from the authoritative alias';
                    END IF;
                    expected_target:=replace(expected_target,'{public_id}',target_public_id);
                END IF;
            END IF;
            target_match:=public.takeover_urls_equal(requested_observed_target_url,expected_target);
            IF coalesce(requested_metrics->>'status_code','') !~ '^[0-9]{3}$' THEN
                RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='server-probe status code is invalid';
            END IF;
            IF coalesce(requested_metrics->>'target_match','') NOT IN ('true','false') THEN
                RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='server-probe target-match fact is invalid';
            END IF;
            status_code:=(requested_metrics->>'status_code')::integer;
            IF requested_status IS DISTINCT FROM (CASE
                    WHEN status_code BETWEEN 200 AND 399 AND target_match THEN 'passed' ELSE 'failed' END)
               OR requested_metrics->>'evidence_source' IS DISTINCT FROM 'server_probe'
               OR requested_metrics->>'evidence_purpose' IS DISTINCT FROM evidence_purpose
               OR requested_metrics->>'transition_event_id' IS DISTINCT FROM event_row.id::text
               OR requested_metrics->>'checked_url' IS DISTINCT FROM requested_checked_url
               OR requested_metrics->>'observed_target' IS DISTINCT FROM requested_observed_target_url
               OR NOT public.takeover_urls_equal(requested_metrics->>'expected_target',expected_target)
               OR (requested_metrics->>'target_match')::boolean IS DISTINCT FROM target_match THEN
                RAISE EXCEPTION USING ERRCODE='22023',
                    MESSAGE='server-probe facts do not match the authoritative route evidence';
            END IF;
            canonical_evidence:='{' ||
                '"checked_url":'||to_jsonb(requested_metrics->>'checked_url')::text||',' ||
                '"evidence_purpose":'||to_jsonb(evidence_purpose)::text||',' ||
                '"evidence_source":"server_probe",' ||
                '"expected_target":'||to_jsonb(requested_metrics->>'expected_target')::text||',' ||
                '"observed_target":'||to_jsonb(requested_metrics->>'observed_target')::text||',' ||
                '"status_code":'||status_code::text||',' ||
                '"target_match":'||CASE WHEN target_match THEN 'true' ELSE 'false' END||',' ||
                '"transition_event_id":'||to_jsonb(event_row.id::text)::text||'}';
            derived_evidence_digest:=encode(public.digest(convert_to(canonical_evidence,'UTF8'),'sha256'),'hex');
            IF requested_evidence_digest IS DISTINCT FROM derived_evidence_digest
               OR requested_metrics->>'evidence_digest' IS DISTINCT FROM derived_evidence_digest THEN
                RAISE EXCEPTION USING ERRCODE='22023',
                    MESSAGE='server-probe evidence digest does not match the canonical facts';
            END IF;
            recommendation:=CASE WHEN requested_status='passed' AND target_match
                THEN 'continue' ELSE CASE WHEN evidence_purpose='rollback' THEN 'retry' ELSE 'rollback' END END;
            INSERT INTO public.takeover_observations (
                id,tenant_id,project_id,route_version_id,transition_event_id,
                checked_url,observed_target_url,evidence_source,evidence_purpose,evidence_digest,
                status,success_rate,error_rate,latency_ms,h5_reach_rate,target_match,
                metrics,recommendation,created_at
            ) VALUES (
                requested_observation_id,requested_tenant_id,requested_project_id,
                requested_route_version_id,requested_transition_event_id,
                requested_checked_url,requested_observed_target_url,'server_probe',evidence_purpose,
                requested_evidence_digest,requested_status,
                CASE WHEN requested_status='passed' THEN 1 ELSE 0 END,
                CASE WHEN requested_status='passed' THEN 0 ELSE 1 END,
                requested_latency_ms,CASE WHEN requested_status='passed' THEN 1 ELSE 0 END,
                target_match,requested_metrics,recommendation,now_at
            );
            observation_id:=requested_observation_id; recorded_at:=now_at; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',
                MESSAGE='takeover route authority is concurrently changing';
        END;
        $function$
        """
    )
    signature = "record_takeover_server_probe(uuid,uuid,uuid,uuid,uuid,uuid,text,text,text,double precision,jsonb,text)"
    op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_RUNTIME_ROLE}")


def _install_transition_interface() -> None:
    op.execute(
        """
        CREATE FUNCTION public.transition_takeover_route(
            requested_tenant_id uuid, requested_auth_session_id uuid,
            requested_event_id uuid, requested_project_id uuid,
            requested_route_version_id uuid, requested_action text,
            requested_idempotency_key text, requested_reason text
        ) RETURNS TABLE (
            route_version_id uuid,project_status text,route_status text,
            current_event_id uuid,replayed boolean,recorded_at timestamptz
        )
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE project_row public.takeover_projects%ROWTYPE;
        DECLARE route_row public.takeover_route_versions%ROWTYPE;
        DECLARE event_row public.takeover_cutover_events%ROWTYPE;
        DECLARE epoch_event public.takeover_cutover_events%ROWTYPE;
        DECLARE domain_check_row public.takeover_domain_checks%ROWTYPE;
        DECLARE existing_claim public.takeover_domain_claims%ROWTYPE;
        DECLARE actor_id uuid;
        DECLARE canonical_domain_key text;
        DECLARE required_permission text;
        DECLARE event_action text;
        DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
            IF requested_action NOT IN (
                'confirm','record_external_execution','cutover','complete','begin_rollback','finish_rollback'
            ) THEN
                RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='unknown takeover route action';
            END IF;
            IF requested_event_id IS NULL OR requested_idempotency_key IS NULL
               OR length(btrim(requested_idempotency_key)) NOT BETWEEN 1 AND 100 THEN
                RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='bounded takeover idempotency key is required';
            END IF;
            IF requested_action IN ('record_external_execution','begin_rollback')
               AND (requested_reason IS NULL OR length(btrim(requested_reason)) NOT BETWEEN 1 AND
                    CASE WHEN requested_action='record_external_execution' THEN 200 ELSE 500 END) THEN
                RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='bounded takeover transition reason is required';
            END IF;
            required_permission:=CASE
                WHEN requested_action='confirm' THEN 'takeover:approve'
                WHEN requested_action IN ('begin_rollback','finish_rollback') THEN 'takeover:rollback'
                ELSE 'takeover:execute' END;
            actor_id:=public.authorize_takeover_actor(
                requested_tenant_id,requested_auth_session_id,required_permission
            );
            event_action:=CASE requested_action
                WHEN 'confirm' THEN 'confirm'
                WHEN 'record_external_execution' THEN 'external_execution'
                WHEN 'begin_rollback' THEN 'rollback_begin'
                WHEN 'finish_rollback' THEN 'rollback_finish'
                ELSE requested_action
            END;
            SELECT * INTO project_row FROM public.takeover_projects AS project
            WHERE project.tenant_id=requested_tenant_id AND project.id=requested_project_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='takeover project is unavailable';
            END IF;
            SELECT * INTO route_row FROM public.takeover_route_versions AS route
            WHERE route.tenant_id=requested_tenant_id AND route.project_id=requested_project_id
              AND route.id=requested_route_version_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='takeover route is unavailable';
            END IF;
            -- The project lock serializes both state and the project-scoped
            -- idempotency namespace. Re-read only after acquiring it so a
            -- concurrent same-key request becomes a replay, never a 23505.
            SELECT * INTO event_row FROM public.takeover_cutover_events AS event
            WHERE event.project_id=requested_project_id
              AND event.idempotency_key=btrim(requested_idempotency_key);
            IF FOUND THEN
                IF event_row.tenant_id IS DISTINCT FROM requested_tenant_id
                   OR event_row.route_version_id IS DISTINCT FROM requested_route_version_id
                   OR event_row.action IS DISTINCT FROM event_action THEN
                    RAISE EXCEPTION USING ERRCODE='22023',
                        MESSAGE='takeover idempotency key was reused for another transition';
                END IF;
                IF requested_action='record_external_execution'
                   AND event_row.details->>'execution_reference' IS DISTINCT FROM btrim(requested_reason) THEN
                    RAISE EXCEPTION USING ERRCODE='22023',
                        MESSAGE='takeover replay payload does not match the external execution';
                END IF;
                IF requested_action='begin_rollback'
                   AND event_row.details->>'reason' IS DISTINCT FROM btrim(requested_reason) THEN
                    RAISE EXCEPTION USING ERRCODE='22023',
                        MESSAGE='takeover replay payload does not match the rollback reason';
                END IF;
                IF (requested_action='confirm' AND NOT (
                        route_row.status='confirmed' AND route_row.current_event_id=event_row.id))
                   OR (requested_action='record_external_execution' AND NOT (
                        route_row.current_event_id=event_row.id AND project_row.status='pending_external'))
                   OR (requested_action='cutover' AND NOT (
                        route_row.status='active' AND route_row.current_event_id=event_row.id
                        AND project_row.active_route_version_id=route_row.id
                        AND EXISTS (SELECT 1 FROM public.takeover_domain_claims claim
                                    WHERE claim.tenant_id=requested_tenant_id
                                      AND claim.project_id=requested_project_id
                                      AND claim.route_version_id=requested_route_version_id
                                      AND claim.current_event_id=event_row.id)))
                   OR (requested_action='complete' AND NOT (
                        project_row.status='completed' AND route_row.current_event_id=event_row.id
                        AND EXISTS (SELECT 1 FROM public.takeover_domain_claims claim
                                    WHERE claim.tenant_id=requested_tenant_id
                                      AND claim.project_id=requested_project_id
                                      AND claim.route_version_id=requested_route_version_id
                                      AND claim.current_event_id=event_row.id AND claim.state='completed')))
                   OR (requested_action='begin_rollback' AND NOT (
                        route_row.status='rolling_back' AND route_row.current_event_id=event_row.id
                        AND project_row.status='rolling_back'))
                   OR (requested_action='finish_rollback' AND NOT (
                        route_row.status='rolled_back' AND project_row.status='rolled_back'
                        AND project_row.active_route_version_id IS NULL
                        AND route_row.current_event_id=event_row.id
                        AND EXISTS (SELECT 1 FROM public.takeover_domain_claims claim
                                    WHERE claim.tenant_id=requested_tenant_id
                                      AND claim.project_id=requested_project_id
                                      AND claim.route_version_id=requested_route_version_id
                                      AND claim.current_event_id=event_row.id AND claim.state='rolled_back'))) THEN
                    RAISE EXCEPTION USING ERRCODE='55000',
                        MESSAGE='takeover idempotency event is not bound to durable transition state';
                END IF;
                route_version_id:=route_row.id; project_status:=project_row.status;
                route_status:=route_row.status; current_event_id:=route_row.current_event_id;
                replayed:=true; recorded_at:=event_row.created_at; RETURN NEXT; RETURN;
            END IF;
            canonical_domain_key:=public.canonical_takeover_domain(route_row.domain);
            IF canonical_domain_key IS NULL OR canonical_domain_key=''
               OR canonical_domain_key IS DISTINCT FROM public.canonical_takeover_domain(
                    coalesce(project_row.consumer_domain,project_row.source_domain)
               ) THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='route domain is not bound to the current project domain';
            END IF;

            IF requested_action='confirm' THEN
                IF route_row.status<>'candidate'
                   OR coalesce((project_row.readiness_snapshot->>'ready')::boolean,false) IS NOT TRUE
                   OR project_row.readiness_digest IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE='23514',
                        MESSAGE='takeover route readiness is not confirmable';
                END IF;
                INSERT INTO public.takeover_cutover_events (
                    id,tenant_id,project_id,route_version_id,action,state,idempotency_key,
                    actor_id,details,created_at
                ) VALUES (
                    requested_event_id,requested_tenant_id,requested_project_id,requested_route_version_id,
                    event_action,'confirmed',btrim(requested_idempotency_key),actor_id,
                    jsonb_build_object('readiness_digest',project_row.readiness_digest),now_at
                );
                UPDATE public.takeover_projects SET brand_confirmed_by=actor_id,brand_confirmed_at=now_at,
                    brand_confirmation_digest=readiness_digest,status='cutover_ready',updated_at=now_at
                WHERE id=project_row.id;
                UPDATE public.takeover_route_versions SET status='confirmed',
                    brand_confirmation_digest=project_row.readiness_digest,
                    readiness_snapshot=project_row.readiness_snapshot,current_event_id=requested_event_id
                WHERE id=route_row.id;
                current_event_id:=requested_event_id;

            ELSIF requested_action='record_external_execution' THEN
                IF project_row.mode<>'legacy_redirect' OR route_row.status<>'confirmed' THEN
                    RAISE EXCEPTION USING ERRCODE='23514',
                        MESSAGE='only a confirmed legacy route can record external execution';
                END IF;
                INSERT INTO public.takeover_cutover_events (
                    id,tenant_id,project_id,route_version_id,action,state,idempotency_key,
                    actor_id,details,created_at
                ) VALUES (
                    requested_event_id,requested_tenant_id,requested_project_id,requested_route_version_id,
                    event_action,'pending_probe',btrim(requested_idempotency_key),actor_id,
                    jsonb_build_object('execution_reference',btrim(requested_reason)),now_at
                );
                UPDATE public.takeover_route_versions SET current_event_id=requested_event_id
                WHERE id=route_row.id;
                UPDATE public.takeover_projects SET status='pending_external',updated_at=now_at
                WHERE id=project_row.id;
                current_event_id:=requested_event_id;

            ELSIF requested_action='cutover' THEN
                IF route_row.status<>'confirmed'
                   OR project_row.brand_confirmation_digest IS NULL
                   OR project_row.brand_confirmation_digest IS DISTINCT FROM project_row.readiness_digest THEN
                    RAISE EXCEPTION USING ERRCODE='23514',
                        MESSAGE='takeover route is not confirmed against current readiness';
                END IF;
                IF route_row.brand_confirmation_digest IS DISTINCT FROM project_row.readiness_digest THEN
                    RAISE EXCEPTION USING ERRCODE='23514',
                        MESSAGE='route confirmation is stale against project readiness';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM public.takeover_route_versions AS other
                    WHERE other.tenant_id=requested_tenant_id AND other.project_id=requested_project_id
                      AND other.id<>requested_route_version_id
                      AND other.status IN ('active','paused','rolling_back')
                ) THEN
                    RAISE EXCEPTION USING ERRCODE='23514',
                        MESSAGE='takeover project already has a live route';
                END IF;
                IF project_row.mode='cname' THEN
                    SELECT * INTO domain_check_row FROM public.takeover_domain_checks AS check_row
                    WHERE check_row.tenant_id=requested_tenant_id
                      AND check_row.project_id=requested_project_id
                      AND public.canonical_takeover_domain(check_row.domain)=canonical_domain_key
                      AND public.canonical_takeover_domain(check_row.expected_cname)=
                          public.canonical_takeover_domain(project_row.expected_cname)
                      AND check_row.observed_cnames::jsonb @> jsonb_build_array(project_row.expected_cname)
                      AND check_row.status='passed' AND check_row.tls_status='active'
                      AND check_row.ownership_verified
                      AND check_row.observed_ownership_tokens::jsonb @>
                          jsonb_build_array('yimatong-verification='||project_row.domain_verification_token)
                      AND check_row.checked_at>=now_at-interval '15 minutes'
                      AND check_row.certificate_expires_at>now_at
                    ORDER BY check_row.checked_at DESC,check_row.id::text DESC LIMIT 1;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION USING ERRCODE='23514',
                            MESSAGE='CNAME takeover domain lacks exact DNS and TLS verification';
                    END IF;
                ELSE
                    SELECT * INTO epoch_event FROM public.takeover_cutover_events AS event
                    WHERE event.tenant_id=requested_tenant_id AND event.project_id=requested_project_id
                      AND event.route_version_id=requested_route_version_id
                      AND event.id=route_row.current_event_id AND event.action='external_execution';
                    IF NOT FOUND OR NOT EXISTS (
                        SELECT 1 FROM public.takeover_observations AS observation
                        WHERE observation.tenant_id=requested_tenant_id
                          AND observation.project_id=requested_project_id
                          AND observation.route_version_id=requested_route_version_id
                          AND observation.transition_event_id=epoch_event.id
                          AND observation.evidence_source='server_probe'
                          AND observation.evidence_purpose='pre_cutover'
                          AND observation.status='passed' AND observation.target_match
                          AND observation.created_at>=now_at-interval '15 minutes'
                    ) THEN
                        RAISE EXCEPTION USING ERRCODE='23514',
                            MESSAGE='legacy takeover lacks authoritative pre-cutover redirect evidence';
                    END IF;
                END IF;
                IF NOT pg_try_advisory_xact_lock(
                    hashtextextended('takeover-domain:'||canonical_domain_key,0)
                ) THEN
                    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='takeover domain claim is concurrently changing';
                END IF;
                SELECT * INTO existing_claim FROM public.takeover_domain_claims AS claim
                WHERE claim.domain_key=canonical_domain_key FOR UPDATE;
                IF FOUND AND (existing_claim.tenant_id,existing_claim.project_id)
                   IS DISTINCT FROM (requested_tenant_id,requested_project_id) THEN
                    RAISE EXCEPTION USING ERRCODE='23505', MESSAGE='takeover public domain is already claimed';
                END IF;
                INSERT INTO public.takeover_cutover_events (
                    id,tenant_id,project_id,route_version_id,action,state,idempotency_key,
                    actor_id,details,created_at
                ) VALUES (
                    requested_event_id,requested_tenant_id,requested_project_id,requested_route_version_id,
                    event_action,'observing',btrim(requested_idempotency_key),actor_id,
                    jsonb_build_object('mode',project_row.mode,'domain_key',canonical_domain_key),now_at
                );
                UPDATE public.takeover_route_versions SET status='active',executed_by=actor_id,
                    executed_at=now_at,current_event_id=requested_event_id WHERE id=route_row.id;
                UPDATE public.takeover_projects SET active_route_version_id=route_row.id,status='observing',
                    updated_at=now_at WHERE id=project_row.id;
                UPDATE public.takeover_aliases AS alias SET status='active',updated_at=now_at
                WHERE alias.tenant_id=requested_tenant_id AND alias.project_id=requested_project_id
                  AND alias.status='staged' AND (
                    (json_array_length(route_row.sample_codes)=0 AND route_row.code_prefix IS NULL)
                    OR EXISTS (SELECT 1 FROM json_array_elements_text(route_row.sample_codes) AS sample(value)
                               WHERE sample.value=alias.normalized_code)
                    OR (route_row.code_prefix IS NOT NULL AND alias.normalized_code LIKE route_row.code_prefix||'%')
                  );
                INSERT INTO public.takeover_domain_claims (
                    domain_key,tenant_id,project_id,route_version_id,verification_kind,
                    domain_check_id,verification_event_id,current_event_id,state,verified_at,claimed_at,updated_at
                ) VALUES (
                    canonical_domain_key,requested_tenant_id,requested_project_id,requested_route_version_id,
                    CASE WHEN project_row.mode='cname' THEN 'cname_dns_tls' ELSE 'legacy_server_redirect' END,
                    CASE WHEN project_row.mode='cname' THEN domain_check_row.id END,
                    CASE WHEN project_row.mode='legacy_redirect' THEN epoch_event.id END,
                    requested_event_id,'active',
                    CASE WHEN project_row.mode='cname' THEN domain_check_row.checked_at ELSE epoch_event.created_at END,
                    now_at,now_at
                ) ON CONFLICT (domain_key) DO UPDATE SET
                    route_version_id=excluded.route_version_id,
                    verification_kind=excluded.verification_kind,
                    domain_check_id=excluded.domain_check_id,
                    verification_event_id=excluded.verification_event_id,
                    current_event_id=excluded.current_event_id,state='active',
                    verified_at=excluded.verified_at,updated_at=excluded.updated_at;
                current_event_id:=requested_event_id;

            ELSIF requested_action='complete' THEN
                SELECT * INTO epoch_event FROM public.takeover_cutover_events AS event
                WHERE event.tenant_id=requested_tenant_id AND event.project_id=requested_project_id
                  AND event.route_version_id=requested_route_version_id
                  AND event.id=route_row.current_event_id AND event.action='cutover';
                IF route_row.status<>'active' OR project_row.status<>'observing' OR NOT FOUND
                   OR NOT EXISTS (
                       SELECT 1 FROM public.takeover_observations AS observation
                       WHERE observation.tenant_id=requested_tenant_id
                         AND observation.project_id=requested_project_id
                         AND observation.route_version_id=requested_route_version_id
                         AND observation.transition_event_id=epoch_event.id
                         AND observation.evidence_source='server_probe'
                         AND observation.evidence_purpose='cutover'
                         AND observation.status='passed' AND observation.target_match
                         AND observation.created_at>=now_at-interval '15 minutes'
                         AND (public.takeover_urls_equal(observation.checked_url,route_row.source_url)
                              OR public.takeover_urls_equal(observation.checked_url,project_row.sample_url))
                   ) THEN
                    RAISE EXCEPTION USING ERRCODE='23514',
                        MESSAGE='takeover completion lacks a passed current-epoch server probe';
                END IF;
                INSERT INTO public.takeover_cutover_events (
                    id,tenant_id,project_id,route_version_id,action,state,idempotency_key,
                    actor_id,details,created_at
                ) VALUES (
                    requested_event_id,requested_tenant_id,requested_project_id,requested_route_version_id,
                    event_action,'completed',btrim(requested_idempotency_key),actor_id,
                    jsonb_build_object('cutover_event_id',epoch_event.id),now_at
                );
                UPDATE public.takeover_projects SET status='completed',updated_at=now_at WHERE id=project_row.id;
                UPDATE public.takeover_route_versions SET current_event_id=requested_event_id WHERE id=route_row.id;
                UPDATE public.takeover_domain_claims AS claim
                SET state='completed',current_event_id=requested_event_id,
                    updated_at=now_at
                WHERE claim.tenant_id=requested_tenant_id AND claim.project_id=requested_project_id
                  AND claim.route_version_id=requested_route_version_id;
                current_event_id:=requested_event_id;

            ELSIF requested_action='begin_rollback' THEN
                IF route_row.status NOT IN ('active','paused')
                   OR project_row.active_route_version_id IS DISTINCT FROM route_row.id THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='takeover route is not rollback-ready';
                END IF;
                INSERT INTO public.takeover_cutover_events (
                    id,tenant_id,project_id,route_version_id,action,state,idempotency_key,
                    actor_id,details,created_at
                ) VALUES (
                    requested_event_id,requested_tenant_id,requested_project_id,requested_route_version_id,
                    event_action,'rolling_back',btrim(requested_idempotency_key),actor_id,
                    jsonb_build_object('reason',btrim(requested_reason)),now_at
                );
                UPDATE public.takeover_route_versions SET status='rolling_back',
                    rollback_reason=btrim(requested_reason),current_event_id=requested_event_id
                WHERE id=route_row.id;
                UPDATE public.takeover_projects SET status='rolling_back',updated_at=now_at WHERE id=project_row.id;
                UPDATE public.takeover_domain_claims AS claim SET state='rolling_back',
                    current_event_id=requested_event_id,updated_at=now_at
                WHERE claim.tenant_id=requested_tenant_id AND claim.project_id=requested_project_id
                  AND claim.route_version_id=requested_route_version_id;
                current_event_id:=requested_event_id;

            ELSE
                SELECT * INTO epoch_event FROM public.takeover_cutover_events AS event
                WHERE event.tenant_id=requested_tenant_id AND event.project_id=requested_project_id
                  AND event.route_version_id=requested_route_version_id
                  AND event.id=route_row.current_event_id AND event.action='rollback_begin';
                IF route_row.status<>'rolling_back' OR project_row.status<>'rolling_back' OR NOT FOUND
                   OR NOT EXISTS (
                       SELECT 1 FROM public.takeover_observations AS observation
                       WHERE observation.tenant_id=requested_tenant_id
                         AND observation.project_id=requested_project_id
                         AND observation.route_version_id=requested_route_version_id
                         AND observation.transition_event_id=epoch_event.id
                         AND observation.evidence_source='server_probe'
                         AND observation.evidence_purpose='rollback'
                         AND observation.status='passed' AND observation.target_match
                         AND observation.created_at>=now_at-interval '15 minutes'
                         AND (public.takeover_urls_equal(observation.checked_url,route_row.source_url)
                              OR public.takeover_urls_equal(observation.checked_url,project_row.sample_url))
                         AND public.takeover_urls_equal(observation.observed_target_url,project_row.fallback_url)
                   ) THEN
                    RAISE EXCEPTION USING ERRCODE='23514',
                        MESSAGE='takeover rollback lacks a passed old-entry fallback probe';
                END IF;
                INSERT INTO public.takeover_cutover_events (
                    id,tenant_id,project_id,route_version_id,action,state,idempotency_key,
                    actor_id,details,created_at
                ) VALUES (
                    requested_event_id,requested_tenant_id,requested_project_id,requested_route_version_id,
                    event_action,'rolled_back',btrim(requested_idempotency_key),actor_id,
                    jsonb_build_object('rollback_event_id',epoch_event.id),now_at
                );
                UPDATE public.takeover_aliases SET status='staged',updated_at=now_at
                WHERE tenant_id=requested_tenant_id AND project_id=requested_project_id AND status='active';
                UPDATE public.takeover_route_versions SET status='rolled_back',current_event_id=requested_event_id
                WHERE id=route_row.id;
                UPDATE public.takeover_projects SET active_route_version_id=NULL,status='rolled_back',updated_at=now_at
                WHERE id=project_row.id;
                UPDATE public.takeover_domain_claims AS claim
                SET state='rolled_back',current_event_id=requested_event_id,
                    updated_at=now_at
                WHERE claim.tenant_id=requested_tenant_id AND claim.project_id=requested_project_id
                  AND claim.route_version_id=requested_route_version_id;
                current_event_id:=requested_event_id;
            END IF;
            SELECT status INTO project_status FROM public.takeover_projects WHERE id=requested_project_id;
            SELECT status INTO route_status FROM public.takeover_route_versions WHERE id=requested_route_version_id;
            route_version_id:=requested_route_version_id; replayed:=false; recorded_at:=now_at; RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING ERRCODE='55P03',
                MESSAGE='takeover route authority is concurrently changing';
        END;
        $function$
        """
    )
    signature = "transition_takeover_route(uuid,uuid,uuid,uuid,uuid,text,text,text)"
    op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_RUNTIME_ROLE}")


def _install_public_resolver() -> None:
    op.execute(
        """
        CREATE FUNCTION public.resolve_takeover_public_route(requested_domain text)
        RETURNS TABLE (tenant_id uuid,project_id uuid,route_version_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path=pg_catalog,public
        AS $function$
        DECLARE canonical_domain_key text;
        BEGIN
            canonical_domain_key:=public.canonical_takeover_domain(requested_domain);
            IF canonical_domain_key IS NULL OR canonical_domain_key=''
               OR length(canonical_domain_key)>253 OR canonical_domain_key !~ '^[a-z0-9.-]+$' THEN
                RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='invalid takeover public domain';
            END IF;
            RETURN QUERY
            SELECT claim.tenant_id,claim.project_id,claim.route_version_id
            FROM public.takeover_domain_claims AS claim
            JOIN public.takeover_projects AS project
              ON project.tenant_id=claim.tenant_id AND project.id=claim.project_id
            JOIN public.takeover_route_versions AS route
              ON route.tenant_id=claim.tenant_id AND route.project_id=claim.project_id
             AND route.id=claim.route_version_id
            WHERE claim.domain_key=canonical_domain_key
              AND public.canonical_takeover_domain(route.domain)=claim.domain_key
              AND route.current_event_id=claim.current_event_id
              AND (
                  (claim.state='active' AND route.status='active'
                   AND project.status='observing' AND project.active_route_version_id=route.id)
                  OR (claim.state='completed' AND route.status='active'
                      AND project.status='completed' AND project.active_route_version_id=route.id)
                  OR (claim.state='rolling_back' AND route.status='rolling_back'
                      AND project.status='rolling_back' AND project.active_route_version_id=route.id)
                  OR (claim.state='rolled_back' AND route.status='rolled_back'
                      AND project.status='rolled_back'
                      AND project.active_route_version_id IS NULL)
              );
        END;
        $function$
        """
    )
    signature = "resolve_takeover_public_route(text)"
    op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_RUNTIME_ROLE}")


def _apply_runtime_acl() -> None:
    if not _runtime_role_exists():
        return
    op.execute(
        f"REVOKE UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON TABLE "
        f"public.takeover_route_versions FROM {_RUNTIME_ROLE}"
    )
    op.execute(
        f"REVOKE UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON TABLE "
        f"public.takeover_aliases FROM {_RUNTIME_ROLE}"
    )
    op.execute(
        f"REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON TABLE "
        f"public.takeover_observations FROM {_RUNTIME_ROLE}"
    )
    for table in (
        "takeover_domain_checks",
        "takeover_import_errors",
        "takeover_import_jobs",
    ):
        op.execute(
            f"REVOKE UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON TABLE public.{table} FROM {_RUNTIME_ROLE}"
        )
    op.execute(f"REVOKE INSERT ON TABLE public.takeover_domain_checks FROM {_RUNTIME_ROLE}")
    op.execute(
        f"REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON TABLE "
        f"public.takeover_cutover_events FROM {_RUNTIME_ROLE}"
    )


def _restore_legacy_foreign_keys() -> None:
    statements = (
        "ALTER TABLE public.takeover_import_jobs ADD CONSTRAINT takeover_import_jobs_project_id_fkey "
        "FOREIGN KEY (project_id) REFERENCES public.takeover_projects(id) NOT VALID",
        "ALTER TABLE public.takeover_import_jobs ADD CONSTRAINT takeover_import_jobs_parent_job_id_fkey "
        "FOREIGN KEY (parent_job_id) REFERENCES public.takeover_import_jobs(id) NOT VALID",
        "ALTER TABLE public.takeover_import_errors ADD CONSTRAINT takeover_import_errors_job_id_fkey "
        "FOREIGN KEY (job_id) REFERENCES public.takeover_import_jobs(id) NOT VALID",
        "ALTER TABLE public.takeover_aliases ADD CONSTRAINT takeover_aliases_project_id_fkey "
        "FOREIGN KEY (project_id) REFERENCES public.takeover_projects(id) NOT VALID",
        "ALTER TABLE public.takeover_aliases ADD CONSTRAINT takeover_aliases_source_job_id_fkey "
        "FOREIGN KEY (source_job_id) REFERENCES public.takeover_import_jobs(id) NOT VALID",
        "ALTER TABLE public.takeover_aliases ADD CONSTRAINT takeover_aliases_internal_code_id_fkey "
        "FOREIGN KEY (internal_code_id) REFERENCES public.code_items(id) NOT VALID",
        "ALTER TABLE public.takeover_aliases ADD CONSTRAINT takeover_aliases_product_id_fkey "
        "FOREIGN KEY (product_id) REFERENCES public.products(id) NOT VALID",
        "ALTER TABLE public.takeover_aliases ADD CONSTRAINT takeover_aliases_production_batch_id_fkey "
        "FOREIGN KEY (production_batch_id) REFERENCES public.production_batches(id) NOT VALID",
        "ALTER TABLE public.takeover_domain_checks ADD CONSTRAINT takeover_domain_checks_project_id_fkey "
        "FOREIGN KEY (project_id) REFERENCES public.takeover_projects(id) NOT VALID",
        "ALTER TABLE public.takeover_route_versions ADD CONSTRAINT takeover_route_versions_project_id_fkey "
        "FOREIGN KEY (project_id) REFERENCES public.takeover_projects(id) NOT VALID",
        "ALTER TABLE public.takeover_cutover_events ADD CONSTRAINT takeover_cutover_events_project_id_fkey "
        "FOREIGN KEY (project_id) REFERENCES public.takeover_projects(id) NOT VALID",
        "ALTER TABLE public.takeover_cutover_events ADD CONSTRAINT takeover_cutover_events_route_version_id_fkey "
        "FOREIGN KEY (route_version_id) REFERENCES public.takeover_route_versions(id) NOT VALID",
        "ALTER TABLE public.takeover_observations ADD CONSTRAINT takeover_observations_project_id_fkey "
        "FOREIGN KEY (project_id) REFERENCES public.takeover_projects(id) NOT VALID",
        "ALTER TABLE public.takeover_observations ADD CONSTRAINT takeover_observations_route_version_id_fkey "
        "FOREIGN KEY (route_version_id) REFERENCES public.takeover_route_versions(id) NOT VALID",
    )
    for statement in statements:
        op.execute(statement)
    for table, name in _LEGACY_FOREIGN_KEYS:
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='5min'")
    _validate_contracts()
    _create_domain_claims()
    op.execute("DROP INDEX IF EXISTS public.uq_takeover_project_domain_owner")
    _install_helpers()
    _install_guards()
    _install_worker_interfaces()
    _install_domain_check_interface()
    _install_probe_interface()
    _install_transition_interface()
    _install_public_resolver()
    op.execute("DROP TRIGGER trg_populate_takeover_domain_verification_token ON public.takeover_projects")
    op.execute("DROP FUNCTION public.populate_takeover_domain_verification_token()")
    op.execute("ALTER TABLE public.takeover_import_jobs ALTER COLUMN attempt_count DROP DEFAULT")
    op.execute("ALTER TABLE public.takeover_projects ALTER COLUMN domain_verification_token DROP DEFAULT")
    op.execute("ALTER TABLE public.takeover_domain_checks ALTER COLUMN observed_ownership_tokens DROP DEFAULT")
    op.execute("ALTER TABLE public.takeover_domain_checks ALTER COLUMN ownership_verified DROP DEFAULT")
    op.execute("ALTER TABLE public.takeover_observations ALTER COLUMN evidence_source DROP DEFAULT")
    op.execute("ALTER TABLE public.takeover_observations ALTER COLUMN evidence_purpose DROP DEFAULT")
    _apply_runtime_acl()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _downgrade_preflight()
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='5min'")
    for signature in (
        "resolve_takeover_public_route(text)",
        "transition_takeover_route(uuid,uuid,uuid,uuid,uuid,text,text,text)",
        "record_takeover_server_probe(uuid,uuid,uuid,uuid,uuid,uuid,text,text,text,double precision,jsonb,text)",
        "record_takeover_domain_check(uuid,uuid,uuid,uuid,text,jsonb,jsonb,jsonb,integer,text,timestamp with time zone,text,text,jsonb)",
        "complete_takeover_import_job(uuid,uuid,uuid,text,jsonb)",
        "fail_takeover_import_job(uuid,uuid,uuid,text)",
        "claim_takeover_import_job(uuid,uuid,uuid)",
        "enqueue_takeover_import_job(uuid,uuid)",
    ):
        op.execute(f"DROP FUNCTION public.{signature}")
    op.execute("DROP TRIGGER trg_guard_takeover_import_claim_state ON public.takeover_import_jobs")
    for table in ("takeover_domain_checks", "takeover_observations", "takeover_cutover_events"):
        op.execute(f"DROP TRIGGER trg_guard_{table}_append_only ON public.{table}")
    op.execute("DROP TRIGGER trg_guard_takeover_route_immutability ON public.takeover_route_versions")
    op.execute("DROP TRIGGER trg_guard_takeover_project_operational_state ON public.takeover_projects")
    op.execute("DROP TRIGGER trg_guard_takeover_project_domain_claim ON public.takeover_projects")
    for signature in (
        "guard_takeover_import_claim_state()",
        "guard_takeover_append_only_evidence()",
        "guard_takeover_route_immutability()",
        "guard_takeover_project_operational_state()",
        "guard_takeover_project_domain_claim()",
        "authorize_takeover_actor(uuid,uuid,text)",
        "takeover_urls_equal(text,text)",
        "canonical_takeover_domain(text)",
    ):
        op.execute(f"DROP FUNCTION public.{signature}")
    op.execute(
        "CREATE UNIQUE INDEX uq_takeover_project_domain_owner ON public.takeover_projects "
        "(coalesce(consumer_domain,source_domain)) "
        "WHERE coalesce(consumer_domain,source_domain) IS NOT NULL"
    )
    op.drop_table("takeover_domain_claims")
    _restore_legacy_foreign_keys()
    op.execute(
        """
        CREATE FUNCTION public.populate_takeover_domain_verification_token() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public
        AS $function$
        BEGIN
            IF NEW.domain_verification_token IS NULL THEN
                NEW.domain_verification_token:=gen_random_uuid()::text;
            END IF;
            RETURN NEW;
        END;
        $function$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_populate_takeover_domain_verification_token "
        "BEFORE INSERT OR UPDATE OF domain_verification_token ON public.takeover_projects "
        "FOR EACH ROW EXECUTE FUNCTION public.populate_takeover_domain_verification_token()"
    )
    op.execute("REVOKE ALL ON FUNCTION public.populate_takeover_domain_verification_token() FROM PUBLIC")
    op.execute("ALTER TABLE public.takeover_import_jobs ALTER COLUMN attempt_count SET DEFAULT 0")
    op.execute("ALTER TABLE public.takeover_projects ALTER COLUMN domain_verification_token SET DEFAULT gen_random_uuid()::text")
    op.execute("ALTER TABLE public.takeover_domain_checks ALTER COLUMN observed_ownership_tokens SET DEFAULT '[]'::json")
    op.execute("ALTER TABLE public.takeover_domain_checks ALTER COLUMN ownership_verified SET DEFAULT false")
    op.execute("ALTER TABLE public.takeover_observations ALTER COLUMN evidence_source SET DEFAULT 'legacy_client'")
    op.execute("ALTER TABLE public.takeover_observations ALTER COLUMN evidence_purpose SET DEFAULT 'legacy_client'")
    if _runtime_role_exists():
        for table in (
            "takeover_aliases",
            "takeover_route_versions",
            "takeover_observations",
            "takeover_cutover_events",
            "takeover_domain_checks",
            "takeover_import_errors",
            "takeover_import_jobs",
        ):
            op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE public.{table} TO {_RUNTIME_ROLE}")
