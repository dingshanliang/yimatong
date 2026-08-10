"""Expand external API credentials to a hashed, tenant-bound lifecycle.

Revision ID: b10d4e6f8a20
Revises: adde49f79bcd
Create Date: 2026-08-10

This is the rolling-deployment expand point.  It retains the nullable legacy
``key`` column so old application processes can drain while new processes use
the digest interfaces.  Existing secrets are copied to an owner-only encrypted
rollback escrow before they are hashed.  Deploy the digest-aware application at
this revision, then apply the contract revision during a maintenance/drain
window.  ``API_KEY_MIGRATION_KEY`` is required only when legacy rows exist; it
is never stored or logged.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from alembic import op

revision: str = "b10d4e6f8a20"
down_revision: str | None = "adde49f79bcd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_ROLE = "yimatong_app"
_ESCROW_TABLE = "api_key_legacy_secret_backups"
_PERMISSIONS_FUNCTION = "api_key_permissions_for_role"
_ACTOR_FUNCTION = "assert_api_key_actor"
_AUDIT_FUNCTION = "append_api_key_audit"
_ROW_GUARD_FUNCTION = "guard_api_key_row"
_RESOLVE_FUNCTION = "resolve_active_api_key"
_LOCK_FUNCTION = "lock_active_api_key"
_ISSUE_FUNCTION = "issue_api_key"
_ROTATE_FUNCTION = "rotate_api_key"
_REVOKE_FUNCTION = "revoke_api_key"
_QUOTA_ROLLOUT_ADVISORY_LOCK_KEY = 6434892150882653249
_LEGACY_ESCROW_SCHEME = "legacy-pgcrypto-v1"
_APP_ESCROW_SCHEME = "app-aesgcm-json-v1"
_ACTIVE_API_KEY_LIMIT = 20

_ROLE_PERMISSIONS = {
    "data_reader": [
        "scan:list",
        "scan:detail",
        "consumer:list",
        "consumer:detail",
        "claim:list",
        "claim:detail",
        "event:list",
        "event:detail",
    ],
    "coupon_operator": [
        "scan:list",
        "scan:detail",
        "consumer:list",
        "consumer:detail",
        "claim:list",
        "claim:detail",
        "event:list",
        "event:detail",
        "coupon:issue",
        "coupon:redeem",
        "coupon:detail",
    ],
    "webhook_admin": [
        "webhook:endpoint_create",
        "webhook:endpoint_list",
        "webhook:endpoint_update",
        "webhook:endpoint_delete",
        "webhook:delivery_list",
        "webhook:delivery_retry",
        "api_key:create",
        "api_key:list",
        "api_key:revoke",
    ],
    "erp_sync": ["product:list", "product:create", "product:update"],
    "full_access": [
        "scan:list",
        "scan:detail",
        "consumer:list",
        "consumer:detail",
        "claim:list",
        "claim:detail",
        "claim:create",
        "claim:update",
        "event:list",
        "event:detail",
        "coupon:issue",
        "coupon:redeem",
        "coupon:detail",
        "coupon:delete",
        "campaign:update",
        "campaign:status",
        "code:batch_create",
        "code:batch_update",
        "product:list",
        "product:create",
        "product:update",
        "webhook:endpoint_create",
        "webhook:endpoint_list",
        "webhook:endpoint_update",
        "webhook:endpoint_delete",
        "webhook:delivery_list",
        "webhook:delivery_retry",
        "api_key:create",
        "api_key:list",
        "api_key:revoke",
    ],
}


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": _RUNTIME_ROLE})
        .scalar_one()
    )


def _migration_key(required: bool) -> str | None:
    value = os.getenv("API_KEY_MIGRATION_KEY")
    if not required:
        return value
    if value is None or len(value.encode("utf-8")) < 32:
        raise RuntimeError("API_KEY_MIGRATION_KEY must contain at least 32 UTF-8 bytes for legacy API-key escrow")
    return value


def _aes_key(key_id: int) -> bytes:
    value = os.getenv(f"AES_MASTER_KEY_V{key_id}")
    if value is None or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise RuntimeError(f"AES_MASTER_KEY_V{key_id} must contain exactly 32 bytes as hexadecimal")
    return bytes.fromhex(value)


def _decrypt_app_escrow(api_key_id: uuid.UUID, ciphertext: bytes) -> str:
    if len(ciphertext) < 31:
        raise RuntimeError(f"API-key app escrow envelope is malformed for row {api_key_id}")
    key_id = struct.unpack(">H", ciphertext[:2])[0]
    try:
        plaintext = AESGCM(_aes_key(key_id)).decrypt(ciphertext[2:14], ciphertext[14:], None)
        payload = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"API-key app escrow authentication failed for row {api_key_id}") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"v", "api_key_id", "secret"}
        or payload.get("v") != 1
        or payload.get("api_key_id") != str(api_key_id)
        or not isinstance(payload.get("secret"), str)
        or re.fullmatch(r"ymt_[0-9a-f]{48}", payload["secret"]) is None
    ):
        raise RuntimeError(f"API-key app escrow payload is invalid for row {api_key_id}")
    return payload["secret"]


def _verified_expand_downgrade_secrets() -> dict[uuid.UUID, str]:
    rows = list(
        op.get_bind()
        .execute(
            sa.text(
                f"""
                SELECT api_key.id, api_key.key_prefix, api_key.key_digest,
                       backup.encryption_scheme, backup.encrypted_key
                FROM public.api_keys AS api_key
                LEFT JOIN public.{_ESCROW_TABLE} AS backup ON backup.api_key_id = api_key.id
                WHERE api_key.key IS NULL
                ORDER BY api_key.id
                """
            )
        )
        .mappings()
    )
    secrets_by_id: dict[uuid.UUID, str] = {}
    for row in rows:
        api_key_id = uuid.UUID(str(row["id"]))
        if row["encryption_scheme"] != _APP_ESCROW_SCHEME or row["encrypted_key"] is None:
            raise RuntimeError(f"API-key expand downgrade lacks app escrow for row {api_key_id}")
        secret = _decrypt_app_escrow(api_key_id, bytes(row["encrypted_key"]))
        if secret[:12] != row["key_prefix"] or hashlib.sha256(secret.encode()).hexdigest() != row["key_digest"]:
            raise RuntimeError(f"API-key expand downgrade escrow integrity check failed for row {api_key_id}")
        secrets_by_id[api_key_id] = secret
    return secrets_by_id


def _permissions_case(role_expression: str) -> str:
    branches = []
    for role, permissions in _ROLE_PERMISSIONS.items():
        value = json.dumps(permissions, separators=(",", ":"))
        branches.append(f"WHEN '{role}' THEN '{value}'::jsonb")
    return f"CASE {role_expression} {' '.join(branches)} ELSE NULL::jsonb END"


def _preflight() -> None:
    permissions = _permissions_case("api_key.role")
    op.execute(
        f"""
        DO $block$
        DECLARE invalid_ids text;
        BEGIN
            SELECT string_agg(api_key.id::text, ', ' ORDER BY api_key.id::text)
            INTO invalid_ids
            FROM public.api_keys AS api_key
            LEFT JOIN public.tenants AS tenant ON tenant.id = api_key.tenant_id
            WHERE tenant.id IS NULL;
            IF invalid_ids IS NOT NULL THEN
                RAISE EXCEPTION 'Orphan API-key tenant rows: %', invalid_ids;
            END IF;

            SELECT string_agg(api_key.id::text, ', ' ORDER BY api_key.id::text)
            INTO invalid_ids
            FROM public.api_keys AS api_key
            WHERE api_key.key !~ '^ymt_[0-9a-f]{{48}}$'
               OR api_key.name IS NULL
               OR btrim(api_key.name) = ''
               OR api_key.role NOT IN ('data_reader', 'coupon_operator', 'webhook_admin', 'erp_sync', 'full_access')
               OR api_key.permissions::jsonb IS DISTINCT FROM {permissions};
            IF invalid_ids IS NOT NULL THEN
                RAISE EXCEPTION 'Malformed legacy API-key rows: %', invalid_ids;
            END IF;

            SELECT string_agg(colliding.digest, ', ' ORDER BY colliding.digest)
            INTO invalid_ids
            FROM (
                SELECT encode(sha256(convert_to(api_key.key, 'UTF8')), 'hex') AS digest
                FROM public.api_keys AS api_key
                GROUP BY encode(sha256(convert_to(api_key.key, 'UTF8')), 'hex')
                HAVING count(*) > 1
            ) AS colliding;
            IF invalid_ids IS NOT NULL THEN
                RAISE EXCEPTION 'Duplicate legacy API-key digests: %', invalid_ids;
            END IF;
        END
        $block$
        """
    )


def _create_escrow(migration_key: str | None) -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        _ESCROW_TABLE,
        sa.Column("api_key_id", sa.Uuid(), nullable=False),
        sa.Column("encrypted_key", sa.LargeBinary(), nullable=False),
        sa.Column("encryption_scheme", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            f"encryption_scheme IN ('{_LEGACY_ESCROW_SCHEME}', '{_APP_ESCROW_SCHEME}')",
            name="ck_api_key_legacy_secret_backups_scheme",
        ),
        sa.PrimaryKeyConstraint("api_key_id"),
        schema="public",
    )
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{_ESCROW_TABLE} FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{_ESCROW_TABLE} FROM {_RUNTIME_ROLE}")
    if migration_key is not None:
        op.get_bind().execute(
            sa.text(
                f"""
                INSERT INTO public.{_ESCROW_TABLE} (api_key_id, encrypted_key, encryption_scheme)
                SELECT id, pgp_sym_encrypt(
                    key,
                    :migration_key,
                    'cipher-algo=aes256, compress-algo=0'
                ), '{_LEGACY_ESCROW_SCHEME}'
                FROM public.api_keys
                """
            ),
            {"migration_key": migration_key},
        )


def _expand_schema() -> None:
    op.add_column("api_keys", sa.Column("key_prefix", sa.String(length=20), nullable=True))
    op.add_column("api_keys", sa.Column("key_digest", sa.String(length=64), nullable=True))
    op.add_column("api_keys", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_keys", sa.Column("rotated_from_id", sa.Uuid(), nullable=True))
    op.add_column("api_keys", sa.Column("created_by", sa.Uuid(), nullable=True))
    op.add_column("api_keys", sa.Column("idempotency_key_digest", sa.String(length=64), nullable=True))
    op.add_column("api_keys", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("api_keys", sa.Column("permanent_reason", sa.String(length=200), nullable=True))
    op.alter_column("api_keys", "key", existing_type=sa.String(length=100), nullable=True)

    op.execute(
        """
        UPDATE public.api_keys
        SET key_prefix = left(key, 12),
            key_digest = encode(sha256(convert_to(key, 'UTF8')), 'hex'),
            revoked_at = CASE
                WHEN revoked THEN COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
                ELSE NULL
            END,
            updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
        """
    )
    op.alter_column("api_keys", "key_prefix", existing_type=sa.String(length=20), nullable=False)
    op.alter_column("api_keys", "key_digest", existing_type=sa.String(length=64), nullable=False)

    op.create_unique_constraint("uq_api_keys_tenant_id_id", "api_keys", ["tenant_id", "id"])
    op.create_index("uq_api_keys_key_digest", "api_keys", ["key_digest"], unique=True)
    op.create_index(
        "uq_api_keys_tenant_creator_idempotency",
        "api_keys",
        ["tenant_id", "created_by", "idempotency_key_digest"],
        unique=True,
        postgresql_where=sa.text("idempotency_key_digest IS NOT NULL"),
    )
    op.create_index(
        "ix_api_keys_tenant_active_expiry",
        "api_keys",
        ["tenant_id", "expires_at"],
        postgresql_where=sa.text("revoked = false AND revoked_at IS NULL"),
    )
    op.create_index(
        "uq_api_keys_tenant_rotated_from",
        "api_keys",
        ["tenant_id", "rotated_from_id"],
        unique=True,
        postgresql_where=sa.text("rotated_from_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_api_keys_digest_format",
        "api_keys",
        "key_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_api_keys_prefix_format",
        "api_keys",
        "key_prefix ~ '^ymt_[0-9a-f]{8}$'",
    )
    op.create_check_constraint(
        "ck_api_keys_revocation_state",
        "api_keys",
        "(revoked = false AND revoked_at IS NULL) OR (revoked = true AND revoked_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_api_keys_expiry_after_creation",
        "api_keys",
        "expires_at IS NULL OR created_at IS NULL OR expires_at > created_at",
    )
    op.create_check_constraint(
        "ck_api_keys_idempotency_contract",
        "api_keys",
        "(idempotency_key_digest IS NULL AND request_fingerprint IS NULL) OR "
        "(created_by IS NOT NULL "
        "AND idempotency_key_digest ~ '^[0-9a-f]{64}$' "
        "AND request_fingerprint ~ '^[0-9a-f]{64}$')",
    )
    op.create_check_constraint(
        "ck_api_keys_post_contract_expiry_max",
        "api_keys",
        "idempotency_key_digest IS NULL OR expires_at IS NULL OR "
        "(created_at IS NOT NULL AND expires_at <= created_at + INTERVAL '365 days')",
    )
    op.create_check_constraint(
        "ck_api_keys_permanent_reason",
        "api_keys",
        "created_by IS NULL OR "
        "((expires_at IS NULL AND permanent_reason IS NOT NULL "
        "AND length(btrim(permanent_reason)) BETWEEN 10 AND 200) "
        "OR (expires_at IS NOT NULL AND permanent_reason IS NULL))",
    )
    op.execute(
        """
        ALTER TABLE public.api_keys
        ADD CONSTRAINT fk_api_keys_tenant
        FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) NOT VALID
        """
    )
    op.execute("ALTER TABLE public.api_keys VALIDATE CONSTRAINT fk_api_keys_tenant")
    op.execute(
        """
        ALTER TABLE public.api_keys
        ADD CONSTRAINT fk_api_keys_tenant_creator
        FOREIGN KEY (tenant_id, created_by)
        REFERENCES public.accounts(tenant_id, id) NOT VALID
        """
    )
    op.execute("ALTER TABLE public.api_keys VALIDATE CONSTRAINT fk_api_keys_tenant_creator")
    op.execute(
        """
        ALTER TABLE public.api_keys
        ADD CONSTRAINT fk_api_keys_tenant_rotated_from
        FOREIGN KEY (tenant_id, rotated_from_id)
        REFERENCES public.api_keys(tenant_id, id) NOT VALID
        """
    )
    op.execute("ALTER TABLE public.api_keys VALIDATE CONSTRAINT fk_api_keys_tenant_rotated_from")


def _install_permissions_contract() -> None:
    cases = []
    for role, permissions in _ROLE_PERMISSIONS.items():
        cases.append(f"WHEN '{role}' THEN '{json.dumps(permissions, separators=(',', ':'))}'::json")
    op.execute(
        f"""
        CREATE FUNCTION public.{_PERMISSIONS_FUNCTION}(requested_role text)
        RETURNS json
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT CASE requested_role {" ".join(cases)} ELSE NULL::json END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_PERMISSIONS_FUNCTION}(text) FROM PUBLIC")
    if _runtime_role_exists():
        # The rolling expand phase still permits drained old processes to issue
        # rows directly. PostgreSQL evaluates CHECK helpers as that writer.
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_PERMISSIONS_FUNCTION}(text) TO {_RUNTIME_ROLE}")
    op.create_check_constraint(
        "ck_api_keys_role_permissions",
        "api_keys",
        f"permissions::jsonb = public.{_PERMISSIONS_FUNCTION}(role)::jsonb",
    )


