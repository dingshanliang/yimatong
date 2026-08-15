"""cut over channel mutations to actor-bound authority

Revision ID: u7a3f4a5b6c7
Revises: u7a2e3f4a5b6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "u7a3f4a5b6c7"
down_revision: str | Sequence[str] | None = "u7a2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE = "yimatong_app"
_PUBLIC = (
    "create_channel_distributor(uuid,uuid,uuid,uuid,text,text,text,text,text,text,text)",
    "update_channel_distributor(uuid,uuid,uuid,uuid,bigint,text,text,text,text,text,text)",
    "create_channel_region(uuid,uuid,uuid,uuid,text,text,text,text,text,text,jsonb,uuid,text)",
    "update_channel_region(uuid,uuid,uuid,uuid,bigint,text,text,text,text,text,jsonb,uuid,text)",
    "create_channel_store(uuid,uuid,uuid,uuid,text,text,text,uuid,uuid,text,text)",
    "update_channel_store(uuid,uuid,uuid,uuid,bigint,text,text,uuid,uuid,text,text)",
    "archive_channel_store(uuid,uuid,uuid,uuid,bigint,text)",
    "assign_code_batch_channel(uuid,uuid,uuid,uuid,text,uuid,uuid)",
    "allocate_code_batch_channel(uuid,uuid,uuid,uuid,text,uuid,text,uuid,bigint,text)",
    "reassign_code_batch_channel(uuid,uuid,uuid,uuid,text,uuid,bigint,text,uuid,bigint,text)",
    "archive_code_batch_allocation(uuid,uuid,uuid,uuid,text,uuid,bigint,text)",
    "set_account_channel_scope(uuid,uuid,uuid,uuid,text,uuid,text,uuid)",
    "delete_account_channel_scope(uuid,uuid,uuid,uuid,bigint,text)",
    "get_my_channel_scope(uuid,uuid,text)",
)


def _role_exists() -> bool:
    return bool(op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": _ROLE}).scalar())


def _install_permissions() -> None:
    op.create_table(
        "channel_permission_backfill",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.Column("permission_code", sa.String(100), nullable=False),
        sa.Column("created_permission", sa.Boolean(), nullable=False),
        sa.Column("created_grant", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "role_id", "permission_code", name="pk_channel_permission_backfill"),
    )
    op.execute("REVOKE ALL ON public.channel_permission_backfill FROM PUBLIC")
    op.execute(
        r"""
        WITH targets AS MATERIALIZED (
          SELECT DISTINCT role.tenant_id,wanted.code
          FROM public.roles AS role
          CROSS JOIN LATERAL (VALUES ('channel:read'),('channel:manage'),('channel:allocate'),('channel:scope')) wanted(code)
          WHERE role.name IN ('admin','operator')
        ), permission_targets AS MATERIALIZED (
          SELECT target.tenant_id,target.code,
                 COALESCE(permission.id,gen_random_uuid()) AS permission_id,
                 permission.id IS NULL AS created_permission
          FROM targets AS target
          LEFT JOIN public.permissions AS permission
            ON permission.tenant_id=target.tenant_id AND permission.code=target.code
        ), desired AS (
          SELECT role.tenant_id,role.id AS role_id,target.code,target.permission_id,target.created_permission,
                 role_permission.role_id IS NULL AS created_grant
          FROM public.roles AS role
          JOIN permission_targets AS target ON target.tenant_id=role.tenant_id
          LEFT JOIN public.role_permissions AS role_permission
            ON role_permission.tenant_id=role.tenant_id AND role_permission.role_id=role.id
           AND role_permission.permission_id=target.permission_id
          WHERE role.name IN ('admin','operator')
            AND (role.name='admin' OR target.code<>'channel:scope')
        )
        INSERT INTO public.channel_permission_backfill
          (tenant_id,role_id,permission_id,permission_code,created_permission,created_grant)
        SELECT tenant_id,role_id,permission_id,code,created_permission,created_grant FROM desired
        ON CONFLICT DO NOTHING
        """
    )
    # Keep endpoint creation and FK-dependent grants in distinct statements.
    # The role_permissions integrity trigger intentionally cannot observe rows
    # written by a sibling data-modifying CTE in the same statement snapshot.
    op.execute(
        r"""
        INSERT INTO public.permissions(id,tenant_id,code,description,created_at,updated_at)
        SELECT DISTINCT ON (tenant_id,permission_code)
          permission_id,tenant_id,permission_code,'渠道权威写权限',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
        FROM public.channel_permission_backfill WHERE created_permission
        ORDER BY tenant_id,permission_code,permission_id
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        r"""
        INSERT INTO public.role_permissions(tenant_id,role_id,permission_id)
        SELECT tenant_id,role_id,permission_id
        FROM public.channel_permission_backfill WHERE created_grant
        ON CONFLICT DO NOTHING
        """
    )


