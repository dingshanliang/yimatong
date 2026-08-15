"""document brand_profile support slots

Revision ID: k9f0a1b2c3d4
Revises: u9a3e4f5a6b7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k9f0a1b2c3d4"
down_revision: str | Sequence[str] | None = "u9a3e4f5a6b7"
branch_labels = None
depends_on = None

_PREVIOUS_COMMENT = "租户品牌定制槽位：primary_color/radius_preset/background_preset/hide_yimatong_brand"
_NEW_COMMENT = (
    "租户品牌定制槽位：primary_color/radius_preset/background_preset/hide_yimatong_brand/"
    "logo_url/support_phone/support_wecom_url"
)


def upgrade() -> None:
    # 注释级变更：brand_profile 是 JSON 列（ADR-0001），新增客服槽位不需要结构变更，
    # 只把列注释同步到当前 7 槽位白名单，避免 autogenerate 漂移。
    op.alter_column(
        "tenants",
        "brand_profile",
        existing_type=sa.JSON(),
        existing_nullable=True,
        existing_comment=_PREVIOUS_COMMENT,
        comment=_NEW_COMMENT,
    )


def downgrade() -> None:
    op.alter_column(
        "tenants",
        "brand_profile",
        existing_type=sa.JSON(),
        existing_nullable=True,
        existing_comment=_NEW_COMMENT,
        comment=_PREVIOUS_COMMENT,
    )
