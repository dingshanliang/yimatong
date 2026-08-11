"""Backfill protected OpenID envelopes and migrate connector secrets.

Revision ID: u6b2e3f4a5b6
Revises: u6b1d2e3f4a5
Create Date: 2026-08-11
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u6b2e3f4a5b6"
down_revision: str | None = "u6b1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_SIZE = 500
_SENSITIVE_KEYS = frozenset(
    {
        "oa_appsecret",
        "cert_private_key",
        "api_v3_key",
        "api_key",
        "api_secret",
        "callback_secret",
        "mch_key",
        "secret",
    }
)


def _crypto_ready() -> None:
    from app.utils.crypto import EnvKeyProvider, init_crypto

    init_crypto(EnvKeyProvider())


def _backfill_openids() -> None:
    from app.utils.crypto import encrypt_wechat_openid, hash_wechat_openid

    while True:
        rows = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT id,tenant_id,wechat_openid FROM public.consumer_profiles "
                    "WHERE wechat_openid IS NOT NULL AND wechat_openid_hash IS NULL "
                    "ORDER BY tenant_id,id FOR UPDATE SKIP LOCKED LIMIT :limit"
                ),
                {"limit": _BATCH_SIZE},
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        for row in rows:
            tenant_id = uuid.UUID(str(row["tenant_id"]))
            consumer_id = uuid.UUID(str(row["id"]))
            openid = str(row["wechat_openid"])
            ciphertext, nonce, key_id = encrypt_wechat_openid(tenant_id, consumer_id, openid)
            op.get_bind().execute(
                sa.text(
                    "UPDATE public.consumer_profiles SET wechat_openid_hash=:digest,"
                    "wechat_openid_ciphertext=:ciphertext,wechat_openid_nonce=:nonce,"
                    "wechat_openid_key_id=:key_id,updated_at=CURRENT_TIMESTAMP "
                    "WHERE tenant_id=:tenant_id AND id=:consumer_id AND wechat_openid_hash IS NULL"
                ),
                {
                    "digest": hash_wechat_openid(tenant_id, openid),
                    "ciphertext": ciphertext,
                    "nonce": nonce,
                    "key_id": key_id,
                    "tenant_id": tenant_id,
                    "consumer_id": consumer_id,
                },
            )


def _verify_openids() -> None:
    from app.utils.crypto import decrypt_wechat_openid, hash_wechat_openid

    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id,tenant_id,wechat_openid,wechat_openid_hash,wechat_openid_ciphertext,"
                "wechat_openid_nonce,wechat_openid_key_id FROM public.consumer_profiles "
                "WHERE wechat_openid IS NOT NULL ORDER BY tenant_id,id"
            )
        )
        .mappings()
    )
    for row in rows:
        tenant_id = uuid.UUID(str(row["tenant_id"]))
        consumer_id = uuid.UUID(str(row["id"]))
        openid = str(row["wechat_openid"])
        if (
            row["wechat_openid_hash"] != hash_wechat_openid(tenant_id, openid)
            or decrypt_wechat_openid(
                tenant_id,
                consumer_id,
                bytes(row["wechat_openid_ciphertext"] or b""),
                bytes(row["wechat_openid_nonce"] or b""),
                str(row["wechat_openid_key_id"] or ""),
            )
            != openid
        ):
            raise RuntimeError(f"consumer OpenID protection verification failed for consumer_id={consumer_id}")


def _restore_openids_to_raw() -> None:
    from app.utils.crypto import decrypt_wechat_openid

    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id,tenant_id,wechat_openid,wechat_openid_ciphertext,wechat_openid_nonce,"
                "wechat_openid_key_id FROM public.consumer_profiles WHERE wechat_openid_hash IS NOT NULL "
                "ORDER BY tenant_id,id FOR UPDATE"
            )
        )
        .mappings()
    )
    for row in rows:
        tenant_id = uuid.UUID(str(row["tenant_id"]))
        consumer_id = uuid.UUID(str(row["id"]))
        openid = decrypt_wechat_openid(
            tenant_id,
            consumer_id,
            bytes(row["wechat_openid_ciphertext"] or b""),
            bytes(row["wechat_openid_nonce"] or b""),
            str(row["wechat_openid_key_id"] or ""),
        )
        if row["wechat_openid"] is not None and row["wechat_openid"] != openid:
            raise RuntimeError(f"consumer OpenID rollback conflict for consumer_id={consumer_id}")
        op.get_bind().execute(
            sa.text(
                "UPDATE public.consumer_profiles SET wechat_openid=:openid "
                "WHERE tenant_id=:tenant_id AND id=:consumer_id AND wechat_openid IS NULL"
            ),
            {"openid": openid, "tenant_id": tenant_id, "consumer_id": consumer_id},
        )


def _install_legacy_openid_guard() -> None:
    op.execute(
        r"""
        CREATE FUNCTION public.guard_legacy_wechat_openid_write()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        BEGIN
            IF NEW.wechat_openid IS NOT NULL AND (
                NEW.wechat_openid_hash IS NULL OR NEW.wechat_openid_ciphertext IS NULL
                OR NEW.wechat_openid_nonce IS NULL OR NEW.wechat_openid_key_id IS NULL
                OR (TG_OP='UPDATE' AND NEW.wechat_openid IS DISTINCT FROM OLD.wechat_openid
                    AND NEW.wechat_openid_hash IS NOT DISTINCT FROM OLD.wechat_openid_hash)
            ) THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='legacy WeChat OpenID write requires a protected envelope';
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.guard_legacy_wechat_openid_write() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_guard_legacy_wechat_openid_write "
        "BEFORE INSERT OR UPDATE OF wechat_openid,wechat_openid_hash,wechat_openid_ciphertext,"
        "wechat_openid_nonce,wechat_openid_key_id ON public.consumer_profiles "
        "FOR EACH ROW EXECUTE FUNCTION public.guard_legacy_wechat_openid_write()"
    )


