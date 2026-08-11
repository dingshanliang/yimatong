"""Finalize protected consumer identity and recall-only batch mutation.

Revision ID: u6b3f4a5b6c7
Revises: u6b2e3f4a5b6
Create Date: 2026-08-11
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u6b3f4a5b6c7"
down_revision: str | None = "u6b2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_SIZE = 500
_RAW_UNIQUE = "uq_consumer_tenant_openid"
_RESTORE_INDEX = "uq_consumer_tenant_openid_restore"
_RESTORE_INDEXDEF = (
    "CREATE UNIQUE INDEX uq_consumer_tenant_openid_restore "
    "ON public.consumer_profiles USING btree (tenant_id, wechat_openid)"
)
_RECALL_SIGNATURE = "recall_production_batch(uuid,uuid,uuid,uuid,text)"


def _role_exists() -> bool:
    return bool(
        op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')")).scalar()
    )


def _crypto_ready() -> None:
    from app.utils.crypto import EnvKeyProvider, init_crypto

    init_crypto(EnvKeyProvider())


def _verify_all_openid_envelopes(*, require_raw: bool) -> None:
    from app.utils.crypto import decrypt_wechat_openid, hash_wechat_openid

    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id,tenant_id,wechat_openid_hash,wechat_openid_ciphertext,wechat_openid_nonce,"
                "wechat_openid_key_id"
                + (",wechat_openid" if require_raw else "")
                + " FROM public.consumer_profiles WHERE wechat_openid_hash IS NOT NULL ORDER BY tenant_id,id"
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
        if row["wechat_openid_hash"] != hash_wechat_openid(tenant_id, openid):
            raise RuntimeError(f"consumer OpenID digest drift for consumer_id={consumer_id}")
        if require_raw and row["wechat_openid"] != openid:
            raise RuntimeError(f"consumer OpenID rollback drift for consumer_id={consumer_id}")


def _has_protected_openids() -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM public.consumer_profiles WHERE wechat_openid_hash IS NOT NULL)"))
        .scalar()
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.execute("LOCK TABLE public.consumer_profiles IN SHARE ROW EXCLUSIVE MODE")
    incomplete = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id FROM public.consumer_profiles WHERE wechat_openid IS NOT NULL "
                "AND wechat_openid_hash IS NULL LIMIT 1"
            )
        )
        .first()
    )
    if incomplete is not None:
        raise RuntimeError("consumer OpenID cutover requires a complete protected backfill")
    if _has_protected_openids():
        _crypto_ready()
        _verify_all_openid_envelopes(require_raw=False)
    op.execute("DROP TRIGGER IF EXISTS trg_guard_legacy_wechat_openid_write ON public.consumer_profiles")
    op.execute("DROP FUNCTION IF EXISTS public.guard_legacy_wechat_openid_write()")
    op.drop_constraint(_RAW_UNIQUE, "consumer_profiles", type_="unique")
    op.drop_column("consumer_profiles", "wechat_openid")
    op.execute("ALTER TABLE public.connectors VALIDATE CONSTRAINT ck_connectors_config_has_no_plaintext_secrets")
    if _role_exists():
        op.execute("REVOKE UPDATE ON TABLE public.production_batches FROM yimatong_app")
        op.execute(
            "GRANT UPDATE(product_id,sku_id,batch_code,production_date,expiry_date,origin,"
            "updated_at,external_id,source_system) ON TABLE public.production_batches TO yimatong_app"
        )
        op.execute(f"REVOKE ALL ON FUNCTION public.{_RECALL_SIGNATURE} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_RECALL_SIGNATURE} TO yimatong_app")


def _column_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name='consumer_profiles' AND column_name=:name)"
            ),
            {"name": name},
        )
        .scalar()
    )


def _constraint_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='public.consumer_profiles'::regclass "
                "AND conname=:name)"
            ),
            {"name": name},
        )
        .scalar()
    )


def _restore_index_facts() -> tuple[bool, str | None]:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT idx.indisvalid,pg_get_indexdef(idx.indexrelid) FROM pg_index AS idx "
                "JOIN pg_class AS cls ON cls.oid=idx.indexrelid "
                "JOIN pg_namespace AS ns ON ns.oid=cls.relnamespace "
                "WHERE ns.nspname='public' AND cls.relname=:name"
            ),
            {"name": _RESTORE_INDEX},
        )
        .one_or_none()
    )
    return (False, None) if row is None else (bool(row[0]), str(row[1]))


def _restore_raw_openids() -> None:
    from app.utils.crypto import decrypt_wechat_openid

    while True:
        rows = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT id,tenant_id,wechat_openid_ciphertext,wechat_openid_nonce,wechat_openid_key_id "
                    "FROM public.consumer_profiles WHERE wechat_openid_hash IS NOT NULL "
                    "AND wechat_openid IS NULL ORDER BY tenant_id,id FOR UPDATE SKIP LOCKED LIMIT :limit"
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
            openid = decrypt_wechat_openid(
                tenant_id,
                consumer_id,
                bytes(row["wechat_openid_ciphertext"] or b""),
                bytes(row["wechat_openid_nonce"] or b""),
                str(row["wechat_openid_key_id"] or ""),
            )
            op.get_bind().execute(
                sa.text(
                    "UPDATE public.consumer_profiles SET wechat_openid=:openid "
                    "WHERE tenant_id=:tenant_id AND id=:consumer_id AND wechat_openid IS NULL "
                    "AND wechat_openid_ciphertext=:ciphertext AND wechat_openid_nonce=:nonce "
                    "AND wechat_openid_key_id=:key_id"
                ),
                {
                    "openid": openid,
                    "tenant_id": tenant_id,
                    "consumer_id": consumer_id,
                    "ciphertext": row["wechat_openid_ciphertext"],
                    "nonce": row["wechat_openid_nonce"],
                    "key_id": row["wechat_openid_key_id"],
                },
            )


def _prepare_restore_index() -> None:
    valid, definition = _restore_index_facts()
    if valid and definition == _RESTORE_INDEXDEF:
        return
    if definition is not None and definition != _RESTORE_INDEXDEF:
        raise RuntimeError(f"refusing unexpected index public.{_RESTORE_INDEX}")
    with op.get_context().autocommit_block():
        if definition is not None:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_RESTORE_INDEX}")
        try:
            op.execute(
                f"CREATE UNIQUE INDEX CONCURRENTLY {_RESTORE_INDEX} "
                "ON public.consumer_profiles (tenant_id,wechat_openid)"
            )
        except BaseException:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_RESTORE_INDEX}")
            raise
    valid, definition = _restore_index_facts()
    if not valid or definition != _RESTORE_INDEXDEF:
        raise RuntimeError(f"raw OpenID restore index public.{_RESTORE_INDEX} is not exact and valid")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    if not _column_exists("wechat_openid"):
        with op.get_context().autocommit_block():
            op.add_column("consumer_profiles", sa.Column("wechat_openid", sa.String(128), nullable=True))
    if _has_protected_openids():
        _crypto_ready()
        with op.get_context().autocommit_block():
            _restore_raw_openids()
        _verify_all_openid_envelopes(require_raw=True)
    if not _constraint_exists(_RAW_UNIQUE):
        _prepare_restore_index()
        op.execute(
            f"ALTER TABLE public.consumer_profiles ADD CONSTRAINT {_RAW_UNIQUE} UNIQUE USING INDEX {_RESTORE_INDEX}"
        )
    if _role_exists():
        op.execute("GRANT UPDATE ON TABLE public.production_batches TO yimatong_app")
