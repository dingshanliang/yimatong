"""backfill consumer phone envelopes

Revision ID: u6f1b2c3d4e5
Revises: u6f0a1b2c3d4
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u6f1b2c3d4e5"
down_revision: str | None = "u6f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_SIZE = 500


def _crypto_ready() -> None:
    from app.utils.crypto import EnvKeyProvider, init_crypto

    init_crypto(EnvKeyProvider())


def _backfill() -> None:
    from app.utils.crypto import decrypt_consumer_phone, decrypt_phone, encrypt_consumer_phone, hash_phone

    while True:
        rows = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT id,tenant_id,phone_hash,phone_encrypted FROM public.consumer_profiles "
                    "WHERE phone_hash IS NOT NULL AND phone_ciphertext IS NULL "
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
            if not row["phone_encrypted"]:
                raise RuntimeError(f"consumer phone backfill lacks a legacy envelope for consumer_id={row['id']}")
            tenant_id = uuid.UUID(str(row["tenant_id"]))
            consumer_id = uuid.UUID(str(row["id"]))
            phone = decrypt_phone(str(row["phone_encrypted"]))
            if hash_phone(phone) != row["phone_hash"]:
                raise RuntimeError(f"consumer phone digest drift for consumer_id={consumer_id}")
            ciphertext, nonce, key_id = encrypt_consumer_phone(tenant_id, consumer_id, phone)
            if decrypt_consumer_phone(tenant_id, consumer_id, ciphertext, nonce, key_id) != phone:
                raise RuntimeError(f"consumer phone envelope verification failed for consumer_id={consumer_id}")
            op.get_bind().execute(
                sa.text(
                    "UPDATE public.consumer_profiles SET phone_ciphertext=:ciphertext,phone_nonce=:nonce,"
                    "phone_key_id=:key_id,updated_at=statement_timestamp() "
                    "WHERE tenant_id=:tenant_id AND id=:consumer_id AND phone_ciphertext IS NULL"
                ),
                {
                    "ciphertext": ciphertext,
                    "nonce": nonce,
                    "key_id": key_id,
                    "tenant_id": tenant_id,
                    "consumer_id": consumer_id,
                },
            )


def _verify() -> None:
    from app.utils.crypto import decrypt_consumer_phone, hash_phone

    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id,tenant_id,phone_hash,phone_ciphertext,phone_nonce,phone_key_id "
                "FROM public.consumer_profiles WHERE phone_hash IS NOT NULL ORDER BY tenant_id,id"
            )
        )
        .mappings()
    )
    for row in rows:
        tenant_id = uuid.UUID(str(row["tenant_id"]))
        consumer_id = uuid.UUID(str(row["id"]))
        phone = decrypt_consumer_phone(
            tenant_id,
            consumer_id,
            bytes(row["phone_ciphertext"] or b""),
            bytes(row["phone_nonce"] or b""),
            str(row["phone_key_id"] or ""),
        )
        if hash_phone(phone) != row["phone_hash"]:
            raise RuntimeError(f"consumer phone v2 digest drift for consumer_id={consumer_id}")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    has_legacy = bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM consumer_profiles WHERE phone_hash IS NOT NULL)"))
        .scalar_one()
    )
    if not has_legacy:
        return
    _crypto_ready()
    with op.get_context().autocommit_block():
        _backfill()
    op.execute("LOCK TABLE public.consumer_profiles IN SHARE ROW EXCLUSIVE MODE")
    if op.get_bind().execute(
        sa.text(
            "SELECT id FROM public.consumer_profiles WHERE phone_hash IS NOT NULL "
            "AND (phone_ciphertext IS NULL OR phone_nonce IS NULL OR phone_key_id IS NULL) LIMIT 1"
        )
    ).first():
        raise RuntimeError("consumer phone backfill did not reach a protected fixed point")
    _verify()


def downgrade() -> None:
    # The expand columns intentionally retain the verified envelope.  The u6f0
    # downgrade restores the legacy representation before dropping those columns.
    return