def _migrate_connector_secrets() -> None:
    from app.services.connectors.secrets import decrypt_secrets, encrypt_secrets

    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id,tenant_id,config,secrets_encrypted FROM public.connectors ORDER BY tenant_id,id FOR UPDATE"
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        config = dict(row["config"] or {})
        moved = {key: config[key] for key in _SENSITIVE_KEYS if key in config}
        if not moved:
            continue
        existing = decrypt_secrets(bytes(row["secrets_encrypted"])) if row["secrets_encrypted"] else {}
        conflicting = [key for key, value in moved.items() if key in existing and existing[key] != value]
        if conflicting:
            raise RuntimeError(f"connector secret conflict for connector_id={row['id']}")
        preexisting = sorted(key for key in moved if key in existing)
        merged = {**existing, **moved}
        sanitized = {key: value for key, value in config.items() if key not in _SENSITIVE_KEYS}
        op.get_bind().execute(
            sa.text(
                "INSERT INTO public.connector_secret_migration_backups("
                "connector_id,tenant_id,moved_secrets_encrypted,preexisting_secret_keys) "
                "VALUES(:connector_id,:tenant_id,:backup,:preexisting) "
                "ON CONFLICT (connector_id) DO NOTHING"
            ).bindparams(sa.bindparam("preexisting", type_=sa.JSON())),
            {
                "connector_id": row["id"],
                "tenant_id": row["tenant_id"],
                "backup": encrypt_secrets(moved),
                "preexisting": preexisting,
            },
        )
        op.get_bind().execute(
            sa.text(
                "UPDATE public.connectors SET config=:config,secrets_encrypted=:secrets,updated_at=CURRENT_TIMESTAMP "
                "WHERE tenant_id=:tenant_id AND id=:connector_id"
            ).bindparams(sa.bindparam("config", type_=sa.JSON())),
            {
                "config": sanitized,
                "secrets": encrypt_secrets(merged),
                "tenant_id": row["tenant_id"],
                "connector_id": row["id"],
            },
        )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    has_openids = bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM consumer_profiles WHERE wechat_openid IS NOT NULL)"))
        .scalar()
    )
    if has_openids:
        _crypto_ready()
        with op.get_context().autocommit_block():
            _backfill_openids()
    op.execute("LOCK TABLE public.consumer_profiles IN SHARE ROW EXCLUSIVE MODE")
    unprotected = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id FROM public.consumer_profiles WHERE wechat_openid IS NOT NULL "
                "AND wechat_openid_hash IS NULL LIMIT 1"
            )
        )
        .first()
    )
    if unprotected is not None:
        raise RuntimeError("consumer OpenID backfill did not reach a protected fixed point")
    has_openids = bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM public.consumer_profiles WHERE wechat_openid IS NOT NULL)"))
        .scalar()
    )
    if has_openids:
        _crypto_ready()
        _verify_openids()
    _install_legacy_openid_guard()
    with op.get_context().autocommit_block():
        _migrate_connector_secrets()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS trg_guard_legacy_wechat_openid_write ON public.consumer_profiles")
    op.execute("DROP FUNCTION IF EXISTS public.guard_legacy_wechat_openid_write()")
    # u6b3 restores the raw column before handing control back here.  Recheck
    # every protected row before clearing the envelope so a partial downgrade
    # can never discard the only recoverable copy of an OpenID.
    has_envelopes = bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM public.consumer_profiles WHERE wechat_openid_hash IS NOT NULL)"))
        .scalar()
    )
    if has_envelopes:
        _crypto_ready()
        _restore_openids_to_raw()
        _verify_openids()
    with op.get_context().autocommit_block():
        while True:
            result = op.get_bind().execute(
                sa.text(
                    "WITH batch AS (SELECT id FROM public.consumer_profiles "
                    "WHERE wechat_openid_hash IS NOT NULL ORDER BY tenant_id,id "
                    "LIMIT :limit FOR UPDATE) UPDATE public.consumer_profiles AS consumer "
                    "SET wechat_openid_hash=NULL,wechat_openid_ciphertext=NULL,"
                    "wechat_openid_nonce=NULL,wechat_openid_key_id=NULL "
                    "FROM batch WHERE consumer.id=batch.id"
                ),
                {"limit": _BATCH_SIZE},
            )
            if result.rowcount == 0:
                break
