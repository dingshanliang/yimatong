"""add connector secrets, benefit connector_id, claim delivery_status

Revision ID: 199331c89363
Revises: b3c4d5e6f7a8
Create Date: 2026-05-30 11:28:11.669688

EPIC-18: 外部权益连接器模型变更
- Connector 增加 secrets_encrypted 字段（AES-GCM 加密凭证）
- Connector 增加 created_at / updated_at 时间戳
- Benefit 增加 connector_id 外键（关联连接器）
- BenefitClaim 增加 delivery_status 字段（追踪外部发放状态）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '199331c89363'
down_revision: Union[str, None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Connector: 加密凭证 + 时间戳
    op.add_column('connectors', sa.Column('secrets_encrypted', sa.LargeBinary(), nullable=True))
    op.add_column('connectors', sa.Column(
        'created_at', sa.DateTime(timezone=True),
        server_default=sa.text('now()'), nullable=False,
    ))
    op.add_column('connectors', sa.Column(
        'updated_at', sa.DateTime(timezone=True),
        server_default=sa.text('now()'), nullable=False,
    ))

    # Benefit: 连接器外键
    op.add_column('benefits', sa.Column('connector_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_benefits_connector_id'), 'benefits', ['connector_id'], unique=False)
    op.create_foreign_key('fk_benefits_connector_id', 'benefits', 'connectors', ['connector_id'], ['id'])

    # BenefitClaim: 外部发放状态
    op.add_column('benefit_claims', sa.Column(
        'delivery_status', sa.String(length=20), nullable=False,
        server_default='not_required',
    ))


def downgrade() -> None:
    op.drop_column('benefit_claims', 'delivery_status')
    op.drop_constraint('fk_benefits_connector_id', 'benefits', type_='foreignkey')
    op.drop_index(op.f('ix_benefits_connector_id'), table_name='benefits')
    op.drop_column('benefits', 'connector_id')
    op.drop_column('connectors', 'updated_at')
    op.drop_column('connectors', 'created_at')
    op.drop_column('connectors', 'secrets_encrypted')
