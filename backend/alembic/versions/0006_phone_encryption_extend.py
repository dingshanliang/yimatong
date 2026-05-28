"""encrypt phone fields in distributors and kyc_records

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-29

Replaces plaintext phone columns with encrypted + hash columns
for AES-GCM + HMAC-SHA256 compliance (PRD requirement).
"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # distributors: contact_phone → contact_phone_encrypted + contact_phone_hash
    op.add_column("distributors", sa.Column("contact_phone_encrypted", sa.Text(), nullable=True))
    op.add_column("distributors", sa.Column("contact_phone_hash", sa.String(64), nullable=True))
    op.drop_column("distributors", "contact_phone")

    # kyc_records: phone → phone_encrypted + phone_hash
    op.add_column("kyc_records", sa.Column("phone_encrypted", sa.Text(), nullable=False, server_default=""))
    op.add_column("kyc_records", sa.Column("phone_hash", sa.String(64), nullable=False, server_default=""))
    op.drop_column("kyc_records", "phone")


def downgrade() -> None:
    # kyc_records
    op.add_column("kyc_records", sa.Column("phone", sa.String(20), nullable=False, server_default=""))
    op.drop_column("kyc_records", "phone_hash")
    op.drop_column("kyc_records", "phone_encrypted")

    # distributors
    op.add_column("distributors", sa.Column("contact_phone", sa.String(20), nullable=True))
    op.drop_column("distributors", "contact_phone_hash")
    op.drop_column("distributors", "contact_phone_encrypted")
