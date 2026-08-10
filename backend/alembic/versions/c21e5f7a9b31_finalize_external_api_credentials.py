"""Finalize hashed external API credentials and close direct runtime writes.

Revision ID: c21e5f7a9b31
Revises: b10d4e6f8a20
Create Date: 2026-08-10

Apply this revision only after the digest-aware application is deployed and old
processes have drained.  The expand revision keeps legacy plaintext temporarily;
this contract revision verifies or captures every remaining legacy secret in
authenticated owner-only escrow, then drops the plaintext column.  Downgrade is
data-reversible only while every row has authenticated escrow evidence and the
required external key for each escrow scheme remains available; otherwise it
fails before schema or data mutation.
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

revision: str = "c21e5f7a9b31"
down_revision: str | None = "b10d4e6f8a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_ROLE = "yimatong_app"
_ESCROW_TABLE = "api_key_legacy_secret_backups"
_ROW_GUARD_FUNCTION = "guard_api_key_row"
_LEGACY_ESCROW_SCHEME = "legacy-pgcrypto-v1"
_APP_ESCROW_SCHEME = "app-aesgcm-json-v1"
_LEGACY_PERMANENT_REASON = "Legacy credential retained during hashed-key migration"


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
        raise RuntimeError("API_KEY_MIGRATION_KEY must contain at least 32 UTF-8 bytes for API-key contract")
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
    nonce = ciphertext[2:14]
    encrypted_payload = ciphertext[14:]
    try:
        plaintext = AESGCM(_aes_key(key_id)).decrypt(nonce, encrypted_payload, None)
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


def _verified_downgrade_secrets() -> dict[uuid.UUID, str]:
    bind = op.get_bind()
    missing = bind.execute(
        sa.text(
            f"""
            SELECT string_agg(api_key.id::text, ', ' ORDER BY api_key.id::text)
            FROM public.api_keys AS api_key
            LEFT JOIN public.{_ESCROW_TABLE} AS backup ON backup.api_key_id = api_key.id
            WHERE backup.api_key_id IS NULL
            """
        )
    ).scalar_one()
    if missing is not None:
        raise RuntimeError(f"API-key downgrade lacks rollback escrow for rows: {missing}")

    rows = bind.execute(
        sa.text(
            f"""
            SELECT api_key.id, api_key.key_prefix, api_key.key_digest,
                   backup.encryption_scheme, backup.encrypted_key
            FROM public.api_keys AS api_key
            JOIN public.{_ESCROW_TABLE} AS backup ON backup.api_key_id = api_key.id
            ORDER BY api_key.id
            """
        )
    ).mappings()
    material = list(rows)
    migration_key = _migration_key(
        required=any(row["encryption_scheme"] == _LEGACY_ESCROW_SCHEME for row in material)
    )
    secrets_by_id: dict[uuid.UUID, str] = {}
    for row in material:
        api_key_id = uuid.UUID(str(row["id"]))
        scheme = row["encryption_scheme"]
        if scheme == _LEGACY_ESCROW_SCHEME:
            assert migration_key is not None
            try:
                secret = bind.execute(
                    sa.text("SELECT pgp_sym_decrypt(:ciphertext, :migration_key)"),
                    {"ciphertext": row["encrypted_key"], "migration_key": migration_key},
                ).scalar_one()
            except Exception as exc:
                raise RuntimeError(f"API-key legacy escrow authentication failed for row {api_key_id}") from exc
        elif scheme == _APP_ESCROW_SCHEME:
            secret = _decrypt_app_escrow(api_key_id, bytes(row["encrypted_key"]))
        else:
            raise RuntimeError(f"API-key escrow scheme is unsupported for row {api_key_id}")
        if (
            re.fullmatch(r"ymt_[0-9a-f]{48}", secret) is None
            or secret[:12] != row["key_prefix"]
            or hashlib.sha256(secret.encode()).hexdigest() != row["key_digest"]
        ):
            raise RuntimeError(f"API-key escrow integrity check failed for row {api_key_id}")
        secrets_by_id[api_key_id] = secret
    return secrets_by_id


def _verify_finalize_preconditions(migration_key: str | None) -> None:
    bind = op.get_bind()
    invalid = bind.execute(
        sa.text(
            """
            SELECT string_agg(id::text, ', ' ORDER BY id::text)
            FROM public.api_keys
            WHERE key_prefix !~ '^ymt_[0-9a-f]{8}$'
               OR key_digest !~ '^[0-9a-f]{64}$'
               OR (key IS NOT NULL AND (
                    key !~ '^ymt_[0-9a-f]{48}$'
                    OR key_prefix <> left(key, 12)
                    OR key_digest <> encode(sha256(convert_to(key, 'UTF8')), 'hex')
               ))
            """
        )
    ).scalar_one()
    if invalid is not None:
        raise RuntimeError(f"API-key contract preflight found malformed rows: {invalid}")

    missing = bind.execute(
        sa.text(
            f"""
            SELECT string_agg(api_key.id::text, ', ' ORDER BY api_key.id::text)
            FROM public.api_keys AS api_key
            LEFT JOIN public.{_ESCROW_TABLE} AS backup ON backup.api_key_id = api_key.id
            WHERE backup.api_key_id IS NULL AND api_key.key IS NULL
            """
        )
    ).scalar_one()
    if missing is not None:
        raise RuntimeError(f"API-key contract lacks rollback escrow for rows: {missing}")

    if migration_key is None:
        return
    # Authentication/decryption is deliberately verified before the contract
    # performs any DDL or data mutation.  pgp_sym_decrypt rejects wrong keys or
    # modified ciphertext through the OpenPGP modification-detection code.
    mismatch = bind.execute(
        sa.text(
            f"""
            SELECT string_agg(api_key.id::text, ', ' ORDER BY api_key.id::text)
            FROM public.api_keys AS api_key
            JOIN public.{_ESCROW_TABLE} AS backup ON backup.api_key_id = api_key.id
            WHERE api_key.key IS NOT NULL
              AND backup.encryption_scheme = '{_LEGACY_ESCROW_SCHEME}'
              AND pgp_sym_decrypt(backup.encrypted_key, :migration_key) IS DISTINCT FROM api_key.key
            """
        ),
        {"migration_key": migration_key},
    ).scalar_one()
    if mismatch is not None:
        raise RuntimeError(f"API-key escrow does not match legacy rows: {mismatch}")


def _capture_unescrowed_legacy(migration_key: str | None) -> None:
    if migration_key is None:
        return
    op.get_bind().execute(
        sa.text(
            f"""
            INSERT INTO public.{_ESCROW_TABLE} (api_key_id, encrypted_key, encryption_scheme)
            SELECT api_key.id,
                   pgp_sym_encrypt(
                       api_key.key,
                       :migration_key,
                       'cipher-algo=aes256, compress-algo=0'
                   ),
                   '{_LEGACY_ESCROW_SCHEME}'
            FROM public.api_keys AS api_key
            LEFT JOIN public.{_ESCROW_TABLE} AS backup ON backup.api_key_id = api_key.id
            WHERE api_key.key IS NOT NULL AND backup.api_key_id IS NULL
            """
        ),
        {"migration_key": migration_key},
    )


def _legacy_permanent_reason_counts() -> tuple[int, int]:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT
                    count(*) FILTER (
                        WHERE created_by IS NULL
                          AND expires_at IS NULL
                          AND permanent_reason IS NULL
                    ) AS missing_provenance,
                    count(*) FILTER (
                        WHERE created_by IS NULL
                          AND expires_at IS NULL
                          AND permanent_reason = :provenance
                    ) AS existing_provenance
                FROM public.api_keys
                """
            ),
            {"provenance": _LEGACY_PERMANENT_REASON},
        )
        .one()
    )
    return int(row.missing_provenance), int(row.existing_provenance)


