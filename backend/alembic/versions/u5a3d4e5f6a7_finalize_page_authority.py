"""Finalize actor-bound, audited page mutation authority.

Revision ID: u5a3d4e5f6a7
Revises: u5a2c3d4e5f6
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u5a3d4e5f6a7"
down_revision: str | None = "u5a2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_ROLE = "yimatong_app"
_FUNCTIONS = (
    "mutate_page_template(uuid,uuid,uuid,text,uuid,uuid,text,text,text)",
    "create_page_version(uuid,uuid,uuid,uuid,uuid,jsonb)",
    "update_page_version(uuid,uuid,uuid,uuid,jsonb)",
    "publish_page_version(uuid,uuid,uuid,uuid)",
    "archive_page_version(uuid,uuid,uuid,uuid)",
    "rollback_page_version(uuid,uuid,uuid,uuid,uuid,uuid)",
)
_INTERNAL_FUNCTIONS = (
    "authorize_page_actor(uuid,uuid,text)",
    "mutate_page_version_authority(uuid,uuid,uuid,text,uuid,uuid,uuid,jsonb)",
)

_COMPAT_FUNCTION_SQL = r"""
CREATE FUNCTION public.populate_page_version_creator_tenant() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $function$
DECLARE requested_auth_session_id uuid;
DECLARE target_tenant_id uuid;
DECLARE resolved_tenant_id uuid;
DECLARE matched_authorization_id uuid;
DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
BEGIN
    target_tenant_id:=public.current_tenant_id();
    IF target_tenant_id IS NULL OR target_tenant_id IS DISTINCT FROM NEW.tenant_id THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page creator tenant context mismatch';
    END IF;
    BEGIN
        requested_auth_session_id:=NULLIF(current_setting('app.auth_session_id',true),'')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page creator session context is invalid';
    END;
    IF requested_auth_session_id IS NULL OR NEW.created_by IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='live page creator session is required';
    END IF;
    SELECT session.tenant_id INTO resolved_tenant_id
    FROM public.auth_sessions AS session
    JOIN public.accounts AS account
      ON account.tenant_id=session.tenant_id AND account.id=session.account_id
    JOIN public.tenants AS principal_tenant ON principal_tenant.id=session.tenant_id
    WHERE session.id=requested_auth_session_id
      AND session.account_id=NEW.created_by
      AND session.revoked_at IS NULL AND session.expires_at>now_at
      AND session.auth_version=account.auth_version AND account.is_active
      AND principal_tenant.status='active';
    IF resolved_tenant_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page creator session is not live';
    END IF;
    PERFORM target.id FROM public.tenants AS target
    WHERE target.id=target_tenant_id AND target.status='active' AND target.tenant_type='brand';
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page target tenant is unavailable';
    END IF;
    IF resolved_tenant_id IS DISTINCT FROM target_tenant_id THEN
        SELECT agency_auth.id INTO matched_authorization_id
        FROM public.agency_authorizations AS agency_auth
        JOIN public.tenants AS agency ON agency.id=agency_auth.agency_tenant_id
        WHERE agency_auth.agency_tenant_id=resolved_tenant_id
          AND agency_auth.client_tenant_id=target_tenant_id
          AND agency_auth.status='active'
          AND (agency_auth.expires_at IS NULL OR agency_auth.expires_at>now_at)
          AND agency_auth.scope::jsonb ? 'pages'
          AND agency.status='active' AND agency.tenant_type='agency';
        IF matched_authorization_id IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='42501',
                MESSAGE='page creator lacks a live pages authorization';
        END IF;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.account_roles AS account_role
        JOIN public.role_permissions AS role_permission
          ON role_permission.tenant_id=account_role.tenant_id
         AND role_permission.role_id=account_role.role_id
        JOIN public.permissions AS permission
          ON permission.tenant_id=role_permission.tenant_id
         AND permission.id=role_permission.permission_id
        WHERE account_role.tenant_id=resolved_tenant_id
          AND account_role.account_id=NEW.created_by
          AND permission.code='page:create'
    ) THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page creator lacks page:create permission';
    END IF;
    NEW.created_by_tenant_id:=resolved_tenant_id;
    RETURN NEW;
