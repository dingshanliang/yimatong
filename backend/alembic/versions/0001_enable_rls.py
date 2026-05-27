"""enable rls and tenant context

Revision ID: 0001_enable_rls
Revises:
Create Date: 2026-05-28

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_enable_rls"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 所有包含 tenant_id 的业务表
RLS_TABLES = [
    "organizations",
    "accounts",
    "roles",
    "permissions",
    "account_roles",
    "role_permissions",
]


def upgrade() -> None:
    # 创建 app.tenant_id 自定义配置参数（PostgreSQL 支持 SET LOCAL）
    # 注意：不需要显式创建，PostgreSQL 允许 SET LOCAL 使用任意参数名

    # 为每个业务表启用 RLS 并创建策略
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id::text = current_setting('app.tenant_id', true)) "
            f"WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"
        )

    # superuser 拥有 bypass RLS 权限，无需额外配置


def downgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
