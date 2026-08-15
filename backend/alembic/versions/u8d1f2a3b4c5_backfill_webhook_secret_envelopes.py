"""backfill webhook secret envelopes

Revision ID: u8d1f2a3b4c5
Revises: u8d0e1f2a3b4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u8d1f2a3b4c5"
down_revision: str | Sequence[str] | None = "u8d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.utils.crypto import EnvKeyProvider, encrypt_bytes, init_crypto

    op.execute("SET LOCAL lock_timeout='5s'")
    init_crypto(EnvKeyProvider())
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id,tenant_id,secret FROM webhook_endpoints WHERE secret_ciphertext IS NULL ORDER BY tenant_id,id")
    ).mappings()
    for row in rows:
        aad = f"webhook-endpoint:{row['tenant_id']}:{row['id']}".encode()
        ciphertext, nonce, key_id = encrypt_bytes(row["secret"].encode(), aad=aad)
        bind.execute(
            sa.text(
                "UPDATE webhook_endpoints SET secret_ciphertext=:ciphertext,secret_nonce=:nonce,"
                "secret_key_id=:key_id,config_version=1 WHERE tenant_id=:tenant_id AND id=:id AND secret_ciphertext IS NULL"
            ),
            {**row, "ciphertext": ciphertext, "nonce": nonce, "key_id": key_id},
        )
    op.execute("UPDATE webhook_endpoints SET config_version=1 WHERE config_version IS NULL")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(
        "UPDATE webhook_endpoints SET secret_ciphertext=NULL,secret_nonce=NULL,secret_key_id=NULL,config_version=NULL"
    )