def _install_internal() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.authorize_channel_actor(
          requested_tenant_id uuid,requested_auth_session_id uuid,
          requested_permission text,requested_admin_only boolean DEFAULT false
        ) RETURNS TABLE(actor_id uuid,principal_tenant_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
          IF requested_permission NOT IN ('channel:manage','channel:allocate','channel:scope') THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='channel permission is invalid';
          END IF;
          IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='channel tenant context mismatch';
          END IF;
          PERFORM pg_advisory_xact_lock_shared(hashtextextended('auth-session:'||requested_auth_session_id::text,0));
          SELECT account.id,session.tenant_id INTO actor_id,principal_tenant_id
          FROM public.auth_sessions session
          JOIN public.accounts account ON account.tenant_id=session.tenant_id AND account.id=session.account_id
          JOIN public.tenants tenant ON tenant.id=session.tenant_id
          WHERE session.id=requested_auth_session_id AND session.tenant_id=requested_tenant_id
            AND session.revoked_at IS NULL AND session.expires_at>now_at
            AND session.auth_version=account.auth_version AND account.is_active AND tenant.status='active';
          IF actor_id IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='channel auth session is not live';
          END IF;
          IF requested_admin_only AND NOT EXISTS(
            SELECT 1 FROM public.account_roles ar JOIN public.roles role
              ON role.tenant_id=ar.tenant_id AND role.id=ar.role_id
            WHERE ar.tenant_id=requested_tenant_id AND ar.account_id=actor_id AND role.name='admin'
          ) THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='channel admin role required';
          END IF;
          IF NOT EXISTS(
            SELECT 1 FROM public.account_roles ar
            JOIN public.role_permissions rp ON rp.tenant_id=ar.tenant_id AND rp.role_id=ar.role_id
            JOIN public.permissions permission
              ON permission.tenant_id=rp.tenant_id AND permission.id=rp.permission_id
            WHERE ar.tenant_id=requested_tenant_id AND ar.account_id=actor_id
              AND permission.code=requested_permission
          ) THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='channel permission denied';
          END IF;
          RETURN NEXT;
        END $fn$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.validate_channel_target(
          requested_tenant_id uuid,requested_target_type text,requested_target_id uuid
        ) RETURNS TABLE(distributor_id uuid,region_id uuid,store_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE resolved_distributor_id uuid;DECLARE resolved_region_id uuid;DECLARE resolved_store_id uuid;
        BEGIN
          IF requested_target_type='distributor' THEN
            SELECT d.id INTO resolved_distributor_id FROM public.distributors d
            WHERE d.tenant_id=requested_tenant_id AND d.id=requested_target_id AND d.status='active' FOR SHARE NOWAIT;
          ELSIF requested_target_type='region' THEN
            SELECT r.distributor_id,r.id INTO resolved_distributor_id,resolved_region_id FROM public.regions r
            WHERE r.tenant_id=requested_tenant_id AND r.id=requested_target_id AND r.status='active' FOR SHARE NOWAIT;
          ELSIF requested_target_type='store' THEN
            SELECT s.distributor_id,s.region_id,s.id INTO resolved_distributor_id,resolved_region_id,resolved_store_id FROM public.stores s
            WHERE s.tenant_id=requested_tenant_id AND s.id=requested_target_id AND s.status='active' FOR SHARE NOWAIT;
          ELSE
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='channel target type is invalid';
          END IF;
          IF COALESCE(resolved_store_id,resolved_region_id,resolved_distributor_id) IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='channel target is unavailable';
          END IF;
          IF resolved_region_id IS NOT NULL AND EXISTS(
            SELECT 1 FROM public.regions r WHERE r.tenant_id=requested_tenant_id AND r.id=resolved_region_id
              AND r.distributor_id IS DISTINCT FROM resolved_distributor_id
          ) THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='channel hierarchy is inconsistent';
          END IF;
          distributor_id:=resolved_distributor_id;region_id:=resolved_region_id;store_id:=resolved_store_id;
          RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN
          RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='channel target is busy';
        END $fn$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.mutate_channel_node(
          requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
          requested_action text,requested_resource_type text,requested_resource_id uuid,
          requested_expected_version bigint,requested_idempotency_key text,requested_payload jsonb
        ) RETURNS TABLE(resource_id uuid,version bigint,status text,replayed boolean,actor_id uuid,recorded_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE resolved_actor uuid; DECLARE digest_value text; DECLARE digest_payload jsonb;
        DECLARE existing public.channel_action_receipts%ROWTYPE; DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        DECLARE target_distributor uuid; DECLARE target_region uuid; DECLARE resolved_code text;
        BEGIN
          SELECT authority.actor_id INTO resolved_actor FROM public.authorize_channel_actor(
            requested_tenant_id,requested_auth_session_id,'channel:manage',false
          ) authority;
          PERFORM pg_advisory_xact_lock(hashtextextended('channel-idem:'||requested_tenant_id::text||':'||requested_action||':'||requested_idempotency_key,0));
          IF requested_audit_id IS NULL OR requested_resource_id IS NULL
             OR NULLIF(trim(requested_idempotency_key),'') IS NULL OR length(requested_idempotency_key)>128 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='channel mutation identity is invalid';
          END IF;
          IF requested_action LIKE 'create_%' AND NULLIF(trim(requested_payload->>'code'),'') IS NULL THEN
            requested_payload:=jsonb_set(requested_payload,'{code}','null'::jsonb,true);
          END IF;
          -- Idempotency binds the caller's business intent, not values generated for
          -- this execution.  Create resource/audit UUIDs are deliberately outside
          -- requested_payload, and randomized phone ciphertext must likewise not
          -- make an otherwise exact retry conflict.  The deterministic phone hash
          -- remains bound.  Updates additionally bind the stable target and the
          -- optimistic-lock version so a key cannot be reused for another row/version.
          IF requested_action LIKE 'create_%' THEN
            digest_payload:=requested_payload-'phone_ciphertext';
          ELSE
            digest_payload:=jsonb_build_object(
              'resource_id',requested_resource_id,
              'expected_version',requested_expected_version,
              'payload',requested_payload-'phone_ciphertext'
            );
          END IF;
          digest_value:=encode(digest(convert_to(digest_payload::text,'UTF8'),'sha256'),'hex');
          SELECT * INTO existing FROM public.channel_action_receipts r
          WHERE r.tenant_id=requested_tenant_id AND r.action=requested_action
            AND r.idempotency_key=requested_idempotency_key FOR SHARE;
          IF FOUND THEN
            IF existing.payload_digest<>digest_value THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='channel idempotency payload conflicts';
            END IF;
            resource_id:=existing.resource_id;version:=existing.resource_version;status:=existing.status;
            replayed:=true;actor_id:=existing.actor_id;recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
          END IF;
          IF requested_action LIKE 'create_%' THEN
            version:=1;
            resolved_code:=NULLIF(trim(requested_payload->>'code'),'');
            IF resolved_code IS NULL THEN
              resolved_code:=CASE requested_resource_type
                WHEN 'distributor' THEN 'DIST-'
                WHEN 'region' THEN 'REG-'
                WHEN 'store' THEN 'STORE-'
                ELSE ''
              END||upper(replace(requested_resource_id::text,'-',''));
            END IF;
            IF requested_resource_type='distributor' THEN
              INSERT INTO public.distributors(id,tenant_id,name,code,contact_name,contact_phone_encrypted,
                contact_phone_hash,contact_phone_recovery_state,status,version,created_at,updated_at)
              VALUES(requested_resource_id,requested_tenant_id,trim(requested_payload->>'name'),resolved_code,
                NULLIF(trim(requested_payload->>'contact_name'),''),NULLIF(requested_payload->>'phone_ciphertext',''),
                NULLIF(requested_payload->>'phone_hash',''),CASE WHEN NULLIF(requested_payload->>'phone_ciphertext','') IS NULL
                THEN 'absent' ELSE 'encrypted' END,requested_payload->>'status',1,now_at,now_at);
            ELSIF requested_resource_type='region' THEN
              target_distributor:=NULLIF(requested_payload->>'distributor_id','')::uuid;
              IF target_distributor IS NOT NULL THEN PERFORM * FROM public.validate_channel_target(requested_tenant_id,'distributor',target_distributor); END IF;
              INSERT INTO public.regions(id,tenant_id,name,code,province,city,coverage_type,coverage_areas,
                distributor_id,status,version,created_at,updated_at)
              VALUES(requested_resource_id,requested_tenant_id,trim(requested_payload->>'name'),resolved_code,
                NULLIF(trim(requested_payload->>'province'),''),NULLIF(trim(requested_payload->>'city'),''),
                requested_payload->>'coverage_type',NULLIF(requested_payload->'coverage_areas','null'::jsonb),target_distributor,
                requested_payload->>'status',1,now_at,now_at);
            ELSIF requested_resource_type='store' THEN
              target_region:=NULLIF(requested_payload->>'region_id','')::uuid;
              target_distributor:=NULLIF(requested_payload->>'distributor_id','')::uuid;
              IF target_region IS NOT NULL THEN
                PERFORM r.id FROM public.regions r WHERE r.tenant_id=requested_tenant_id AND r.id=target_region
                  AND (r.distributor_id IS NULL OR r.distributor_id IS NOT DISTINCT FROM target_distributor) FOR SHARE NOWAIT;
                IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='store hierarchy is inconsistent'; END IF;
              END IF;
              IF target_distributor IS NOT NULL THEN PERFORM * FROM public.validate_channel_target(requested_tenant_id,'distributor',target_distributor); END IF;
              INSERT INTO public.stores(id,tenant_id,name,code,region_id,distributor_id,address,status,version,created_at,updated_at)
              VALUES(requested_resource_id,requested_tenant_id,trim(requested_payload->>'name'),resolved_code,
                target_region,target_distributor,NULLIF(trim(requested_payload->>'address'),''),requested_payload->>'status',1,now_at,now_at);
            ELSE RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='channel resource type is invalid'; END IF;
          ELSE
            IF requested_expected_version IS NULL OR requested_expected_version<1 THEN
              RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='channel expected version is invalid';
            END IF;
            IF requested_resource_type='distributor' THEN
              UPDATE public.distributors d SET name=trim(requested_payload->>'name'),
                contact_name=NULLIF(trim(requested_payload->>'contact_name'),''),
                contact_phone_encrypted=NULLIF(requested_payload->>'phone_ciphertext',''),
                contact_phone_hash=NULLIF(requested_payload->>'phone_hash',''),
                contact_phone_recovery_state=CASE WHEN NULLIF(requested_payload->>'phone_ciphertext','') IS NULL THEN 'absent' ELSE 'encrypted' END,
                status=requested_payload->>'status',version=d.version+1,updated_at=now_at
              WHERE d.tenant_id=requested_tenant_id AND d.id=requested_resource_id AND d.version=requested_expected_version
              RETURNING d.version,d.status INTO version,status;
            ELSIF requested_resource_type='region' THEN
              target_distributor:=NULLIF(requested_payload->>'distributor_id','')::uuid;
              IF target_distributor IS NOT NULL THEN PERFORM * FROM public.validate_channel_target(requested_tenant_id,'distributor',target_distributor); END IF;
              UPDATE public.regions r SET name=trim(requested_payload->>'name'),province=NULLIF(trim(requested_payload->>'province'),''),
                city=NULLIF(trim(requested_payload->>'city'),''),coverage_type=requested_payload->>'coverage_type',
                coverage_areas=NULLIF(requested_payload->'coverage_areas','null'::jsonb),distributor_id=target_distributor,
                status=requested_payload->>'status',version=r.version+1,updated_at=now_at
              WHERE r.tenant_id=requested_tenant_id AND r.id=requested_resource_id AND r.version=requested_expected_version
              RETURNING r.version,r.status INTO version,status;
            ELSIF requested_resource_type='store' THEN
              IF requested_action='archive_store' THEN
                UPDATE public.stores s SET status='inactive',version=s.version+1,updated_at=now_at
                WHERE s.tenant_id=requested_tenant_id AND s.id=requested_resource_id AND s.version=requested_expected_version
                RETURNING s.version,s.status INTO version,status;
              ELSE
                target_region:=NULLIF(requested_payload->>'region_id','')::uuid;
                target_distributor:=NULLIF(requested_payload->>'distributor_id','')::uuid;
                IF target_region IS NOT NULL THEN
                  PERFORM r.id FROM public.regions r WHERE r.tenant_id=requested_tenant_id AND r.id=target_region
                    AND (r.distributor_id IS NULL OR r.distributor_id IS NOT DISTINCT FROM target_distributor) FOR SHARE NOWAIT;
                  IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='store hierarchy is inconsistent'; END IF;
                END IF;
                UPDATE public.stores s SET name=trim(requested_payload->>'name'),region_id=target_region,
                  distributor_id=target_distributor,address=NULLIF(trim(requested_payload->>'address'),''),
                  status=requested_payload->>'status',version=s.version+1,updated_at=now_at
                WHERE s.tenant_id=requested_tenant_id AND s.id=requested_resource_id AND s.version=requested_expected_version
                RETURNING s.version,s.status INTO version,status;
              END IF;
            END IF;
            IF version IS NULL THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='channel version conflict'; END IF;
          END IF;
          resource_id:=requested_resource_id; status:=COALESCE(status,requested_payload->>'status','active');
          replayed:=false;actor_id:=resolved_actor;recorded_at:=now_at;
          INSERT INTO public.channel_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,
            resource_type,resource_id,resource_version,status,actor_id,actor_tenant_id,audit_id,result,recorded_at)
          VALUES(requested_audit_id,requested_tenant_id,requested_action,requested_idempotency_key,digest_value,
            requested_resource_type,resource_id,version,status,resolved_actor,requested_tenant_id,requested_audit_id,
            jsonb_build_object('resource_id',resource_id,'version',version,'status',status),now_at);
          INSERT INTO public.platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at)
          VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,requested_action,
            requested_resource_type||':'||resource_id::text,jsonb_build_object('version',version,'result','success'),
            now_at,now_at,now_at);
          RETURN NEXT;
        EXCEPTION WHEN lock_not_available THEN RAISE EXCEPTION USING ERRCODE='55P03',MESSAGE='channel resource is busy';
        END $fn$
        """
    )


def _install_node_wrappers() -> None:
    common = "RETURNS TABLE(resource_id uuid,version bigint,status text,replayed boolean,actor_id uuid,recorded_at timestamptz) LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public"
    wrappers = (
        (
            "create_channel_distributor(requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,requested_id uuid,requested_idem text,requested_name text,requested_code text,requested_contact_name text,requested_phone_ciphertext text,requested_phone_hash text,requested_status text)",
            "SELECT * FROM public.mutate_channel_node(requested_tenant_id,requested_auth_session_id,requested_audit_id,'create_distributor','distributor',requested_id,NULL,requested_idem,jsonb_build_object('name',requested_name,'code',requested_code,'contact_name',requested_contact_name,'phone_ciphertext',requested_phone_ciphertext,'phone_hash',requested_phone_hash,'status',requested_status))",
        ),
        (
            "update_channel_distributor(requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,requested_id uuid,requested_expected_version bigint,requested_idem text,requested_name text,requested_contact_name text,requested_phone_ciphertext text,requested_phone_hash text,requested_status text)",
            "SELECT * FROM public.mutate_channel_node(requested_tenant_id,requested_auth_session_id,requested_audit_id,'update_distributor','distributor',requested_id,requested_expected_version,requested_idem,jsonb_build_object('name',requested_name,'contact_name',requested_contact_name,'phone_ciphertext',requested_phone_ciphertext,'phone_hash',requested_phone_hash,'status',requested_status))",
        ),
        (
            "create_channel_region(requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,requested_id uuid,requested_idem text,requested_name text,requested_code text,requested_province text,requested_city text,requested_coverage_type text,requested_coverage_areas jsonb,requested_distributor_id uuid,requested_status text)",
            "SELECT * FROM public.mutate_channel_node(requested_tenant_id,requested_auth_session_id,requested_audit_id,'create_region','region',requested_id,NULL,requested_idem,jsonb_build_object('name',requested_name,'code',requested_code,'province',requested_province,'city',requested_city,'coverage_type',requested_coverage_type,'coverage_areas',requested_coverage_areas,'distributor_id',requested_distributor_id,'status',requested_status))",
        ),
        (
            "update_channel_region(requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,requested_id uuid,requested_expected_version bigint,requested_idem text,requested_name text,requested_province text,requested_city text,requested_coverage_type text,requested_coverage_areas jsonb,requested_distributor_id uuid,requested_status text)",
            "SELECT * FROM public.mutate_channel_node(requested_tenant_id,requested_auth_session_id,requested_audit_id,'update_region','region',requested_id,requested_expected_version,requested_idem,jsonb_build_object('name',requested_name,'province',requested_province,'city',requested_city,'coverage_type',requested_coverage_type,'coverage_areas',requested_coverage_areas,'distributor_id',requested_distributor_id,'status',requested_status))",
        ),
        (
            "create_channel_store(requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,requested_id uuid,requested_idem text,requested_name text,requested_code text,requested_region_id uuid,requested_distributor_id uuid,requested_address text,requested_status text)",
            "SELECT * FROM public.mutate_channel_node(requested_tenant_id,requested_auth_session_id,requested_audit_id,'create_store','store',requested_id,NULL,requested_idem,jsonb_build_object('name',requested_name,'code',requested_code,'region_id',requested_region_id,'distributor_id',requested_distributor_id,'address',requested_address,'status',requested_status))",
        ),
        (
            "update_channel_store(requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,requested_id uuid,requested_expected_version bigint,requested_idem text,requested_name text,requested_region_id uuid,requested_distributor_id uuid,requested_address text,requested_status text)",
            "SELECT * FROM public.mutate_channel_node(requested_tenant_id,requested_auth_session_id,requested_audit_id,'update_store','store',requested_id,requested_expected_version,requested_idem,jsonb_build_object('name',requested_name,'region_id',requested_region_id,'distributor_id',requested_distributor_id,'address',requested_address,'status',requested_status))",
        ),
        (
            "archive_channel_store(requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,requested_id uuid,requested_expected_version bigint,requested_idem text)",
            "SELECT * FROM public.mutate_channel_node(requested_tenant_id,requested_auth_session_id,requested_audit_id,'archive_store','store',requested_id,requested_expected_version,requested_idem,'{}'::jsonb)",
        ),
    )
    for signature, body in wrappers:
        op.execute(f"CREATE FUNCTION public.{signature} {common} AS $fn$ {body} $fn$")


def _install_allocation_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.allocate_code_batch_channel(
          requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
          requested_allocation_id uuid,requested_idem text,requested_batch_id uuid,
          requested_target_type text,requested_target_id uuid,requested_quantity bigint,requested_reason text
        ) RETURNS TABLE(allocation_id uuid,allocation_root_id uuid,version bigint,status text,quantity bigint,
          target_type text,target_id uuid,replayed boolean,actor_id uuid,recorded_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE resolved_actor uuid;DECLARE digest_value text;DECLARE existing public.channel_action_receipts%ROWTYPE;
        DECLARE batch_quantity bigint;DECLARE used_quantity bigint;DECLARE d uuid;DECLARE r uuid;DECLARE s uuid;
        DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
          SELECT authority.actor_id INTO resolved_actor FROM public.authorize_channel_actor(requested_tenant_id,requested_auth_session_id,'channel:allocate',false) authority;
          IF requested_quantity<=0 OR requested_allocation_id IS NULL OR requested_audit_id IS NULL OR NULLIF(trim(requested_idem),'') IS NULL
             OR length(requested_idem)>128 OR NULLIF(trim(requested_reason),'') IS NULL OR length(trim(requested_reason))>200 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='channel allocation input is invalid'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('channel-idem:'||requested_tenant_id::text||':allocate_codes:'||requested_idem,0));
          digest_value:=encode(digest(convert_to(jsonb_build_array(requested_batch_id,requested_target_type,requested_target_id,requested_quantity,trim(requested_reason))::text,'UTF8'),'sha256'),'hex');
          SELECT * INTO existing FROM public.channel_action_receipts receipt WHERE receipt.tenant_id=requested_tenant_id AND receipt.action='allocate_codes' AND receipt.idempotency_key=requested_idem FOR SHARE;
          IF FOUND THEN
            IF existing.payload_digest<>digest_value THEN RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='channel idempotency payload conflicts'; END IF;
            allocation_id:=(existing.result->>'allocation_id')::uuid;allocation_root_id:=(existing.result->>'allocation_root_id')::uuid;
            version:=existing.resource_version;status:=existing.status;quantity:=(existing.result->>'quantity')::bigint;
            target_type:=existing.result->>'target_type';target_id:=(existing.result->>'target_id')::uuid;replayed:=true;
            actor_id:=existing.actor_id;recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
          END IF;
          SELECT batch.quantity INTO batch_quantity FROM public.code_batches batch WHERE batch.tenant_id=requested_tenant_id AND batch.id=requested_batch_id FOR UPDATE;
          IF batch_quantity IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='code batch is unavailable'; END IF;
          SELECT target.distributor_id,target.region_id,target.store_id INTO d,r,s FROM public.validate_channel_target(requested_tenant_id,requested_target_type,requested_target_id) target;
          SELECT COALESCE(sum(a.quantity),0) INTO used_quantity FROM public.code_allocations a
          WHERE a.tenant_id=requested_tenant_id AND a.batch_id=requested_batch_id AND a.effective_to IS NULL AND a.status='active';
          IF used_quantity+requested_quantity>batch_quantity THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='channel allocation exceeds batch capacity'; END IF;
          INSERT INTO public.code_allocations(id,tenant_id,batch_id,store_id,region_id,distributor_id,allocation_root_id,
            target_type,target_id,action,status,quantity,allocated_at,effective_from,version,change_reason,actor_id,actor_tenant_id,audit_id,created_at,updated_at)
          VALUES(requested_allocation_id,requested_tenant_id,requested_batch_id,s,r,d,requested_allocation_id,
            requested_target_type,requested_target_id,'allocate','active',requested_quantity,to_char(now_at,'YYYY-MM-DD"T"HH24:MI:SSOF'),
            now_at,1,trim(requested_reason),resolved_actor,requested_tenant_id,requested_audit_id,now_at,now_at);
          allocation_id:=requested_allocation_id;allocation_root_id:=requested_allocation_id;version:=1;status:='active';quantity:=requested_quantity;
          target_type:=requested_target_type;target_id:=requested_target_id;replayed:=false;actor_id:=resolved_actor;recorded_at:=now_at;
          INSERT INTO public.channel_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,resource_type,resource_id,resource_version,status,actor_id,actor_tenant_id,audit_id,result,recorded_at)
          VALUES(requested_audit_id,requested_tenant_id,'allocate_codes',requested_idem,digest_value,'allocation',allocation_id,1,status,resolved_actor,requested_tenant_id,requested_audit_id,
            jsonb_build_object('allocation_id',allocation_id,'allocation_root_id',allocation_root_id,'quantity',quantity,'target_type',target_type,'target_id',target_id),now_at);
          INSERT INTO public.platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at)
          VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,'channel_codes_allocated','allocation:'||allocation_id::text,
            jsonb_build_object('batch_id',requested_batch_id,'quantity',quantity,'reason',trim(requested_reason)),now_at,now_at,now_at);RETURN NEXT;
        END $fn$
        """
    )
    # Reassign/archive use the same locked capacity authority.  They close only
    # the previous current interval and append a new immutable version.
    op.execute(
        r"""
        CREATE FUNCTION public.reassign_code_batch_channel(
          requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
          requested_new_id uuid,requested_idem text,requested_current_id uuid,requested_expected_version bigint,
          requested_target_type text,requested_target_id uuid,requested_quantity bigint,requested_reason text
        ) RETURNS TABLE(allocation_id uuid,allocation_root_id uuid,version bigint,status text,quantity bigint,
          target_type text,target_id uuid,replayed boolean,actor_id uuid,recorded_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE current_row public.code_allocations%ROWTYPE;DECLARE resolved_actor uuid;DECLARE d uuid;DECLARE r uuid;DECLARE s uuid;
        DECLARE capacity bigint;DECLARE used bigint;DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;DECLARE digest_value text;
        DECLARE existing public.channel_action_receipts%ROWTYPE;
        BEGIN
          SELECT authority.actor_id INTO resolved_actor FROM public.authorize_channel_actor(requested_tenant_id,requested_auth_session_id,'channel:allocate',false) authority;
          IF requested_audit_id IS NULL OR requested_new_id IS NULL OR requested_current_id IS NULL
             OR requested_expected_version IS NULL OR requested_expected_version<1
             OR requested_quantity<=0 OR NULLIF(trim(requested_idem),'') IS NULL OR length(requested_idem)>128
             OR NULLIF(trim(requested_reason),'') IS NULL OR length(trim(requested_reason))>200 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='channel reassign input is invalid'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('channel-idem:'||requested_tenant_id::text||':reassign_codes:'||requested_idem,0));
          digest_value:=encode(digest(convert_to(jsonb_build_array(requested_current_id,requested_expected_version,requested_target_type,requested_target_id,requested_quantity,trim(requested_reason))::text,'UTF8'),'sha256'),'hex');
          SELECT * INTO existing FROM public.channel_action_receipts receipt WHERE receipt.tenant_id=requested_tenant_id AND receipt.action='reassign_codes' AND receipt.idempotency_key=requested_idem FOR SHARE;
          IF FOUND THEN
            IF existing.payload_digest<>digest_value THEN RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='channel idempotency payload conflicts'; END IF;
            allocation_id:=(existing.result->>'allocation_id')::uuid;allocation_root_id:=(existing.result->>'allocation_root_id')::uuid;
            version:=existing.resource_version;status:=existing.status;quantity:=(existing.result->>'quantity')::bigint;
            target_type:=existing.result->>'target_type';target_id:=(existing.result->>'target_id')::uuid;replayed:=true;
            actor_id:=existing.actor_id;recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
          END IF;
          SELECT * INTO current_row FROM public.code_allocations a WHERE a.tenant_id=requested_tenant_id AND a.id=requested_current_id AND a.effective_to IS NULL AND a.status='active' FOR UPDATE;
          IF current_row.id IS NULL OR current_row.version<>requested_expected_version THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='channel allocation version conflict'; END IF;
          PERFORM batch.id FROM public.code_batches batch WHERE batch.tenant_id=requested_tenant_id AND batch.id=current_row.batch_id FOR UPDATE;
          SELECT batch.quantity INTO capacity FROM public.code_batches batch WHERE batch.tenant_id=requested_tenant_id AND batch.id=current_row.batch_id;
          SELECT target.distributor_id,target.region_id,target.store_id INTO d,r,s FROM public.validate_channel_target(requested_tenant_id,requested_target_type,requested_target_id) target;
          SELECT COALESCE(sum(a.quantity),0) INTO used FROM public.code_allocations a WHERE a.tenant_id=requested_tenant_id AND a.batch_id=current_row.batch_id AND a.effective_to IS NULL AND a.status='active' AND a.id<>current_row.id;
          IF requested_quantity<=0 OR used+requested_quantity>capacity THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='channel allocation exceeds batch capacity'; END IF;
          UPDATE public.code_allocations a SET effective_to=now_at,updated_at=now_at WHERE a.id=current_row.id;
          INSERT INTO public.code_allocations(id,tenant_id,batch_id,store_id,region_id,distributor_id,allocation_root_id,target_type,target_id,action,status,quantity,allocated_at,effective_from,version,change_reason,actor_id,actor_tenant_id,audit_id,created_at,updated_at)
          VALUES(requested_new_id,requested_tenant_id,current_row.batch_id,s,r,d,current_row.allocation_root_id,requested_target_type,requested_target_id,'reassign','active',requested_quantity,to_char(now_at,'YYYY-MM-DD"T"HH24:MI:SSOF'),now_at,current_row.version+1,trim(requested_reason),resolved_actor,requested_tenant_id,requested_audit_id,now_at,now_at);
          allocation_id:=requested_new_id;allocation_root_id:=current_row.allocation_root_id;version:=current_row.version+1;status:='active';quantity:=requested_quantity;target_type:=requested_target_type;target_id:=requested_target_id;replayed:=false;actor_id:=resolved_actor;recorded_at:=now_at;
          INSERT INTO public.channel_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,resource_type,resource_id,resource_version,status,actor_id,actor_tenant_id,audit_id,result,recorded_at)
          VALUES(requested_audit_id,requested_tenant_id,'reassign_codes',requested_idem,digest_value,'allocation',allocation_id,version,status,resolved_actor,requested_tenant_id,requested_audit_id,jsonb_build_object('allocation_id',allocation_id,'allocation_root_id',allocation_root_id,'quantity',quantity,'target_type',target_type,'target_id',target_id),now_at);
          INSERT INTO public.platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at) VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,'channel_codes_reassigned','allocation:'||allocation_id::text,jsonb_build_object('previous_id',requested_current_id,'version',version),now_at,now_at,now_at);RETURN NEXT;
        END $fn$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.archive_code_batch_allocation(
          requested_tenant_id uuid,requested_auth_session_id uuid,requested_audit_id uuid,
          requested_new_id uuid,requested_idem text,requested_current_id uuid,requested_expected_version bigint,requested_reason text
        ) RETURNS TABLE(allocation_id uuid,allocation_root_id uuid,version bigint,status text,quantity bigint,
          target_type text,target_id uuid,replayed boolean,actor_id uuid,recorded_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE current_row public.code_allocations%ROWTYPE;DECLARE resolved_actor uuid;DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        DECLARE digest_value text;DECLARE existing public.channel_action_receipts%ROWTYPE;
        BEGIN
          SELECT authority.actor_id INTO resolved_actor FROM public.authorize_channel_actor(requested_tenant_id,requested_auth_session_id,'channel:allocate',false) authority;
          IF requested_audit_id IS NULL OR requested_new_id IS NULL OR requested_current_id IS NULL
             OR requested_expected_version IS NULL OR requested_expected_version<1
             OR NULLIF(trim(requested_idem),'') IS NULL OR length(requested_idem)>128
             OR NULLIF(trim(requested_reason),'') IS NULL OR length(trim(requested_reason))>200 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='channel archive input is invalid'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('channel-idem:'||requested_tenant_id::text||':archive_allocation:'||requested_idem,0));
          digest_value:=encode(digest(convert_to(jsonb_build_array(requested_current_id,requested_expected_version,trim(requested_reason))::text,'UTF8'),'sha256'),'hex');
          SELECT * INTO existing FROM public.channel_action_receipts receipt WHERE receipt.tenant_id=requested_tenant_id AND receipt.action='archive_allocation' AND receipt.idempotency_key=requested_idem FOR SHARE;
          IF FOUND THEN
            IF existing.payload_digest<>digest_value THEN RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='channel idempotency payload conflicts'; END IF;
            allocation_id:=(existing.result->>'allocation_id')::uuid;allocation_root_id:=(existing.result->>'allocation_root_id')::uuid;
            version:=existing.resource_version;status:=existing.status;quantity:=0;target_type:=NULL;target_id:=NULL;
            replayed:=true;actor_id:=existing.actor_id;recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
          END IF;
          SELECT * INTO current_row FROM public.code_allocations a WHERE a.tenant_id=requested_tenant_id AND a.id=requested_current_id AND a.effective_to IS NULL AND a.status='active' FOR UPDATE;
          IF current_row.id IS NULL OR current_row.version<>requested_expected_version THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='channel allocation version conflict'; END IF;
          PERFORM batch.id FROM public.code_batches batch WHERE batch.tenant_id=requested_tenant_id AND batch.id=current_row.batch_id FOR UPDATE;
          UPDATE public.code_allocations a SET effective_to=now_at,updated_at=now_at WHERE a.id=current_row.id;
          INSERT INTO public.code_allocations(id,tenant_id,batch_id,allocation_root_id,action,status,quantity,allocated_at,effective_from,version,change_reason,actor_id,actor_tenant_id,audit_id,created_at,updated_at)
          VALUES(requested_new_id,requested_tenant_id,current_row.batch_id,current_row.allocation_root_id,'archive','archived',0,to_char(now_at,'YYYY-MM-DD"T"HH24:MI:SSOF'),now_at,current_row.version+1,trim(requested_reason),resolved_actor,requested_tenant_id,requested_audit_id,now_at,now_at);
          allocation_id:=requested_new_id;allocation_root_id:=current_row.allocation_root_id;version:=current_row.version+1;status:='archived';quantity:=0;target_type:=NULL;target_id:=NULL;replayed:=false;actor_id:=resolved_actor;recorded_at:=now_at;
          INSERT INTO public.channel_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,resource_type,resource_id,resource_version,status,actor_id,actor_tenant_id,audit_id,result,recorded_at)
          VALUES(requested_audit_id,requested_tenant_id,'archive_allocation',requested_idem,digest_value,'allocation',allocation_id,version,status,resolved_actor,requested_tenant_id,requested_audit_id,jsonb_build_object('allocation_id',allocation_id,'allocation_root_id',allocation_root_id,'quantity',0),now_at);
          INSERT INTO public.platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at) VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,'channel_allocation_archived','allocation:'||allocation_id::text,jsonb_build_object('previous_id',requested_current_id,'version',version),now_at,now_at,now_at);RETURN NEXT;
        END $fn$
        """
    )