END;
$function$
"""

_AUTHORIZE_SQL = r"""
CREATE FUNCTION public.authorize_page_actor(
    requested_tenant_id uuid,
    requested_auth_session_id uuid,
    requested_permission text
) RETURNS TABLE(actor_id uuid, principal_tenant_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $function$
DECLARE probed_principal_tenant_id uuid;
DECLARE matched_authorization_id uuid;
DECLARE now_at timestamptz := CURRENT_TIMESTAMP;
BEGIN
    IF requested_permission NOT IN ('page:create','page:publish') THEN
        RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='unknown page permission';
    END IF;
    IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page tenant context mismatch';
    END IF;
    SELECT session.tenant_id INTO probed_principal_tenant_id
    FROM public.auth_sessions AS session WHERE session.id=requested_auth_session_id;
    IF probed_principal_tenant_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page auth session is not live';
    END IF;
    PERFORM pg_advisory_xact_lock_shared(6434892150882653249);
    PERFORM tenant.id FROM public.tenants AS tenant
    WHERE tenant.id IN (probed_principal_tenant_id,requested_tenant_id)
    ORDER BY tenant.id::text FOR SHARE;
    PERFORM pg_advisory_xact_lock_shared(
        hashtextextended('auth-session:' || requested_auth_session_id::text,0)
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
    IF actor_id IS NULL OR principal_tenant_id IS DISTINCT FROM probed_principal_tenant_id THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page auth session is not live';
    END IF;
    IF principal_tenant_id IS DISTINCT FROM requested_tenant_id THEN
        PERFORM pg_advisory_xact_lock(
            hashtextextended(principal_tenant_id::text || ':' || requested_tenant_id::text,0)
        );
        SELECT agency_auth.id INTO matched_authorization_id
        FROM public.agency_authorizations AS agency_auth
        JOIN public.tenants AS agency ON agency.id=agency_auth.agency_tenant_id
        JOIN public.tenants AS client ON client.id=agency_auth.client_tenant_id
        WHERE agency_auth.agency_tenant_id=principal_tenant_id
          AND agency_auth.client_tenant_id=requested_tenant_id
          AND agency_auth.status='active'
          AND (agency_auth.expires_at IS NULL OR agency_auth.expires_at>now_at)
          AND agency_auth.scope::jsonb ? 'pages'
          AND agency.status='active' AND agency.tenant_type='agency'
          AND client.status='active' AND client.tenant_type='brand'
        FOR SHARE OF agency_auth;
        IF matched_authorization_id IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page actor lacks a live pages authorization';
        END IF;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.account_roles AS account_role
        JOIN public.role_permissions AS role_permission
          ON role_permission.tenant_id=account_role.tenant_id
         AND role_permission.role_id=account_role.role_id
        JOIN public.permissions AS permission
          ON permission.tenant_id=role_permission.tenant_id
         AND permission.id=role_permission.permission_id
        WHERE account_role.tenant_id=principal_tenant_id
          AND account_role.account_id=actor_id
          AND permission.code=requested_permission
    ) THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page actor lacks a live required permission';
    END IF;
    RETURN NEXT;
END;
$function$
"""

_TEMPLATE_SQL = r"""
CREATE FUNCTION public.mutate_page_template(
    requested_tenant_id uuid,
    requested_auth_session_id uuid,
    requested_audit_id uuid,
    requested_action text,
    requested_template_id uuid,
    requested_product_id uuid,
    requested_name text,
    requested_template_type text,
    requested_description text
) RETURNS TABLE (
    page_template_id uuid,
    product_id uuid,
    status text,
    recorded_at timestamptz
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $function$
DECLARE actor_id uuid;
DECLARE template_row record;
DECLARE now_at timestamptz := CURRENT_TIMESTAMP;
DECLARE audit_action text;
DECLARE prior_status text;
DECLARE prior_name text;
DECLARE prior_description text;
BEGIN
    IF requested_action NOT IN ('create','update','archive','activate')
       OR requested_template_id IS NULL OR requested_audit_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='page template mutation parameters are invalid';
    END IF;
    IF requested_action='create' AND (
        NULLIF(btrim(requested_name),'') IS NULL OR length(requested_name)>200
        OR requested_template_type NOT IN ('product_info','traceability','brand_story')
        OR length(COALESCE(requested_description,''))>500
    ) THEN
        RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='page template content is invalid';
    END IF;
    IF requested_action='update' AND (
        (requested_name IS NOT NULL AND (NULLIF(btrim(requested_name),'') IS NULL OR length(requested_name)>200))
        OR length(COALESCE(requested_description,''))>500
    ) THEN
        RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='page template patch is invalid';
    END IF;
    IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page tenant context mismatch';
    END IF;
    IF NOT pg_try_advisory_xact_lock(
        hashtextextended(
            'page-template:' || requested_tenant_id::text || ':' || requested_template_id::text,0
        )
    ) THEN
        RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='page authority is concurrently changing';
    END IF;
    SELECT authorized.actor_id INTO actor_id
    FROM public.authorize_page_actor(
        requested_tenant_id,requested_auth_session_id,'page:create'
    ) AS authorized;
    IF requested_action='create' AND requested_product_id IS NOT NULL THEN
        PERFORM product.id FROM public.products AS product
        WHERE product.tenant_id=requested_tenant_id AND product.id=requested_product_id
        FOR SHARE NOWAIT;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='page template product is unavailable';
        END IF;
    END IF;
    IF requested_action='create' THEN
        INSERT INTO public.page_templates(
            id,tenant_id,product_id,name,template_type,status,description,created_at,updated_at
        ) VALUES (
            requested_template_id,requested_tenant_id,requested_product_id,btrim(requested_name),
            requested_template_type,'active',NULLIF(btrim(requested_description),''),now_at,now_at
        ) RETURNING * INTO template_row;
        prior_status:=NULL; prior_name:=NULL; prior_description:=NULL;
        audit_action:='page_template_created';
    ELSE
        SELECT * INTO template_row FROM public.page_templates AS template
        WHERE template.tenant_id=requested_tenant_id AND template.id=requested_template_id
        FOR UPDATE NOWAIT;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='page template is unavailable';
        END IF;
        prior_status:=template_row.status;
        prior_name:=template_row.name;
        prior_description:=template_row.description;
        IF requested_action='update' THEN
            UPDATE public.page_templates SET
                name=CASE WHEN requested_name IS NULL THEN name ELSE btrim(requested_name) END,
                description=CASE WHEN requested_description IS NULL THEN description
                    ELSE NULLIF(btrim(requested_description),'') END,
                updated_at=now_at
            WHERE tenant_id=requested_tenant_id AND id=requested_template_id RETURNING * INTO template_row;
            audit_action:='page_template_updated';
        ELSIF requested_action='archive' THEN
            IF template_row.status<>'active' THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='page template is not active';
            END IF;
            UPDATE public.page_templates SET status='archived',updated_at=now_at
            WHERE tenant_id=requested_tenant_id AND id=requested_template_id RETURNING * INTO template_row;
            audit_action:='page_template_archived';
        ELSE
            IF template_row.status<>'archived' THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='page template is not archived';
            END IF;
            UPDATE public.page_templates SET status='active',updated_at=now_at
            WHERE tenant_id=requested_tenant_id AND id=requested_template_id RETURNING * INTO template_row;
            audit_action:='page_template_activated';
        END IF;
    END IF;
    INSERT INTO public.platform_audit_log(
        id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
    ) VALUES (
        requested_audit_id,actor_id::text,requested_tenant_id::text,audit_action,
        'page_template:' || requested_template_id::text,
        jsonb_build_object(
            'before',jsonb_build_object('status',prior_status,'name',prior_name,'description',prior_description),
            'after',jsonb_build_object('status',template_row.status,'product_id',template_row.product_id,
                'name',template_row.name,'description',template_row.description)
        ),
        now_at,now_at,now_at
    );
    page_template_id:=template_row.id; product_id:=template_row.product_id;
    status:=template_row.status; recorded_at:=now_at; RETURN NEXT;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='page authority is concurrently changing';
END;
$function$
"""

_VERSION_INTERNAL_SQL = r"""
CREATE FUNCTION public.mutate_page_version_authority(
    requested_tenant_id uuid,
    requested_auth_session_id uuid,
    requested_audit_id uuid,
    requested_action text,
    requested_version_id uuid,
    requested_template_id uuid,
    requested_source_version_id uuid,
    requested_config jsonb
) RETURNS TABLE (
    page_version_id uuid,
    page_template_id uuid,
    source_version_id uuid,
    version_number integer,
    current_status text,
    published_at timestamptz,
    updated_at timestamptz,
    recorded_at timestamptz
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $function$
DECLARE actor_id uuid;
DECLARE actor_tenant_id uuid;
DECLARE template_id_value uuid;
DECLARE template_status text;
DECLARE version_row record;
DECLARE source_row record;
DECLARE next_version integer;
DECLARE prior_status text;
DECLARE prior_config jsonb;
DECLARE audit_action text;
DECLARE now_at timestamptz := CURRENT_TIMESTAMP;
BEGIN
    IF requested_action NOT IN ('create','update','publish','archive','rollback')
       OR requested_audit_id IS NULL OR requested_version_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='page version mutation parameters are invalid';
    END IF;
    IF requested_action IN ('create','update')
       AND (requested_config IS NULL OR jsonb_typeof(requested_config)<>'object') THEN
        RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='page version config must be an object';
    END IF;
    IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page tenant context mismatch';
    END IF;
    IF requested_action IN ('create','rollback') THEN
        template_id_value:=requested_template_id;
    ELSE
        SELECT version.page_template_id INTO template_id_value
        FROM public.page_versions AS version
        WHERE version.tenant_id=requested_tenant_id AND version.id=requested_version_id;
    END IF;
    IF template_id_value IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='page version is unavailable';
    END IF;
    IF NOT pg_try_advisory_xact_lock(
        hashtextextended(
            'page-template:' || requested_tenant_id::text || ':' || template_id_value::text,0
        )
    ) THEN
        RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='page authority is concurrently changing';
    END IF;
    SELECT authorized.actor_id,authorized.principal_tenant_id INTO actor_id,actor_tenant_id
    FROM public.authorize_page_actor(
        requested_tenant_id,requested_auth_session_id,
        CASE WHEN requested_action='publish' THEN 'page:publish' ELSE 'page:create' END
    ) AS authorized;
    SELECT template.status INTO template_status FROM public.page_templates AS template
    WHERE template.tenant_id=requested_tenant_id AND template.id=template_id_value
    FOR UPDATE NOWAIT;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='page template is unavailable';
    END IF;
    IF requested_action IN ('create','update','publish','rollback') AND template_status<>'active' THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='page template is not active';
    END IF;
    IF requested_action='create' THEN
        SELECT COALESCE(max(existing.version),0)+1 INTO next_version
        FROM public.page_versions AS existing
        WHERE existing.tenant_id=requested_tenant_id AND existing.page_template_id=template_id_value;
        INSERT INTO public.page_versions(
            id,tenant_id,page_template_id,version,config_json,status,created_by_tenant_id,created_by,
            published_at,created_at,updated_at
        ) VALUES (
            requested_version_id,requested_tenant_id,template_id_value,next_version,requested_config,
            'draft',actor_tenant_id,actor_id,NULL,now_at,now_at
        ) RETURNING * INTO version_row;
        prior_status:=NULL; prior_config:=NULL; audit_action:='page_version_created';
    ELSIF requested_action='rollback' THEN
        SELECT * INTO source_row FROM public.page_versions AS source
        WHERE source.tenant_id=requested_tenant_id AND source.page_template_id=template_id_value
          AND source.id=requested_source_version_id FOR UPDATE NOWAIT;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='page rollback source is unavailable';
        END IF;
        SELECT COALESCE(max(existing.version),0)+1 INTO next_version
        FROM public.page_versions AS existing
        WHERE existing.tenant_id=requested_tenant_id AND existing.page_template_id=template_id_value;
        INSERT INTO public.page_versions(
            id,tenant_id,page_template_id,version,config_json,status,created_by_tenant_id,created_by,
            published_at,created_at,updated_at
        ) VALUES (
            requested_version_id,requested_tenant_id,template_id_value,next_version,source_row.config_json,
            'draft',actor_tenant_id,actor_id,NULL,now_at,now_at
        ) RETURNING * INTO version_row;
        prior_status:=source_row.status; prior_config:=source_row.config_json;
        audit_action:='page_version_rolled_back';
    ELSIF requested_action='publish' THEN
        PERFORM existing.id FROM public.page_versions AS existing
        WHERE existing.tenant_id=requested_tenant_id AND existing.page_template_id=template_id_value
          AND (existing.status='published' OR existing.id=requested_version_id)
        ORDER BY existing.id FOR UPDATE NOWAIT;
        SELECT * INTO version_row FROM public.page_versions AS version
        WHERE version.tenant_id=requested_tenant_id AND version.page_template_id=template_id_value
          AND version.id=requested_version_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='page version is unavailable';
        END IF;
        prior_status:=version_row.status;
        prior_config:=version_row.config_json;
        IF prior_status<>'draft' THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='only a draft page version can be published';
        END IF;
        UPDATE public.page_versions AS mutable SET status='archived',updated_at=now_at
        WHERE mutable.tenant_id=requested_tenant_id AND mutable.page_template_id=template_id_value
          AND mutable.status='published' AND mutable.id<>requested_version_id;
        UPDATE public.page_versions AS mutable SET status='published',published_at=now_at,updated_at=now_at
        WHERE mutable.tenant_id=requested_tenant_id AND mutable.page_template_id=template_id_value
          AND mutable.id=requested_version_id RETURNING mutable.* INTO version_row;
        audit_action:='page_version_published';
    ELSE
        SELECT * INTO version_row FROM public.page_versions AS version
        WHERE version.tenant_id=requested_tenant_id AND version.page_template_id=template_id_value
          AND version.id=requested_version_id FOR UPDATE NOWAIT;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='page version is unavailable';
        END IF;
        prior_status:=version_row.status;
        prior_config:=version_row.config_json;
        IF requested_action='update' THEN
            IF prior_status<>'draft' THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='only a draft page version can be updated';
            END IF;
            UPDATE public.page_versions AS mutable SET config_json=requested_config,updated_at=now_at
            WHERE mutable.tenant_id=requested_tenant_id AND mutable.page_template_id=template_id_value
              AND mutable.id=requested_version_id RETURNING mutable.* INTO version_row;
            audit_action:='page_version_updated';
        ELSE
            IF prior_status NOT IN ('draft','published') THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='page version cannot be archived';
            END IF;
            UPDATE public.page_versions AS mutable SET status='archived',updated_at=now_at
            WHERE mutable.tenant_id=requested_tenant_id AND mutable.page_template_id=template_id_value
              AND mutable.id=requested_version_id RETURNING mutable.* INTO version_row;
            audit_action:='page_version_archived';
        END IF;
    END IF;
    INSERT INTO public.platform_audit_log(
        id,operator_id,target_tenant_id,action,resource,details,timestamp,created_at,updated_at
    ) VALUES (
        requested_audit_id,actor_id::text,requested_tenant_id::text,audit_action,
        'page_version:' || requested_version_id::text,
        jsonb_build_object(
            'template_id',template_id_value,'source_version_id',requested_source_version_id,
            'before_sha256',CASE WHEN prior_config IS NULL THEN NULL ELSE
                encode(public.digest(convert_to(prior_config::text,'UTF8'),'sha256'),'hex') END,
            'after_sha256',encode(public.digest(convert_to(version_row.config_json::text,'UTF8'),'sha256'),'hex'),
            'before',jsonb_build_object('status',prior_status),
            'after',jsonb_build_object('status',version_row.status,'version',version_row.version)
        ),now_at,now_at,now_at
    );
    page_version_id:=version_row.id; page_template_id:=version_row.page_template_id;
    source_version_id:=requested_source_version_id; version_number:=version_row.version;
    current_status:=version_row.status; published_at:=version_row.published_at;
    updated_at:=version_row.updated_at; recorded_at:=now_at; RETURN NEXT;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='page authority is concurrently changing';
END;
$function$
"""

_WRAPPER_SQL = r"""
CREATE FUNCTION public.create_page_version(
    requested_tenant_id uuid, requested_auth_session_id uuid, requested_audit_id uuid,
    requested_version_id uuid, requested_template_id uuid, requested_config jsonb
) RETURNS TABLE (
    page_version_id uuid,page_template_id uuid,version_number integer,current_status text,
    published_at timestamptz,updated_at timestamptz,recorded_at timestamptz
) LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $function$
SELECT result.page_version_id,result.page_template_id,result.version_number,result.current_status,
    result.published_at,result.updated_at,result.recorded_at
FROM public.mutate_page_version_authority(
    requested_tenant_id,requested_auth_session_id,requested_audit_id,'create',
    requested_version_id,requested_template_id,NULL,requested_config
) AS result
$function$;

CREATE FUNCTION public.update_page_version(
    requested_tenant_id uuid, requested_auth_session_id uuid, requested_audit_id uuid,
    requested_version_id uuid, requested_config jsonb
) RETURNS TABLE (
    page_version_id uuid,page_template_id uuid,version_number integer,current_status text,
    published_at timestamptz,updated_at timestamptz,recorded_at timestamptz
) LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $function$
SELECT result.page_version_id,result.page_template_id,result.version_number,result.current_status,
    result.published_at,result.updated_at,result.recorded_at
FROM public.mutate_page_version_authority(
    requested_tenant_id,requested_auth_session_id,requested_audit_id,'update',
    requested_version_id,NULL,NULL,requested_config
) AS result
$function$;

CREATE FUNCTION public.publish_page_version(
    requested_tenant_id uuid, requested_auth_session_id uuid, requested_audit_id uuid,
    requested_version_id uuid
) RETURNS TABLE (
    page_version_id uuid,page_template_id uuid,version_number integer,current_status text,
    published_at timestamptz,updated_at timestamptz,recorded_at timestamptz
) LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $function$
SELECT result.page_version_id,result.page_template_id,result.version_number,result.current_status,
    result.published_at,result.updated_at,result.recorded_at
FROM public.mutate_page_version_authority(
    requested_tenant_id,requested_auth_session_id,requested_audit_id,'publish',
    requested_version_id,NULL,NULL,NULL
) AS result
$function$;

CREATE FUNCTION public.archive_page_version(
    requested_tenant_id uuid, requested_auth_session_id uuid, requested_audit_id uuid,
    requested_version_id uuid
) RETURNS TABLE (
    page_version_id uuid,page_template_id uuid,version_number integer,current_status text,
    published_at timestamptz,updated_at timestamptz,recorded_at timestamptz
) LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $function$
SELECT result.page_version_id,result.page_template_id,result.version_number,result.current_status,
    result.published_at,result.updated_at,result.recorded_at
FROM public.mutate_page_version_authority(
    requested_tenant_id,requested_auth_session_id,requested_audit_id,'archive',
    requested_version_id,NULL,NULL,NULL
) AS result
$function$;

CREATE FUNCTION public.rollback_page_version(
    requested_tenant_id uuid, requested_auth_session_id uuid, requested_audit_id uuid,
    requested_new_version_id uuid, requested_template_id uuid, requested_source_version_id uuid
) RETURNS TABLE (
    page_version_id uuid,page_template_id uuid,source_version_id uuid,version_number integer,
    current_status text,published_at timestamptz,updated_at timestamptz,recorded_at timestamptz
) LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $function$
SELECT result.page_version_id,result.page_template_id,result.source_version_id,
    result.version_number,result.current_status,result.published_at,result.updated_at,result.recorded_at
FROM public.mutate_page_version_authority(
    requested_tenant_id,requested_auth_session_id,requested_audit_id,'rollback',
    requested_new_version_id,requested_template_id,requested_source_version_id,NULL
) AS result
$function$;
"""


def _role_exists() -> bool:
    return bool(op.get_bind().exec_driver_sql("SELECT 1 FROM pg_roles WHERE rolname='yimatong_app'").scalar())


def _assert_function_catalog() -> None:
    for signature in (*_INTERNAL_FUNCTIONS, *_FUNCTIONS):
        row = op.get_bind().execute(
            sa.text(
                "SELECT procedure.prosecdef,procedure.proconfig "
                "FROM pg_proc AS procedure WHERE procedure.oid=to_regprocedure(:signature)"
            ),
            {"signature": f"public.{signature}"},
        ).one_or_none()
        if row is None or row[0] is not True or row[1] != ["search_path=pg_catalog, public"]:
            raise RuntimeError(f"page authority function public.{signature} is not exact and secure")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(_AUTHORIZE_SQL)
    op.execute(_TEMPLATE_SQL)
    op.execute(_VERSION_INTERNAL_SQL)
    for statement in _WRAPPER_SQL.split("$function$;"):
        if statement.strip():
            op.execute(statement + "$function$")
    _assert_function_catalog()
    for signature in (*_INTERNAL_FUNCTIONS, *_FUNCTIONS):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
    if _role_exists():
        op.execute(
            "REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER "
            "ON TABLE public.page_templates,public.page_versions FROM yimatong_app"
        )
        for signature in _INTERNAL_FUNCTIONS:
            op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM yimatong_app")
        for signature in _FUNCTIONS:
            op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO yimatong_app")
    op.execute("DROP TRIGGER trg_populate_page_version_creator_tenant ON public.page_versions")
    op.execute("DROP FUNCTION public.populate_page_version_creator_tenant()")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for signature in reversed(_FUNCTIONS):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        if _role_exists():
            op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM yimatong_app")
        op.execute(f"DROP FUNCTION public.{signature}")
    for signature in reversed(_INTERNAL_FUNCTIONS):
        op.execute(f"DROP FUNCTION public.{signature}")
    op.execute(_COMPAT_FUNCTION_SQL)
    op.execute(
        "CREATE TRIGGER trg_populate_page_version_creator_tenant "
        "BEFORE INSERT OR UPDATE OF created_by ON public.page_versions "
        "FOR EACH ROW EXECUTE FUNCTION public.populate_page_version_creator_tenant()"
    )
    op.execute("REVOKE ALL ON FUNCTION public.populate_page_version_creator_tenant() FROM PUBLIC")
    if _role_exists():
        op.execute("REVOKE ALL ON FUNCTION public.populate_page_version_creator_tenant() FROM yimatong_app")
        op.execute(
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE "
            "public.page_templates,public.page_versions TO yimatong_app"
        )