def _install_actor_and_audit_helpers() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_ACTOR_FUNCTION}(
            requested_tenant_id uuid,
            requested_actor_id uuid,
            requested_auth_session_id uuid,
            require_active_plan boolean
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE live_tenant public.tenants%ROWTYPE;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
                RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'API-key tenant context is not authorized';
            END IF;
            PERFORM pg_advisory_xact_lock_shared({_QUOTA_ROLLOUT_ADVISORY_LOCK_KEY});
            SELECT * INTO live_tenant
            FROM public.tenants
            WHERE id = requested_tenant_id
            FOR UPDATE;
            IF NOT FOUND
               OR live_tenant.status <> 'active'
               OR live_tenant.tenant_type <> 'brand' THEN
                RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'API-key tenant is not an active brand';
            END IF;
            IF require_active_plan
               AND live_tenant.plan_expires_at IS NOT NULL
               AND live_tenant.plan_expires_at <= CURRENT_TIMESTAMP THEN
                RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'API-key tenant plan is expired';
            END IF;

            PERFORM pg_advisory_xact_lock(
                hashtextextended('auth-session:' || requested_auth_session_id::text, 0)
            );
            IF NOT EXISTS (
                SELECT 1
                FROM public.auth_sessions AS auth_session
                JOIN public.accounts AS account
                  ON account.tenant_id = auth_session.tenant_id
                 AND account.id = auth_session.account_id
                WHERE auth_session.id = requested_auth_session_id
                  AND auth_session.account_id = requested_actor_id
                  AND auth_session.tenant_id = requested_tenant_id
                  AND auth_session.revoked_at IS NULL
                  AND auth_session.expires_at > CURRENT_TIMESTAMP
                  AND auth_session.auth_version = account.auth_version
                  AND account.is_active
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '28000', MESSAGE = 'API-key actor session is not live';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM public.account_roles AS account_role
                JOIN public.roles AS role
                  ON role.tenant_id = account_role.tenant_id
                 AND role.id = account_role.role_id
                JOIN public.role_permissions AS role_permission
                  ON role_permission.tenant_id = account_role.tenant_id
                 AND role_permission.role_id = account_role.role_id
                JOIN public.permissions AS permission
                  ON permission.tenant_id = role_permission.tenant_id
                 AND permission.id = role_permission.permission_id
                WHERE account_role.tenant_id = requested_tenant_id
                  AND account_role.account_id = requested_actor_id
                  AND role.name = 'admin'
                  AND permission.code = 'tenant:manage'
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'API-key actor is not a tenant administrator';
            END IF;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ACTOR_FUNCTION}(uuid, uuid, uuid, boolean) FROM PUBLIC")

    op.execute(
        f"""
        CREATE FUNCTION public.{_AUDIT_FUNCTION}(
            requested_audit_id uuid,
            requested_actor_id uuid,
            requested_tenant_id uuid,
            requested_action text,
            requested_key_id uuid,
            requested_details jsonb
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            INSERT INTO public.platform_audit_log (
                id, operator_id, target_tenant_id, action, resource, details,
                timestamp, created_at, updated_at
            ) VALUES (
                requested_audit_id,
                requested_actor_id::text,
                requested_tenant_id::text,
                requested_action,
                'api_key:' || requested_key_id::text,
                requested_details,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            );
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_AUDIT_FUNCTION}(uuid, uuid, uuid, text, uuid, jsonb) FROM PUBLIC")


def _install_row_guard() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_ROW_GUARD_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE derived_digest text;
        BEGIN
            IF NEW.key IS NOT NULL THEN
                IF NEW.key !~ '^ymt_[0-9a-f]{{48}}$' THEN
                    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'API key secret must be canonical high entropy';
                END IF;
                derived_digest := encode(sha256(convert_to(NEW.key, 'UTF8')), 'hex');
                IF NEW.key_digest IS NOT NULL AND NEW.key_digest <> derived_digest THEN
                    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'API key digest does not match legacy secret';
                END IF;
                IF NEW.key_prefix IS NOT NULL AND NEW.key_prefix <> left(NEW.key, 12) THEN
                    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'API key prefix does not match legacy secret';
                END IF;
                NEW.key_digest := derived_digest;
                NEW.key_prefix := left(NEW.key, 12);
            END IF;
            IF NEW.key_digest !~ '^[0-9a-f]{{64}}$'
               OR NEW.key_prefix !~ '^ymt_[0-9a-f]{{8}}$'
               OR btrim(NEW.name) = ''
               OR public.{_PERMISSIONS_FUNCTION}(NEW.role) IS NULL
               OR NEW.permissions::jsonb <> public.{_PERMISSIONS_FUNCTION}(NEW.role)::jsonb
               OR NOT (
                    (NEW.idempotency_key_digest IS NULL AND NEW.request_fingerprint IS NULL)
                    OR (NEW.created_by IS NOT NULL
                        AND NEW.idempotency_key_digest ~ '^[0-9a-f]{{64}}$'
                        AND NEW.request_fingerprint ~ '^[0-9a-f]{{64}}$')
               )
               OR (NEW.created_by IS NOT NULL AND NOT (
                    (NEW.expires_at IS NULL AND NEW.permanent_reason IS NOT NULL
                     AND length(btrim(NEW.permanent_reason)) BETWEEN 10 AND 200)
                    OR (NEW.expires_at IS NOT NULL AND NEW.permanent_reason IS NULL)
               ))
               OR (NEW.idempotency_key_digest IS NOT NULL
                   AND NEW.expires_at IS NOT NULL
                   AND NEW.expires_at > CURRENT_TIMESTAMP + INTERVAL '365 days') THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'API key metadata is invalid';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.key_digest IS DISTINCT FROM OLD.key_digest
                   OR NEW.key_prefix IS DISTINCT FROM OLD.key_prefix
                   OR NEW.role IS DISTINCT FROM OLD.role
                   OR NEW.permissions::jsonb IS DISTINCT FROM OLD.permissions::jsonb
                   OR NEW.created_by IS DISTINCT FROM OLD.created_by
                   OR NEW.rotated_from_id IS DISTINCT FROM OLD.rotated_from_id
                   OR NEW.idempotency_key_digest IS DISTINCT FROM OLD.idempotency_key_digest
                   OR NEW.request_fingerprint IS DISTINCT FROM OLD.request_fingerprint
                   OR NEW.permanent_reason IS DISTINCT FROM OLD.permanent_reason
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'API key identity is immutable';
                END IF;
                IF OLD.revoked AND NOT NEW.revoked THEN
                    RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'Revoked API key cannot be reactivated';
                END IF;
                IF NOT OLD.revoked AND NEW.revoked AND NEW.revoked_at IS NULL THEN
                    NEW.revoked_at := CURRENT_TIMESTAMP;
                END IF;
            END IF;
            IF NEW.revoked <> (NEW.revoked_at IS NOT NULL) THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'API key revocation state is inconsistent';
            END IF;
            NEW.updated_at := COALESCE(NEW.updated_at, CURRENT_TIMESTAMP);
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ROW_GUARD_FUNCTION}() FROM PUBLIC")
    op.execute(
        f"""
        CREATE TRIGGER trg_guard_api_key_row
        BEFORE INSERT OR UPDATE ON public.api_keys
        FOR EACH ROW EXECUTE FUNCTION public.{_ROW_GUARD_FUNCTION}()
        """
    )


def _install_runtime_interfaces() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_RESOLVE_FUNCTION}(requested_digest text)
        RETURNS TABLE (
            api_key_id uuid,
            tenant_id uuid,
            role text,
            permissions json,
            expires_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF requested_digest !~ '^[0-9a-f]{{64}}$' THEN
                RETURN;
            END IF;
            RETURN QUERY
            UPDATE public.api_keys AS api_key
            SET last_used_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            FROM public.tenants AS tenant
            WHERE api_key.key_digest = requested_digest
              AND NOT api_key.revoked
              AND api_key.revoked_at IS NULL
              AND (api_key.expires_at IS NULL OR api_key.expires_at > CURRENT_TIMESTAMP)
              AND tenant.id = api_key.tenant_id
              AND tenant.status = 'active'
              AND tenant.tenant_type = 'brand'
            RETURNING api_key.id, api_key.tenant_id, api_key.role::text, api_key.permissions, api_key.expires_at;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_RESOLVE_FUNCTION}(text) FROM PUBLIC")

    op.execute(
        f"""
        CREATE FUNCTION public.{_LOCK_FUNCTION}(requested_tenant_id uuid, requested_api_key_id uuid)
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE candidate public.api_keys%ROWTYPE;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
                RETURN false;
            END IF;
            PERFORM pg_advisory_xact_lock_shared({_QUOTA_ROLLOUT_ADVISORY_LOCK_KEY});
            PERFORM tenant.id
            FROM public.tenants AS tenant
            WHERE tenant.id = requested_tenant_id
              AND tenant.status = 'active'
              AND tenant.tenant_type = 'brand'
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN false;
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended('api-key:' || requested_api_key_id::text, 0));
            SELECT * INTO candidate
            FROM public.api_keys
            WHERE id = requested_api_key_id AND tenant_id = requested_tenant_id
            FOR UPDATE;
            RETURN FOUND
               AND NOT candidate.revoked
               AND candidate.revoked_at IS NULL
               AND (candidate.expires_at IS NULL OR candidate.expires_at > CURRENT_TIMESTAMP);
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_LOCK_FUNCTION}(uuid, uuid) FROM PUBLIC")

    op.execute(
        f"""
        CREATE FUNCTION public.{_ISSUE_FUNCTION}(
            requested_id uuid,
            requested_tenant_id uuid,
            requested_actor_id uuid,
            requested_auth_session_id uuid,
            requested_name text,
            requested_key_prefix text,
            requested_key_digest text,
            requested_role text,
            requested_expires_at timestamptz,
            requested_idempotency_digest text,
            requested_request_fingerprint text,
            requested_escrow_ciphertext bytea,
            requested_permanent_reason text,
            requested_audit_id uuid
        ) RETURNS TABLE(api_key_id uuid, replayed boolean, escrow_ciphertext bytea)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE active_count bigint;
        DECLARE derived_permissions json;
        DECLARE existing_ciphertext bytea;
        DECLARE existing_key public.api_keys%ROWTYPE;
        BEGIN
            IF btrim(COALESCE(requested_name, '')) = ''
               OR length(requested_name) > 200
               OR requested_key_prefix !~ '^ymt_[0-9a-f]{{8}}$'
               OR requested_key_digest !~ '^[0-9a-f]{{64}}$'
               OR requested_idempotency_digest !~ '^[0-9a-f]{{64}}$'
               OR requested_request_fingerprint !~ '^[0-9a-f]{{64}}$'
               OR requested_escrow_ciphertext IS NULL
               OR octet_length(requested_escrow_ciphertext) < 31 THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'API key issue metadata is invalid';
            END IF;
            derived_permissions := public.{_PERMISSIONS_FUNCTION}(requested_role);
            IF derived_permissions IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'API key role is invalid';
            END IF;
            PERFORM public.{_ACTOR_FUNCTION}(
                requested_tenant_id, requested_actor_id, requested_auth_session_id, true
            );
            PERFORM pg_advisory_xact_lock(hashtextextended(
                'api-key-idem:' || requested_tenant_id::text || ':' || requested_actor_id::text || ':' ||
                requested_idempotency_digest,
                0
            ));
            SELECT * INTO existing_key
            FROM public.api_keys
            WHERE tenant_id = requested_tenant_id
              AND created_by = requested_actor_id
              AND idempotency_key_digest = requested_idempotency_digest;
            IF FOUND THEN
                IF existing_key.request_fingerprint IS DISTINCT FROM requested_request_fingerprint THEN
                    RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'API key idempotency payload conflicts';
                END IF;
                SELECT backup.encrypted_key INTO existing_ciphertext
                FROM public.{_ESCROW_TABLE} AS backup
                WHERE backup.api_key_id = existing_key.id
                  AND backup.encryption_scheme = '{_APP_ESCROW_SCHEME}';
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING ERRCODE = 'XX001', MESSAGE = 'API key idempotency escrow is missing';
                END IF;
                RETURN QUERY SELECT existing_key.id, true, existing_ciphertext;
                RETURN;
            END IF;
            IF requested_expires_at IS NOT NULL AND (
                    requested_expires_at <= CURRENT_TIMESTAMP
                    OR requested_expires_at > CURRENT_TIMESTAMP + INTERVAL '365 days'
               )
               OR requested_expires_at IS NULL AND (
                    requested_permanent_reason IS NULL
                    OR length(btrim(requested_permanent_reason)) NOT BETWEEN 10 AND 200
               )
               OR requested_expires_at IS NOT NULL AND requested_permanent_reason IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'API key expiry acknowledgement is invalid';
            END IF;
            SELECT count(*) INTO active_count
            FROM public.api_keys
            WHERE tenant_id = requested_tenant_id
              AND NOT revoked
              AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP);
            IF active_count >= {_ACTIVE_API_KEY_LIMIT} THEN
                RAISE EXCEPTION USING ERRCODE = '54000', MESSAGE = 'API key active limit reached';
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended('api-key:' || requested_id::text, 0));
            INSERT INTO public.api_keys (
                id, tenant_id, name, key_prefix, key_digest, role, permissions,
                revoked, revoked_at, rotated_from_id, created_by, last_used_at,
                expires_at, idempotency_key_digest, request_fingerprint,
                permanent_reason, created_at, updated_at
            ) VALUES (
                requested_id, requested_tenant_id, requested_name,
                requested_key_prefix, requested_key_digest, requested_role,
                derived_permissions, false, NULL, NULL, requested_actor_id, NULL,
                requested_expires_at, requested_idempotency_digest,
                requested_request_fingerprint, requested_permanent_reason,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            );
            INSERT INTO public.{_ESCROW_TABLE} (
                api_key_id, encrypted_key, encryption_scheme
            ) VALUES (
                requested_id, requested_escrow_ciphertext, '{_APP_ESCROW_SCHEME}'
            );
            PERFORM public.{_AUDIT_FUNCTION}(
                requested_audit_id, requested_actor_id, requested_tenant_id,
                'api_key_issued', requested_id,
                jsonb_build_object(
                    'key_prefix', requested_key_prefix,
                    'role', requested_role,
                    'expires_at', requested_expires_at,
                    'permanent_reason', requested_permanent_reason
                )
            );
            RETURN QUERY SELECT requested_id, false, requested_escrow_ciphertext;
        END
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_ISSUE_FUNCTION}"
        "(uuid, uuid, uuid, uuid, text, text, text, text, timestamptz, "
        "text, text, bytea, text, uuid) FROM PUBLIC"
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_ROTATE_FUNCTION}(
            requested_old_id uuid,
            requested_new_id uuid,
            requested_tenant_id uuid,
            requested_actor_id uuid,
            requested_auth_session_id uuid,
            requested_name text,
            requested_key_prefix text,
            requested_key_digest text,
            requested_role text,
            requested_expires_at timestamptz,
            requested_idempotency_digest text,
            requested_request_fingerprint text,
            requested_escrow_ciphertext bytea,
            requested_audit_id uuid
        ) RETURNS TABLE(api_key_id uuid, replayed boolean, escrow_ciphertext bytea)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE derived_permissions json;
        DECLARE existing_ciphertext bytea;
        DECLARE existing_key public.api_keys%ROWTYPE;
        DECLARE lock_id uuid;
        DECLARE old_key public.api_keys%ROWTYPE;
        BEGIN
            IF btrim(COALESCE(requested_name, '')) = ''
               OR length(requested_name) > 200
               OR requested_key_prefix !~ '^ymt_[0-9a-f]{{8}}$'
               OR requested_key_digest !~ '^[0-9a-f]{{64}}$'
               OR requested_idempotency_digest !~ '^[0-9a-f]{{64}}$'
               OR requested_request_fingerprint !~ '^[0-9a-f]{{64}}$'
               OR requested_escrow_ciphertext IS NULL
               OR octet_length(requested_escrow_ciphertext) < 31 THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'API key rotation metadata is invalid';
            END IF;
            derived_permissions := public.{_PERMISSIONS_FUNCTION}(requested_role);
            IF derived_permissions IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'API key role is invalid';
            END IF;
            PERFORM public.{_ACTOR_FUNCTION}(
                requested_tenant_id, requested_actor_id, requested_auth_session_id, true
            );
            PERFORM pg_advisory_xact_lock(hashtextextended(
                'api-key-idem:' || requested_tenant_id::text || ':' || requested_actor_id::text || ':' ||
                requested_idempotency_digest,
                0
            ));
            SELECT * INTO existing_key
            FROM public.api_keys
            WHERE tenant_id = requested_tenant_id
              AND created_by = requested_actor_id
              AND idempotency_key_digest = requested_idempotency_digest;
            IF FOUND THEN
                IF existing_key.request_fingerprint IS DISTINCT FROM requested_request_fingerprint THEN
                    RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'API key idempotency payload conflicts';
                END IF;
                SELECT backup.encrypted_key INTO existing_ciphertext
                FROM public.{_ESCROW_TABLE} AS backup
                WHERE backup.api_key_id = existing_key.id
                  AND backup.encryption_scheme = '{_APP_ESCROW_SCHEME}';
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING ERRCODE = 'XX001', MESSAGE = 'API key idempotency escrow is missing';
                END IF;
                RETURN QUERY SELECT existing_key.id, true, existing_ciphertext;
                RETURN;
            END IF;
            IF requested_old_id = requested_new_id
               OR requested_expires_at IS NOT NULL AND (
                    requested_expires_at <= CURRENT_TIMESTAMP
                    OR requested_expires_at > CURRENT_TIMESTAMP + INTERVAL '365 days'
               ) THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'API key rotation metadata is invalid';
            END IF;
            FOR lock_id IN
                SELECT id FROM (VALUES (requested_old_id), (requested_new_id)) AS requested(id)
                ORDER BY id::text
            LOOP
                PERFORM pg_advisory_xact_lock(hashtextextended('api-key:' || lock_id::text, 0));
            END LOOP;
            SELECT * INTO old_key
            FROM public.api_keys
            WHERE id = requested_old_id AND tenant_id = requested_tenant_id
            FOR UPDATE;
            IF NOT FOUND OR old_key.revoked OR old_key.revoked_at IS NOT NULL
               OR old_key.expires_at IS NOT NULL AND old_key.expires_at <= CURRENT_TIMESTAMP THEN
                RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'API key is not live for rotation';
            END IF;
            IF requested_name IS DISTINCT FROM old_key.name
               OR requested_role IS DISTINCT FROM old_key.role
               OR requested_expires_at IS DISTINCT FROM old_key.expires_at
               OR derived_permissions::jsonb IS DISTINCT FROM old_key.permissions::jsonb THEN
                RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'API key rotation metadata is stale';
            END IF;
            UPDATE public.api_keys
            SET revoked = true, revoked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = requested_old_id;
            INSERT INTO public.api_keys (
                id, tenant_id, name, key_prefix, key_digest, role, permissions,
                revoked, revoked_at, rotated_from_id, created_by, last_used_at,
                expires_at, idempotency_key_digest, request_fingerprint,
                permanent_reason, created_at, updated_at
            ) VALUES (
                requested_new_id, requested_tenant_id, requested_name,
                requested_key_prefix, requested_key_digest, old_key.role,
                old_key.permissions, false, NULL, requested_old_id, requested_actor_id,
                NULL, old_key.expires_at, requested_idempotency_digest,
                requested_request_fingerprint, old_key.permanent_reason,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            );
            INSERT INTO public.{_ESCROW_TABLE} (
                api_key_id, encrypted_key, encryption_scheme
            ) VALUES (
                requested_new_id, requested_escrow_ciphertext, '{_APP_ESCROW_SCHEME}'
            );
            PERFORM public.{_AUDIT_FUNCTION}(
                requested_audit_id, requested_actor_id, requested_tenant_id,
                'api_key_rotated', requested_new_id,
                jsonb_build_object(
                    'key_prefix', requested_key_prefix,
                    'role', old_key.role,
                    'expires_at', old_key.expires_at,
                    'permanent_reason', old_key.permanent_reason,
                    'rotated_from_id', requested_old_id::text
                )
            );
            RETURN QUERY SELECT requested_new_id, false, requested_escrow_ciphertext;
        END
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_ROTATE_FUNCTION}"
        "(uuid, uuid, uuid, uuid, uuid, text, text, text, text, timestamptz, "
        "text, text, bytea, uuid) FROM PUBLIC"
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_REVOKE_FUNCTION}(
            requested_key_id uuid,
            requested_tenant_id uuid,
            requested_actor_id uuid,
            requested_auth_session_id uuid,
            requested_audit_id uuid
        ) RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE candidate public.api_keys%ROWTYPE;
        BEGIN
            PERFORM public.{_ACTOR_FUNCTION}(
                requested_tenant_id, requested_actor_id, requested_auth_session_id, false
            );
            PERFORM pg_advisory_xact_lock(hashtextextended('api-key:' || requested_key_id::text, 0));
            SELECT * INTO candidate
            FROM public.api_keys
            WHERE id = requested_key_id AND tenant_id = requested_tenant_id
            FOR UPDATE;
            IF NOT FOUND OR candidate.revoked OR candidate.revoked_at IS NOT NULL THEN
                RETURN NULL;
            END IF;
            UPDATE public.api_keys
            SET revoked = true, revoked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = requested_key_id;
            PERFORM public.{_AUDIT_FUNCTION}(
                requested_audit_id, requested_actor_id, requested_tenant_id,
                'api_key_revoked', requested_key_id,
                jsonb_build_object(
                    'key_prefix', candidate.key_prefix,
                    'role', candidate.role,
                    'expires_at', candidate.expires_at,
                    'permanent_reason', candidate.permanent_reason
                )
            );
            RETURN requested_key_id;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_REVOKE_FUNCTION}(uuid, uuid, uuid, uuid, uuid) FROM PUBLIC")

    if _runtime_role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_RESOLVE_FUNCTION}(text) TO {_RUNTIME_ROLE}")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_LOCK_FUNCTION}(uuid, uuid) TO {_RUNTIME_ROLE}")
        op.execute(
            f"GRANT EXECUTE ON FUNCTION public.{_ISSUE_FUNCTION}"
            f"(uuid, uuid, uuid, uuid, text, text, text, text, timestamptz, "
            f"text, text, bytea, text, uuid) TO {_RUNTIME_ROLE}"
        )
        op.execute(
            f"GRANT EXECUTE ON FUNCTION public.{_ROTATE_FUNCTION}"
            f"(uuid, uuid, uuid, uuid, uuid, text, text, text, text, timestamptz, "
            f"text, text, bytea, uuid) TO {_RUNTIME_ROLE}"
        )
        op.execute(
            f"GRANT EXECUTE ON FUNCTION public.{_REVOKE_FUNCTION}(uuid, uuid, uuid, uuid, uuid) TO {_RUNTIME_ROLE}"
        )


def _drop_interfaces() -> None:
    signatures = (
        f"public.{_REVOKE_FUNCTION}(uuid, uuid, uuid, uuid, uuid)",
        f"public.{_ROTATE_FUNCTION}(uuid, uuid, uuid, uuid, uuid, text, text, text, text, timestamptz, "
        "text, text, bytea, uuid)",
        f"public.{_ISSUE_FUNCTION}(uuid, uuid, uuid, uuid, text, text, text, text, timestamptz, "
        "text, text, bytea, text, uuid)",
        f"public.{_LOCK_FUNCTION}(uuid, uuid)",
        f"public.{_RESOLVE_FUNCTION}(text)",
    )
    if _runtime_role_exists():
        for signature in signatures:
            op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM {_RUNTIME_ROLE}")
    for signature in signatures:
        op.execute(f"DROP FUNCTION {signature}")
    op.execute(f"DROP FUNCTION public.{_AUDIT_FUNCTION}(uuid, uuid, uuid, text, uuid, jsonb)")
    op.execute(f"DROP FUNCTION public.{_ACTOR_FUNCTION}(uuid, uuid, uuid, boolean)")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    legacy_count = int(bind.execute(sa.text("SELECT count(*) FROM public.api_keys")).scalar_one())
    migration_key = _migration_key(required=legacy_count > 0)

    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    _preflight()
    _create_escrow(migration_key)
    _expand_schema()
    _install_permissions_contract()
    _install_actor_and_audit_helpers()
    _install_row_guard()
    _install_runtime_interfaces()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    secrets_by_id = _verified_expand_downgrade_secrets()

    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")

    _drop_interfaces()
    op.execute("DROP TRIGGER trg_guard_api_key_row ON public.api_keys")
    op.execute(f"DROP FUNCTION public.{_ROW_GUARD_FUNCTION}()")
    for api_key_id, secret in secrets_by_id.items():
        bind.execute(
            sa.text("UPDATE public.api_keys SET key=:secret WHERE id=:api_key_id"),
            {"secret": secret, "api_key_id": api_key_id},
        )
    op.drop_constraint("ck_api_keys_role_permissions", "api_keys", type_="check")
    if _runtime_role_exists():
        op.execute(f"REVOKE EXECUTE ON FUNCTION public.{_PERMISSIONS_FUNCTION}(text) FROM {_RUNTIME_ROLE}")
    op.execute(f"DROP FUNCTION public.{_PERMISSIONS_FUNCTION}(text)")
    op.drop_constraint("fk_api_keys_tenant_rotated_from", "api_keys", type_="foreignkey")
    op.drop_constraint("fk_api_keys_tenant_creator", "api_keys", type_="foreignkey")
    op.drop_constraint("fk_api_keys_tenant", "api_keys", type_="foreignkey")
    op.drop_constraint("ck_api_keys_permanent_reason", "api_keys", type_="check")
    op.drop_constraint("ck_api_keys_post_contract_expiry_max", "api_keys", type_="check")
    op.drop_constraint("ck_api_keys_idempotency_contract", "api_keys", type_="check")
    op.drop_constraint("ck_api_keys_expiry_after_creation", "api_keys", type_="check")
    op.drop_constraint("ck_api_keys_revocation_state", "api_keys", type_="check")
    op.drop_constraint("ck_api_keys_prefix_format", "api_keys", type_="check")
    op.drop_constraint("ck_api_keys_digest_format", "api_keys", type_="check")
    op.drop_index("uq_api_keys_tenant_rotated_from", table_name="api_keys")
    op.drop_index("ix_api_keys_tenant_active_expiry", table_name="api_keys")
    op.drop_index("uq_api_keys_tenant_creator_idempotency", table_name="api_keys")
    op.drop_index("uq_api_keys_key_digest", table_name="api_keys")
    op.drop_constraint("uq_api_keys_tenant_id_id", "api_keys", type_="unique")
    op.alter_column("api_keys", "key", existing_type=sa.String(length=100), nullable=False)
    op.drop_column("api_keys", "permanent_reason")
    op.drop_column("api_keys", "request_fingerprint")
    op.drop_column("api_keys", "idempotency_key_digest")
    op.drop_column("api_keys", "created_by")
    op.drop_column("api_keys", "rotated_from_id")
    op.drop_column("api_keys", "revoked_at")
    op.drop_column("api_keys", "key_digest")
    op.drop_column("api_keys", "key_prefix")
    op.drop_table(_ESCROW_TABLE, schema="public")