def _install_assign_scope_read() -> None:
    # Batch assignment is serialized but kept separate from quantity allocation.
    op.execute(
        r"""
        CREATE FUNCTION public.assign_code_batch_channel(requested_tenant_id uuid,requested_auth_session_id uuid,
          requested_audit_id uuid,requested_batch_id uuid,requested_idem text,requested_distributor_id uuid,requested_region_id uuid)
        RETURNS TABLE(resource_id uuid,version bigint,status text,replayed boolean,actor_id uuid,recorded_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE resolved_actor uuid;DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;DECLARE digest_value text;
        DECLARE existing public.channel_action_receipts%ROWTYPE;
        BEGIN
          SELECT authority.actor_id INTO resolved_actor FROM public.authorize_channel_actor(requested_tenant_id,requested_auth_session_id,'channel:allocate',false) authority;
          IF requested_audit_id IS NULL OR requested_batch_id IS NULL
             OR NULLIF(trim(requested_idem),'') IS NULL OR length(requested_idem)>128
             OR (requested_distributor_id IS NULL AND requested_region_id IS NULL) THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='batch channel input is invalid'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('channel-idem:'||requested_tenant_id::text||':assign_batch:'||requested_idem,0));
          digest_value:=encode(digest(convert_to(jsonb_build_array(requested_batch_id,requested_distributor_id,requested_region_id)::text,'UTF8'),'sha256'),'hex');
          SELECT * INTO existing FROM public.channel_action_receipts receipt WHERE receipt.tenant_id=requested_tenant_id AND receipt.action='assign_batch' AND receipt.idempotency_key=requested_idem FOR SHARE;
          IF FOUND THEN
            IF existing.payload_digest<>digest_value THEN RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='channel idempotency payload conflicts'; END IF;
            resource_id:=existing.resource_id;version:=existing.resource_version;status:=existing.status;replayed:=true;
            actor_id:=existing.actor_id;recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
          END IF;
          IF requested_distributor_id IS NOT NULL THEN PERFORM * FROM public.validate_channel_target(requested_tenant_id,'distributor',requested_distributor_id); END IF;
          IF requested_region_id IS NOT NULL THEN
            PERFORM r.id FROM public.regions r WHERE r.tenant_id=requested_tenant_id AND r.id=requested_region_id AND (r.distributor_id IS NULL OR r.distributor_id IS NOT DISTINCT FROM requested_distributor_id) FOR SHARE NOWAIT;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='batch channel hierarchy is inconsistent'; END IF;
          END IF;
          UPDATE public.code_batches batch SET distributor_id=requested_distributor_id,region_id=requested_region_id,updated_at=now_at WHERE batch.tenant_id=requested_tenant_id AND batch.id=requested_batch_id;
          IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='code batch is unavailable'; END IF;
          resource_id:=requested_batch_id;version:=1;status:='assigned';replayed:=false;actor_id:=resolved_actor;recorded_at:=now_at;
          INSERT INTO public.channel_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,resource_type,resource_id,resource_version,status,actor_id,actor_tenant_id,audit_id,result,recorded_at) VALUES(requested_audit_id,requested_tenant_id,'assign_batch',requested_idem,digest_value,'code_batch',resource_id,1,status,resolved_actor,requested_tenant_id,requested_audit_id,jsonb_build_object('resource_id',resource_id,'version',1,'status',status),now_at);
          INSERT INTO public.platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at) VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,'channel_batch_assigned','code_batch:'||resource_id::text,jsonb_build_object('distributor_id',requested_distributor_id,'region_id',requested_region_id),now_at,now_at,now_at);RETURN NEXT;
        END $fn$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.set_account_channel_scope(requested_tenant_id uuid,requested_auth_session_id uuid,
          requested_audit_id uuid,requested_scope_id uuid,requested_idem text,requested_account_id uuid,
          requested_scope_type text,requested_target_id uuid)
        RETURNS TABLE(resource_id uuid,version bigint,status text,replayed boolean,actor_id uuid,recorded_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE resolved_actor uuid;DECLARE current_version bigint;DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        DECLARE d uuid;DECLARE r uuid;DECLARE s uuid;DECLARE digest_value text;
        DECLARE existing public.channel_action_receipts%ROWTYPE;
        BEGIN
          SELECT authority.actor_id INTO resolved_actor FROM public.authorize_channel_actor(requested_tenant_id,requested_auth_session_id,'channel:scope',true) authority;
          IF requested_audit_id IS NULL OR requested_scope_id IS NULL OR requested_account_id IS NULL
             OR requested_target_id IS NULL OR NULLIF(trim(requested_idem),'') IS NULL
             OR length(requested_idem)>128 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='channel scope input is invalid'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('channel-idem:'||requested_tenant_id::text||':set_account_scope:'||requested_idem,0));
          digest_value:=encode(digest(convert_to(jsonb_build_array(requested_account_id,requested_scope_type,requested_target_id)::text,'UTF8'),'sha256'),'hex');
          SELECT * INTO existing FROM public.channel_action_receipts receipt WHERE receipt.tenant_id=requested_tenant_id AND receipt.action='set_account_scope' AND receipt.idempotency_key=requested_idem FOR SHARE;
          IF FOUND THEN
            IF existing.payload_digest<>digest_value THEN RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='channel idempotency payload conflicts'; END IF;
            resource_id:=existing.resource_id;version:=existing.resource_version;status:=existing.status;replayed:=true;
            actor_id:=existing.actor_id;recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
          END IF;
          PERFORM account.id FROM public.accounts account WHERE account.tenant_id=requested_tenant_id AND account.id=requested_account_id AND account.is_active FOR SHARE NOWAIT;
          IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='scope account is unavailable'; END IF;
          SELECT target.distributor_id,target.region_id,target.store_id INTO d,r,s FROM public.validate_channel_target(requested_tenant_id,requested_scope_type,requested_target_id) target;
          -- Exact discriminator: inherited hierarchy columns are deliberately not persisted.
          d:=CASE WHEN requested_scope_type='distributor' THEN requested_target_id END;
          r:=CASE WHEN requested_scope_type='region' THEN requested_target_id END;
          s:=CASE WHEN requested_scope_type='store' THEN requested_target_id END;
          SELECT scope.version INTO current_version FROM public.account_channel_scopes scope WHERE scope.tenant_id=requested_tenant_id AND scope.account_id=requested_account_id AND scope.scope_type=requested_scope_type FOR UPDATE;
          IF current_version IS NULL THEN
            INSERT INTO public.account_channel_scopes(id,tenant_id,account_id,scope_type,target_id,distributor_id,region_id,store_id,version,created_at,updated_at)
            VALUES(requested_scope_id,requested_tenant_id,requested_account_id,requested_scope_type,requested_target_id,d,r,s,1,now_at,now_at);version:=1;
          ELSE
            UPDATE public.account_channel_scopes scope SET target_id=requested_target_id,distributor_id=d,region_id=r,store_id=s,version=scope.version+1,updated_at=now_at
            WHERE scope.tenant_id=requested_tenant_id AND scope.account_id=requested_account_id AND scope.scope_type=requested_scope_type
            RETURNING scope.id,scope.version INTO requested_scope_id,version;
          END IF;
          resource_id:=requested_scope_id;status:='active';replayed:=false;actor_id:=resolved_actor;recorded_at:=now_at;
          INSERT INTO public.channel_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,resource_type,resource_id,resource_version,status,actor_id,actor_tenant_id,audit_id,result,recorded_at) VALUES(requested_audit_id,requested_tenant_id,'set_account_scope',requested_idem,digest_value,'account_scope',resource_id,version,status,resolved_actor,requested_tenant_id,requested_audit_id,jsonb_build_object('resource_id',resource_id,'version',version,'status',status),now_at);
          INSERT INTO public.platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at) VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,'channel_scope_set','account_scope:'||resource_id::text,jsonb_build_object('account_id',requested_account_id,'scope_type',requested_scope_type,'target_id',requested_target_id),now_at,now_at,now_at);RETURN NEXT;
        END $fn$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.delete_account_channel_scope(requested_tenant_id uuid,requested_auth_session_id uuid,
          requested_audit_id uuid,requested_scope_id uuid,requested_expected_version bigint,requested_idem text)
        RETURNS TABLE(resource_id uuid,version bigint,status text,replayed boolean,actor_id uuid,recorded_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE resolved_actor uuid;DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;DECLARE digest_value text;
        DECLARE existing public.channel_action_receipts%ROWTYPE;
        BEGIN
          SELECT authority.actor_id INTO resolved_actor FROM public.authorize_channel_actor(requested_tenant_id,requested_auth_session_id,'channel:scope',true) authority;
          IF requested_audit_id IS NULL OR requested_scope_id IS NULL OR requested_expected_version IS NULL
             OR requested_expected_version<1 OR NULLIF(trim(requested_idem),'') IS NULL
             OR length(requested_idem)>128 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='channel scope delete input is invalid'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('channel-idem:'||requested_tenant_id::text||':delete_account_scope:'||requested_idem,0));
          digest_value:=encode(digest(convert_to(jsonb_build_array(requested_scope_id,requested_expected_version)::text,'UTF8'),'sha256'),'hex');
          SELECT * INTO existing FROM public.channel_action_receipts receipt WHERE receipt.tenant_id=requested_tenant_id AND receipt.action='delete_account_scope' AND receipt.idempotency_key=requested_idem FOR SHARE;
          IF FOUND THEN
            IF existing.payload_digest<>digest_value THEN RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='channel idempotency payload conflicts'; END IF;
            resource_id:=existing.resource_id;version:=existing.resource_version;status:=existing.status;replayed:=true;
            actor_id:=existing.actor_id;recorded_at:=existing.recorded_at;RETURN NEXT;RETURN;
          END IF;
          DELETE FROM public.account_channel_scopes scope WHERE scope.tenant_id=requested_tenant_id AND scope.id=requested_scope_id AND scope.version=requested_expected_version;
          IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='channel scope version conflict'; END IF;
          resource_id:=requested_scope_id;version:=requested_expected_version+1;status:='deleted';replayed:=false;actor_id:=resolved_actor;recorded_at:=now_at;
          INSERT INTO public.channel_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,resource_type,resource_id,resource_version,status,actor_id,actor_tenant_id,audit_id,result,recorded_at) VALUES(requested_audit_id,requested_tenant_id,'delete_account_scope',requested_idem,digest_value,'account_scope',resource_id,version,status,resolved_actor,requested_tenant_id,requested_audit_id,jsonb_build_object('resource_id',resource_id,'version',version,'status',status),now_at);
          INSERT INTO public.platform_audit_log(id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at) VALUES(requested_audit_id,resolved_actor::text,requested_tenant_id::text,'channel_scope_deleted','account_scope:'||resource_id::text,jsonb_build_object('version',version),now_at,now_at,now_at);RETURN NEXT;
        END $fn$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.get_my_channel_scope(requested_tenant_id uuid,requested_auth_session_id uuid,requested_scope_type text)
        RETURNS TABLE(scope_id uuid,scope_type text,target_id uuid,target_name text,target_status text,version bigint)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
        DECLARE subject_account uuid;DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
          IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id OR requested_scope_type NOT IN ('distributor','region','store') THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='channel portal subject denied'; END IF;
          PERFORM pg_advisory_xact_lock_shared(hashtextextended('auth-session:'||requested_auth_session_id::text,0));
          SELECT account.id INTO subject_account FROM public.auth_sessions session
          JOIN public.accounts account ON account.tenant_id=session.tenant_id AND account.id=session.account_id
          JOIN public.tenants tenant ON tenant.id=session.tenant_id
          WHERE session.id=requested_auth_session_id AND session.tenant_id=requested_tenant_id
            AND session.revoked_at IS NULL AND session.expires_at>now_at
            AND session.auth_version=account.auth_version AND account.is_active AND tenant.status='active';
          IF subject_account IS NULL THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='channel portal subject denied'; END IF;
          RETURN QUERY SELECT scope.id,scope.scope_type::text,scope.target_id,
            (CASE scope.scope_type WHEN 'distributor' THEN d.name WHEN 'region' THEN r.name ELSE s.name END)::text,
            (CASE scope.scope_type WHEN 'distributor' THEN d.status WHEN 'region' THEN r.status ELSE s.status END)::text,
            scope.version
          FROM public.account_channel_scopes scope LEFT JOIN public.distributors d ON scope.scope_type='distributor' AND d.tenant_id=scope.tenant_id AND d.id=scope.target_id
          LEFT JOIN public.regions r ON scope.scope_type='region' AND r.tenant_id=scope.tenant_id AND r.id=scope.target_id
          LEFT JOIN public.stores s ON scope.scope_type='store' AND s.tenant_id=scope.tenant_id AND s.id=scope.target_id
          WHERE scope.tenant_id=requested_tenant_id AND scope.account_id=subject_account AND scope.scope_type=requested_scope_type;
        END $fn$
        """
    )


