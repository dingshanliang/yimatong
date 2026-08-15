"""bind prepared exports to immutable actor authority

Revision ID: u8c3c4d5e6f7
Revises: u8c2b3c4d5e6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u8c3c4d5e6f7"
down_revision: str | Sequence[str] | None = "u8c2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION_SIGNATURE = (
    "record_prepared_export(uuid,uuid,uuid,text,text,jsonb,text,text,text,integer,text,bigint,"
    "uuid,uuid,integer,bytea,bytea,text,text)"
)
_SEED_FUNCTION_SIGNATURE = (
    "record_seed_code_export_manifest(uuid,uuid,uuid,text,integer,text,bigint,bytea,bytea,text,text)"
)
_REQUIRED_CHECK = "ck_export_logs_required_u8c_stage"
_EXPORT_POLICY = "export_logs_tenant_isolation"
_GUARDED_CONTROL = (
    "public.current_tenant_id() IS NULL "
    "AND current_setting('app.bypass_rls',true)='true' "
    "AND has_parameter_privilege(session_user,'app.bypass_rls','SET')"
)


def _runtime_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')"))
        .scalar_one()
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("SET LOCAL search_path=public,pg_catalog")

    # This is the sole short write cutover. It closes the race between the
    # coexistence delta backfill and enforcement of NOT VALID constraints.
    op.execute("LOCK TABLE public.export_logs IN SHARE ROW EXCLUSIVE MODE")
    orphan_count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM public.export_logs e WHERE NOT EXISTS "
                "(SELECT 1 FROM public.accounts a WHERE a.tenant_id=e.tenant_id AND a.id=e.account_id)"
            )
        )
        .scalar_one()
    )
    if orphan_count:
        raise RuntimeError(f"u8c3 upgrade blocked: {orphan_count} export rows have no tenant-bound actor")

    # Rows created by old application instances after u8c1 are honestly marked
    # legacy. No session or reason is inferred from an account identifier.
    op.execute(
        r"""UPDATE public.export_logs AS export
        SET reason='legacy export record; original reason unavailable',scope_snapshot='{}'::jsonb,
          idempotency_key='legacy:'||export.id::text,
          payload_digest=encode(digest(convert_to(jsonb_build_array(
            export.tenant_id,export.account_id,export.auth_session_id,export.export_type,export.resource_id,
            export.file_name,CASE
              WHEN export.file_name ILIKE '%.xlsx' THEN
                'application/vnd.openxmlformats-officedocument.'||'spreadsheetml.sheet'
              WHEN export.file_name ILIKE '%.csv' OR export.export_type='code_csv' THEN 'text/csv; charset=utf-8'
              ELSE 'application/octet-stream' END,
            export.row_count,export.status,'legacy export record; original reason unavailable','{}'::jsonb,0,
            export.code_batch_id,export.manifest_version,export.checksum_sha256,export.artifact_size_bytes,
            CASE WHEN export.artifact_ciphertext IS NULL THEN NULL
                 ELSE encode(digest(export.artifact_ciphertext,'sha256'),'hex') END,
            CASE WHEN export.artifact_nonce IS NULL THEN NULL ELSE encode(export.artifact_nonce,'hex') END,
            export.artifact_scheme,export.artifact_key_id,export.created_at
          )::text,'UTF8'),'sha256'),'hex'),
          content_type=CASE
            WHEN export.file_name ILIKE '%.xlsx' THEN
              'application/vnd.openxmlformats-officedocument.'||'spreadsheetml.sheet'
            WHEN export.file_name ILIKE '%.csv' OR export.export_type='code_csv' THEN 'text/csv; charset=utf-8'
            ELSE 'application/octet-stream' END,
          authority_version=0
        WHERE export.authority_version IS NULL"""
    )

    op.execute(
        f"""ALTER TABLE public.export_logs ADD CONSTRAINT {_REQUIRED_CHECK} CHECK (
        reason IS NOT NULL AND scope_snapshot IS NOT NULL AND idempotency_key IS NOT NULL
        AND payload_digest IS NOT NULL AND content_type IS NOT NULL AND authority_version IS NOT NULL
        ) NOT VALID"""
    )
    op.execute(
        """ALTER TABLE public.export_logs ADD CONSTRAINT ck_export_logs_authority_version_u8c
        CHECK (authority_version IN (0,1,2)) NOT VALID"""
    )
    op.execute(
        """ALTER TABLE public.export_logs ADD CONSTRAINT ck_export_logs_authoritative_shape_u8c CHECK (
        authority_version=0 OR (((authority_version=1 AND auth_session_id IS NOT NULL)
          OR (authority_version=2 AND auth_session_id IS NULL AND export_type='code_csv'))
        AND NULLIF(btrim(reason),'') IS NOT NULL
        AND NULLIF(btrim(idempotency_key),'') IS NOT NULL
        AND payload_digest ~ '^[0-9a-f]{64}$'
        AND checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND artifact_size_bytes BETWEEN 1 AND 67108864
        AND NULLIF(btrim(content_type),'') IS NOT NULL
        AND status IN ('prepared','completed'))) NOT VALID"""
    )
    op.execute(
        """ALTER TABLE public.export_logs ADD CONSTRAINT fk_export_logs_tenant_account_u8c
        FOREIGN KEY (tenant_id,account_id) REFERENCES public.accounts(tenant_id,id) NOT VALID"""
    )
    op.execute(
        """ALTER TABLE public.export_logs ADD CONSTRAINT fk_export_logs_tenant_auth_session_u8c
        FOREIGN KEY (tenant_id,auth_session_id) REFERENCES public.auth_sessions(tenant_id,id) NOT VALID"""
    )

    op.execute("ALTER TABLE public.export_logs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.export_logs FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_EXPORT_POLICY} ON public.export_logs")
    export_scope = f"(tenant_id=public.current_tenant_id() OR ({_GUARDED_CONTROL}))"
    op.execute(
        f"CREATE POLICY {_EXPORT_POLICY} ON public.export_logs USING ({export_scope}) WITH CHECK ({export_scope})"
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.guard_export_ledger_u8c() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $fn$ BEGIN
          RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='prepared export ledger is immutable';
        END $fn$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_export_ledger_u8c() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_export_ledger_u8c BEFORE UPDATE OR DELETE ON public.export_logs "
        "FOR EACH ROW EXECUTE FUNCTION public.guard_export_ledger_u8c()"
    )

    op.execute(
        r"""CREATE FUNCTION public.record_prepared_export(
          requested_tenant_id uuid,requested_auth_session_id uuid,requested_export_id uuid,
          requested_export_type text,requested_reason text,requested_scope_snapshot jsonb,
          requested_idempotency_key text,requested_file_name text,requested_content_type text,
          requested_row_count integer,requested_checksum_sha256 text,requested_file_size_bytes bigint,
          requested_resource_id uuid,requested_code_batch_id uuid,requested_manifest_version integer,
          requested_artifact_ciphertext bytea,requested_artifact_nonce bytea,
          requested_artifact_scheme text,requested_artifact_key_id text)
        RETURNS TABLE(export_id uuid,account_id uuid,replayed boolean,checksum_sha256 text,
          file_size_bytes bigint,row_count integer,status text)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE resolved_actor uuid;probed_tenant uuid;required_permission text;required_role text;
          normalized_reason text:=btrim(requested_reason);normalized_idem text:=btrim(requested_idempotency_key);
          normalized_name text:=btrim(requested_file_name);normalized_type text:=btrim(requested_content_type);
          payload text;prior public.export_logs%ROWTYPE;now_at timestamptz:=CURRENT_TIMESTAMP;
          result_status text:='prepared';
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='prepared export authority denied'; END IF;
          IF requested_export_type IN (
             'scan_events_xlsx','scan_stats_xlsx','campaign_dashboard_xlsx','regional_dashboard_xlsx') THEN
            required_permission:='analytics:view';required_role:='admin';
          ELSIF requested_export_type IN (
             'risk_dashboard_xlsx','risk_alerts_csv','risk_diversions_csv') THEN
            required_permission:='risk:read';required_role:='admin';
          ELSIF requested_export_type IN ('code_csv','code_csv_download') THEN
            required_permission:='code:export';required_role:=NULL;
          ELSIF requested_export_type='takeover_import_errors_csv' THEN
            required_permission:='takeover:prepare';required_role:=NULL;
          ELSE RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='unsupported prepared export type'; END IF;

          IF requested_export_id IS NULL OR (get_byte(uuid_send(requested_export_id),6)>>4)<>7
             OR normalized_reason IS NULL OR length(normalized_reason) NOT BETWEEN 1 AND 500
             OR normalized_idem IS NULL OR length(normalized_idem) NOT BETWEEN 1 AND 128
             OR normalized_name IS NULL OR length(normalized_name) NOT BETWEEN 1 AND 255
             OR normalized_name ~ '[/\\]' OR normalized_name ~ '[[:cntrl:]]'
             OR normalized_type IS NULL OR length(normalized_type) NOT BETWEEN 1 AND 127
             OR requested_row_count NOT BETWEEN 0 AND 50000
             OR requested_checksum_sha256 !~ '^[0-9a-f]{64}$'
             OR requested_file_size_bytes NOT BETWEEN 1 AND 67108864
             OR requested_scope_snapshot IS NULL OR jsonb_typeof(requested_scope_snapshot)<>'object'
             OR octet_length(requested_scope_snapshot::text)>8192 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid prepared export metadata'; END IF;
          IF normalized_type NOT IN (
             'text/csv; charset=utf-8',
             'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet') THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='unsupported prepared export content type'; END IF;

          IF requested_export_type='code_csv' THEN
            result_status:='completed';
            IF requested_code_batch_id IS NULL OR requested_resource_id IS DISTINCT FROM requested_code_batch_id
               OR requested_manifest_version IS NULL OR requested_manifest_version<1
               OR requested_file_size_bytes>16777216 OR requested_row_count NOT BETWEEN 1 AND 10000
               OR requested_artifact_ciphertext IS NULL
               OR octet_length(requested_artifact_ciphertext)<>requested_file_size_bytes+16
               OR requested_artifact_nonce IS NULL OR octet_length(requested_artifact_nonce)<>12
               OR requested_artifact_scheme<>'aes-256-gcm-v1'
               OR requested_artifact_key_id !~ '^[A-Za-z0-9._:-]{1,64}$' THEN
              RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid code export manifest'; END IF;
          ELSIF requested_artifact_ciphertext IS NOT NULL OR requested_artifact_nonce IS NOT NULL
             OR requested_artifact_scheme IS NOT NULL OR requested_artifact_key_id IS NOT NULL
             OR requested_manifest_version IS NOT NULL THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='ordinary export cannot contain encrypted artifact';
          END IF;
          IF requested_export_type='code_csv_download' AND (requested_code_batch_id IS NULL
             OR requested_resource_id IS DISTINCT FROM requested_code_batch_id) THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid code export download scope'; END IF;

          IF NOT pg_try_advisory_xact_lock_shared(hashtextextended(
             'auth-session:'||requested_auth_session_id::text,0)) THEN
            RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='prepared export auth session is busy'; END IF;
          SELECT s.tenant_id INTO probed_tenant FROM public.auth_sessions s WHERE s.id=requested_auth_session_id;
          IF NOT FOUND OR probed_tenant IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='prepared export auth session is missing'; END IF;
          SELECT a.id INTO resolved_actor FROM public.auth_sessions s
          JOIN public.accounts a ON a.tenant_id=s.tenant_id AND a.id=s.account_id
          JOIN public.tenants t ON t.id=s.tenant_id
          WHERE s.id=requested_auth_session_id AND s.tenant_id=requested_tenant_id
            AND s.revoked_at IS NULL AND s.expires_at>now_at AND NULLIF(btrim(s.current_refresh_jti),'') IS NOT NULL
            AND s.auth_version=a.auth_version AND a.is_active AND t.status='active' AND t.tenant_type='brand';
          IF resolved_actor IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='prepared export auth session is not live'; END IF;
          IF NOT EXISTS(SELECT 1 FROM public.account_roles ar
            JOIN public.roles r ON r.tenant_id=ar.tenant_id AND r.id=ar.role_id
            JOIN public.role_permissions rp ON rp.tenant_id=ar.tenant_id AND rp.role_id=ar.role_id
            JOIN public.permissions p ON p.tenant_id=rp.tenant_id AND p.id=rp.permission_id
            WHERE ar.tenant_id=requested_tenant_id AND ar.account_id=resolved_actor
              AND r.name IN ('admin','operator') AND (required_role IS NULL OR r.name=required_role)
              AND p.code=required_permission) THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='prepared export permission denied'; END IF;
          IF requested_code_batch_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.code_batches b
             WHERE b.tenant_id=requested_tenant_id AND b.id=requested_code_batch_id) THEN
            RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='prepared export resource is missing'; END IF;

          payload:=encode(digest(convert_to(jsonb_build_array(
            requested_tenant_id,resolved_actor,requested_auth_session_id,requested_export_type,
            requested_resource_id,normalized_name,normalized_type,requested_row_count,result_status,
            normalized_reason,requested_scope_snapshot,1,requested_code_batch_id,requested_manifest_version,
            requested_checksum_sha256,requested_file_size_bytes,
            CASE WHEN requested_artifact_ciphertext IS NULL THEN NULL
                 ELSE encode(digest(requested_artifact_ciphertext,'sha256'),'hex') END,
            CASE WHEN requested_artifact_nonce IS NULL THEN NULL ELSE encode(requested_artifact_nonce,'hex') END,
            requested_artifact_scheme,requested_artifact_key_id
          )::text,'UTF8'),'sha256'),'hex');
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'prepared-export:'||requested_tenant_id::text||':'||normalized_idem,0));
          SELECT * INTO prior FROM public.export_logs e
           WHERE e.tenant_id=requested_tenant_id AND e.idempotency_key=normalized_idem;
          IF FOUND THEN
            IF prior.payload_digest<>payload THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='prepared export idempotency payload conflicts'; END IF;
            RETURN QUERY SELECT prior.id,prior.account_id,true,prior.checksum_sha256::text,
              prior.artifact_size_bytes,prior.row_count,prior.status::text;RETURN;
          END IF;

          INSERT INTO public.export_logs(id,tenant_id,account_id,auth_session_id,export_type,resource_id,file_name,
            content_type,row_count,status,reason,scope_snapshot,idempotency_key,payload_digest,authority_version,
            code_batch_id,manifest_version,checksum_sha256,artifact_size_bytes,artifact_ciphertext,artifact_nonce,
            artifact_scheme,artifact_key_id,created_at,updated_at)
          VALUES(requested_export_id,requested_tenant_id,resolved_actor,requested_auth_session_id,requested_export_type,
            requested_resource_id,normalized_name,normalized_type,requested_row_count,result_status,normalized_reason,
            requested_scope_snapshot,normalized_idem,payload,1,requested_code_batch_id,requested_manifest_version,
            requested_checksum_sha256,requested_file_size_bytes,requested_artifact_ciphertext,requested_artifact_nonce,
            requested_artifact_scheme,requested_artifact_key_id,statement_timestamp(),statement_timestamp());
          RETURN QUERY SELECT requested_export_id,resolved_actor,false,requested_checksum_sha256,
            requested_file_size_bytes,requested_row_count,result_status;
        END $fn$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION_SIGNATURE} FROM PUBLIC")

    op.execute(
        r"""CREATE FUNCTION public.record_seed_code_export_manifest(
          requested_tenant_id uuid,requested_export_id uuid,requested_code_batch_id uuid,
          requested_file_name text,requested_row_count integer,requested_checksum_sha256 text,
          requested_file_size_bytes bigint,requested_artifact_ciphertext bytea,
          requested_artifact_nonce bytea,requested_artifact_scheme text,requested_artifact_key_id text)
        RETURNS TABLE(export_id uuid,replayed boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE batch_row public.code_batches%ROWTYPE;prior public.export_logs%ROWTYPE;payload text;
          fixed_idem text:='seed:code_csv:'||requested_code_batch_id::text;
          fixed_reason text:='trusted seed prepared code artifact';
          fixed_scope jsonb:='{"source":"trusted_seed"}'::jsonb;
        BEGIN
          IF session_user='yimatong_app'
             OR NOT has_parameter_privilege(session_user,'app.bypass_rls','SET')
             OR public.current_tenant_id() IS NOT NULL
             OR current_setting('app.bypass_rls',true)<>'true' THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='seed export authority denied'; END IF;
          IF requested_export_id IS NULL OR (get_byte(uuid_send(requested_export_id),6)>>4)<>7
             OR requested_file_name IS NULL OR btrim(requested_file_name)=''
             OR length(btrim(requested_file_name))>255 OR requested_file_name ~ '[/\\]'
             OR btrim(requested_file_name) ~ '[[:cntrl:]]'
             OR requested_row_count NOT BETWEEN 1 AND 10000
             OR requested_checksum_sha256 !~ '^[0-9a-f]{64}$'
             OR requested_file_size_bytes NOT BETWEEN 1 AND 16777216
             OR requested_artifact_ciphertext IS NULL
             OR octet_length(requested_artifact_ciphertext)<>requested_file_size_bytes+16
             OR requested_artifact_nonce IS NULL OR octet_length(requested_artifact_nonce)<>12
             OR requested_artifact_scheme<>'aes-256-gcm-v1'
             OR requested_artifact_key_id !~ '^[A-Za-z0-9._:-]{1,64}$' THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid seed export manifest'; END IF;
          SELECT * INTO batch_row FROM public.code_batches b
           WHERE b.tenant_id=requested_tenant_id AND b.id=requested_code_batch_id FOR UPDATE;
          IF NOT FOUND OR batch_row.status::text<>'completed'
             OR batch_row.expected_item_count IS DISTINCT FROM requested_row_count THEN
            RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='seed export batch is unavailable'; END IF;
          payload:=encode(digest(convert_to(jsonb_build_array(
            requested_tenant_id,batch_row.created_by,NULL,'code_csv',requested_code_batch_id,
            btrim(requested_file_name),'text/csv; charset=utf-8',requested_row_count,'completed',
            fixed_reason,fixed_scope,2,requested_code_batch_id,1,requested_checksum_sha256,
            requested_file_size_bytes,encode(digest(requested_artifact_ciphertext,'sha256'),'hex'),
            encode(requested_artifact_nonce,'hex'),requested_artifact_scheme,requested_artifact_key_id
          )::text,'UTF8'),'sha256'),'hex');
          SELECT * INTO prior FROM public.export_logs e
           WHERE e.tenant_id=requested_tenant_id AND e.idempotency_key=fixed_idem;
          IF FOUND THEN
            IF prior.payload_digest<>payload THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='seed export idempotency payload conflicts'; END IF;
            RETURN QUERY SELECT prior.id,true;RETURN;
          END IF;
          INSERT INTO public.export_logs(id,tenant_id,account_id,auth_session_id,export_type,resource_id,file_name,
            content_type,row_count,status,reason,scope_snapshot,idempotency_key,payload_digest,authority_version,
            code_batch_id,manifest_version,checksum_sha256,artifact_size_bytes,artifact_ciphertext,artifact_nonce,
            artifact_scheme,artifact_key_id,created_at,updated_at)
          VALUES(requested_export_id,requested_tenant_id,batch_row.created_by,NULL,'code_csv',requested_code_batch_id,
            btrim(requested_file_name),'text/csv; charset=utf-8',requested_row_count,'completed',fixed_reason,
            fixed_scope,fixed_idem,payload,2,requested_code_batch_id,1,requested_checksum_sha256,
            requested_file_size_bytes,requested_artifact_ciphertext,requested_artifact_nonce,
            requested_artifact_scheme,requested_artifact_key_id,statement_timestamp(),statement_timestamp());
          RETURN QUERY SELECT requested_export_id,false;
        END $fn$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_SEED_FUNCTION_SIGNATURE} FROM PUBLIC")

    if _runtime_exists():
        op.execute("REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON public.export_logs FROM yimatong_app")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION_SIGNATURE} TO yimatong_app")
        op.execute(f"REVOKE ALL ON FUNCTION public.{_SEED_FUNCTION_SIGNATURE} FROM yimatong_app")
        op.execute("REVOKE SELECT ON public.export_logs FROM yimatong_app")
        op.execute(
            "GRANT SELECT(id,tenant_id,account_id,auth_session_id,export_type,resource_id,file_name,content_type,"
            "row_count,status,reason,scope_snapshot,idempotency_key,payload_digest,authority_version,code_batch_id,"
            "manifest_version,checksum_sha256,artifact_size_bytes,created_at,updated_at) "
            "ON public.export_logs TO yimatong_app"
        )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL search_path=public,pg_catalog")
    facts = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM public.export_logs WHERE authority_version IN (1,2)"))
        .scalar_one()
    )
    if facts:
        raise RuntimeError("u8c3 downgrade blocked: immutable prepared export facts exist")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_SEED_FUNCTION_SIGNATURE}")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_FUNCTION_SIGNATURE}")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_export_ledger_u8c ON public.export_logs")
    op.execute("DROP FUNCTION IF EXISTS public.guard_export_ledger_u8c()")
    op.execute(f"DROP POLICY IF EXISTS {_EXPORT_POLICY} ON public.export_logs")
    op.execute(f"CREATE POLICY {_EXPORT_POLICY} ON public.export_logs USING (tenant_id=public.current_tenant_id())")
    if _runtime_exists():
        op.execute("GRANT INSERT,UPDATE ON public.export_logs TO yimatong_app")
        op.execute("REVOKE SELECT ON public.export_logs FROM yimatong_app")
        op.execute(
            "GRANT SELECT(id,tenant_id,account_id,export_type,resource_id,file_name,row_count,status,"
            "code_batch_id,manifest_version,checksum_sha256,artifact_size_bytes,created_at,updated_at) "
            "ON public.export_logs TO yimatong_app"
        )
    op.execute("ALTER TABLE public.export_logs DROP CONSTRAINT fk_export_logs_tenant_auth_session_u8c")
    op.execute("ALTER TABLE public.export_logs DROP CONSTRAINT fk_export_logs_tenant_account_u8c")
    op.execute("ALTER TABLE public.export_logs DROP CONSTRAINT ck_export_logs_authoritative_shape_u8c")
    op.execute("ALTER TABLE public.export_logs DROP CONSTRAINT ck_export_logs_authority_version_u8c")
    op.execute(f"ALTER TABLE public.export_logs DROP CONSTRAINT {_REQUIRED_CHECK}")