def _backfill_legacy_permanent_reason(expected_missing: int, existing_provenance: int) -> None:
    result = op.get_bind().execute(
        sa.text(
            """
            UPDATE public.api_keys
            SET permanent_reason = :provenance,
                updated_at = CURRENT_TIMESTAMP
            WHERE created_by IS NULL
              AND expires_at IS NULL
              AND permanent_reason IS NULL
            """
        ),
        {"provenance": _LEGACY_PERMANENT_REASON},
    )
    if result.rowcount != expected_missing:
        raise RuntimeError(
            "API-key legacy permanence provenance backfill count changed "
            f"(expected {expected_missing}, updated {result.rowcount})"
        )
    remaining, final_provenance = _legacy_permanent_reason_counts()
    if remaining != 0 or final_provenance != existing_provenance + expected_missing:
        raise RuntimeError("API-key legacy permanence provenance postcheck failed")


def _install_final_row_guard() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_ROW_GUARD_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF NEW.key_digest !~ '^[0-9a-f]{{64}}$'
               OR NEW.key_prefix !~ '^ymt_[0-9a-f]{{8}}$'
               OR btrim(NEW.name) = ''
               OR public.api_key_permissions_for_role(NEW.role) IS NULL
               OR NEW.permissions::jsonb <> public.api_key_permissions_for_role(NEW.role)::jsonb
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