def upgrade() -> None:
    _install_permissions()
    _install_internal()
    _install_node_wrappers()
    _install_allocation_functions()
    _install_assign_scope_read()
    op.alter_column("distributors", "contact_phone_recovery_state", server_default=None)
    for table in ("distributors", "regions", "stores", "account_channel_scopes"):
        op.alter_column(table, "version", server_default=None)
    op.alter_column("code_allocations", "action", server_default=None)
    op.alter_column("code_allocations", "status", server_default=None)
    for signature in (
        "authorize_channel_actor(uuid,uuid,text,boolean)",
        "validate_channel_target(uuid,text,uuid)",
        "mutate_channel_node(uuid,uuid,uuid,text,text,uuid,bigint,text,jsonb)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _role_exists():
            op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM {_ROLE}")
    for signature in _PUBLIC:
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _role_exists():
            op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_ROLE}")
    if _role_exists():
        for table in (
            "distributors",
            "regions",
            "stores",
            "code_allocations",
            "account_channel_scopes",
            "channel_action_receipts",
        ):
            op.execute(f"REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON public.{table} FROM {_ROLE}")
            op.execute(f"GRANT SELECT ON public.{table} TO {_ROLE}")


def downgrade() -> None:
    facts = op.get_bind().execute(sa.text("SELECT count(*) FROM public.channel_action_receipts")).scalar_one()
    if facts:
        raise RuntimeError("u7a3 downgrade blocked: channel action receipts are immutable facts")
    op.alter_column("code_allocations", "status", server_default="active")
    op.alter_column("code_allocations", "action", server_default="allocate")
    for table in ("account_channel_scopes", "stores", "regions", "distributors"):
        op.alter_column(table, "version", server_default="1")
    op.alter_column("distributors", "contact_phone_recovery_state", server_default="legacy_unknown")
    for signature in reversed(_PUBLIC):
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
    for signature in (
        "mutate_channel_node(uuid,uuid,uuid,text,text,uuid,bigint,text,jsonb)",
        "validate_channel_target(uuid,text,uuid)",
        "authorize_channel_actor(uuid,uuid,text,boolean)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
    # Remove only grants/permissions introduced by this revision.
    op.execute(
        "DELETE FROM public.role_permissions rp USING public.channel_permission_backfill marker "
        "WHERE marker.created_grant AND rp.tenant_id=marker.tenant_id AND rp.role_id=marker.role_id "
        "AND rp.permission_id=marker.permission_id"
    )
    op.execute(
        "DELETE FROM public.permissions permission USING public.channel_permission_backfill marker "
        "WHERE marker.created_permission AND permission.tenant_id=marker.tenant_id AND permission.id=marker.permission_id"
    )
    op.drop_table("channel_permission_backfill")
