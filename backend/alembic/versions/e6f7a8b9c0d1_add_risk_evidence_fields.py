"""add risk evidence fields

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-07-27 14:00:00.000000

yimatong-zgb1.7 Decision 17：风险记录存储命中事实、证据质量、规则版本、风险等级。
risk_alerts 加 risk_level/rule_version/evidence_quality/rule_name 字段 + 复合索引。
兼容期：旧行允许 NULL。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("risk_alerts", sa.Column("risk_level", sa.String(length=10), nullable=True))
    op.add_column("risk_alerts", sa.Column("rule_version", sa.String(length=50), nullable=True))
    op.add_column(
        "risk_alerts", sa.Column("evidence_quality", sa.String(length=10), nullable=True)
    )
    op.add_column("risk_alerts", sa.Column("rule_name", sa.String(length=100), nullable=True))
    # yimatong-zgb1.7：按 public_id 查 active risk（claim_benefit 门禁用）
    op.create_index(
        "ix_risk_alerts_tenant_pid_resolved",
        "risk_alerts",
        ["tenant_id", "public_id", "resolved"],
    )


def downgrade() -> None:
    op.drop_index("ix_risk_alerts_tenant_pid_resolved", table_name="risk_alerts")
    op.drop_column("risk_alerts", "rule_name")
    op.drop_column("risk_alerts", "evidence_quality")
    op.drop_column("risk_alerts", "rule_version")
    op.drop_column("risk_alerts", "risk_level")
