"""add consent evidence fields

Revision ID: d5e6f7a8b9c0
Revises: c7d8e9f0a1b2
Create Date: 2026-07-27 12:00:00.000000

yimatong-zgb1.5：consent_records 加合规证据链字段。
AGENTS.md §7 要求"授权场景、版本、时间、IP/UA、撤回时间"，原模型缺 scenario/policy_version/user_agent。
兼容期：旧行允许 NULL。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # scenario：授权场景
    op.add_column(
        "consent_records",
        sa.Column("scenario", sa.String(length=100), nullable=True),
    )
    # policy_version：政策版本
    op.add_column(
        "consent_records",
        sa.Column("policy_version", sa.String(length=50), nullable=True),
    )
    # user_agent：授权时 UA（与 scan_events.user_agent 一致，脱敏截断）
    op.add_column(
        "consent_records",
        sa.Column("user_agent", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("consent_records", "user_agent")
    op.drop_column("consent_records", "policy_version")
    op.drop_column("consent_records", "scenario")
