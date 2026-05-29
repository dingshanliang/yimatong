"""export_log + consent_records + scan_events 分区 + 缺失索引

Revision ID: 0007
Revises: 0006
"""

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. export_logs 表
    op.create_table(
        "export_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("export_type", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="completed"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_export_logs_tenant_id", "export_logs", ["tenant_id"])

    # 2. consent_records 表
    op.create_table(
        "consent_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("consumer_id", sa.Uuid(), nullable=True),
        sa.Column("consent_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="granted"),
        sa.Column("public_id", sa.String(20), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["consumer_id"], ["consumer_profiles.id"]),
    )
    op.create_index("ix_consent_records_tenant_id", "consent_records", ["tenant_id"])
    op.create_index(
        "ix_consent_records_tenant_type", "consent_records", ["tenant_id", "consent_type"],
    )

    # 3. scan_events 分区：创建按月分区表结构
    # 注意：分区表要求主键包含分区键，所以需要重建表
    # 为了安全起见，使用原生 SQL
    op.execute("""
        -- 创建分区主表（仅在表为空或数据量小时安全执行）
        -- 由于开发阶段，直接重建
        DO $$
        BEGIN
            -- 检查 scan_events 是否已有分区
            IF NOT EXISTS (
                SELECT 1 FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = 'scan_events' AND c.relkind = 'p'
            ) THEN
                -- 重命名旧表
                ALTER TABLE scan_events RENAME TO scan_events_old;

                -- 创建分区主表
                CREATE TABLE scan_events (
                    id UUID NOT NULL,
                    tenant_id UUID NOT NULL,
                    public_id VARCHAR(20) NOT NULL,
                    scan_time TIMESTAMPTZ NOT NULL,
                    ip_hash VARCHAR(64),
                    user_agent VARCHAR(500),
                    is_first_scan BOOLEAN NOT NULL DEFAULT FALSE,
                    environment VARCHAR(20),
                    PRIMARY KEY (id, scan_time)
                ) PARTITION BY RANGE (scan_time);

                -- 创建默认分区
                CREATE TABLE scan_events_default PARTITION OF scan_events DEFAULT;

                -- 创建近期月份分区（2026-05 到 2026-12）
                CREATE TABLE scan_events_2026_05 PARTITION OF scan_events
                    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
                CREATE TABLE scan_events_2026_06 PARTITION OF scan_events
                    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
                CREATE TABLE scan_events_2026_07 PARTITION OF scan_events
                    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
                CREATE TABLE scan_events_2026_08 PARTITION OF scan_events
                    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
                CREATE TABLE scan_events_2026_09 PARTITION OF scan_events
                    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
                CREATE TABLE scan_events_2026_10 PARTITION OF scan_events
                    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
                CREATE TABLE scan_events_2026_11 PARTITION OF scan_events
                    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
                CREATE TABLE scan_events_2026_12 PARTITION OF scan_events
                    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

                -- 迁移旧数据
                INSERT INTO scan_events SELECT * FROM scan_events_old;

                -- 迁移索引
                CREATE INDEX IF NOT EXISTS ix_scan_events_tenant_id ON scan_events (tenant_id);
                CREATE INDEX IF NOT EXISTS ix_scan_events_public_id ON scan_events (public_id);

                -- 删除旧表
                DROP TABLE scan_events_old;
            END IF;
        END
        $$;
    """)

    # 4. scan_events 缺失索引
    op.execute("CREATE INDEX IF NOT EXISTS ix_scan_events_ip ON scan_events (ip_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_scan_events_environment ON scan_events (environment)")

    # 5. benefits 缺失索引
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_benefits_stock ON benefits (stock_total, stock_used)
    """)

    # 6. page_versions 缺失索引
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_page_versions_template_status
        ON page_versions (page_template_id, status)
    """)

    # 7. RLS 策略（新表）
    op.execute("ALTER TABLE export_logs ENABLE ROW LEVEL SECURITY")
    op.execute("""
        DO $$ BEGIN
            DROP POLICY IF EXISTS export_logs_tenant_isolation ON export_logs;
            CREATE POLICY export_logs_tenant_isolation ON export_logs
                USING (tenant_id = current_tenant_id());
        END $$;
    """)
    op.execute("ALTER TABLE consent_records ENABLE ROW LEVEL SECURITY")
    op.execute("""
        DO $$ BEGIN
            DROP POLICY IF EXISTS consent_records_tenant_isolation ON consent_records;
            CREATE POLICY consent_records_tenant_isolation ON consent_records
                USING (tenant_id = current_tenant_id());
        END $$;
    """)

    # 8. scan_events RLS（分区表需要单独处理）
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policy WHERE polname = 'scan_events_tenant_isolation'
            ) THEN
                ALTER TABLE scan_events ENABLE ROW LEVEL SECURITY;
                CREATE POLICY scan_events_tenant_isolation ON scan_events
                    USING (tenant_id = current_tenant_id());
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.drop_table("consent_records")
    op.drop_table("export_logs")