def _install_expand_row_guard() -> None:
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
               OR public.api_key_permissions_for_role(NEW.role) IS NULL
               OR NEW.permissions::jsonb <> public.api_key_permissions_for_role(NEW.role)::jsonb
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


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    raw_count = int(bind.execute(sa.text("SELECT count(*) FROM public.api_keys WHERE key IS NOT NULL")).scalar_one())
    migration_key = _migration_key(required=raw_count > 0)
    _verify_finalize_preconditions(migration_key)
    missing_provenance, existing_provenance = _legacy_permanent_reason_counts()

    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    _capture_unescrowed_legacy(migration_key)
    op.execute("DROP TRIGGER trg_guard_api_key_row ON public.api_keys")
    op.execute(f"DROP FUNCTION public.{_ROW_GUARD_FUNCTION}()")
    _backfill_legacy_permanent_reason(missing_provenance, existing_provenance)
    op.drop_constraint("api_keys_key_key", "api_keys", type_="unique")
    op.drop_column("api_keys", "key")
    _install_final_row_guard()
    op.execute(
        f"""
        CREATE TRIGGER trg_guard_api_key_row
        BEFORE INSERT OR UPDATE ON public.api_keys
        FOR EACH ROW EXECUTE FUNCTION public.{_ROW_GUARD_FUNCTION}()
        """
    )
    op.drop_index("ix_api_keys_tenant_id", table_name="api_keys")
    if _runtime_role_exists():
        op.execute(
            f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
            f"ON TABLE public.api_keys FROM {_RUNTIME_ROLE}"
        )
        op.execute(f"GRANT SELECT ON TABLE public.api_keys TO {_RUNTIME_ROLE}")
        op.execute("REVOKE EXECUTE ON FUNCTION public.api_key_permissions_for_role(text) FROM yimatong_app")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    secrets_by_id = _verified_downgrade_secrets()

    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")
    op.execute("DROP TRIGGER trg_guard_api_key_row ON public.api_keys")
    op.execute(f"DROP FUNCTION public.{_ROW_GUARD_FUNCTION}()")
    op.add_column("api_keys", sa.Column("key", sa.String(length=100), nullable=True))
    for api_key_id, secret in secrets_by_id.items():
        bind.execute(
            sa.text("UPDATE public.api_keys SET key=:secret WHERE id=:api_key_id"),
            {"secret": secret, "api_key_id": api_key_id},
        )
    op.alter_column("api_keys", "key", existing_type=sa.String(length=100), nullable=False)
    op.create_unique_constraint("api_keys_key_key", "api_keys", ["key"])
    _install_expand_row_guard()
    op.execute(
        f"""
        CREATE TRIGGER trg_guard_api_key_row
        BEFORE INSERT OR UPDATE ON public.api_keys
        FOR EACH ROW EXECUTE FUNCTION public.{_ROW_GUARD_FUNCTION}()
        """
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"], unique=False)
    if _runtime_role_exists():
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.api_keys TO {_RUNTIME_ROLE}")
        op.execute("GRANT EXECUTE ON FUNCTION public.api_key_permissions_for_role(text) TO yimatong_app")
