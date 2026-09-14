"""extend connectors.config plaintext secret blocklist

Revision ID: 16d96e6016e9
Revises: b3d7e8f2a1c4

为有赞连接器（YouzanAdapter）扩展 connectors.config 的明文密钥 CHECK 约束：
新增 client_secret / access_token / refresh_token / token_expires_at / token_refreshed_at。
这些键只能存在于 secrets_encrypted（AES-256-GCM 信封），与
app/services/connectors/secrets.py 的 SENSITIVE_CONFIG_KEYS 保持同步。

纯约束收紧，无数据回填需求；若存量行已含这些键会约束失败——按定义这类数据本就违规，
升级前需先迁移至 secrets。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "16d96e6016e9"
down_revision: str | Sequence[str] | None = "b3d7e8f2a1c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_KEYS = "'client_secret','access_token','refresh_token','token_expires_at','token_refreshed_at'"
_OLD_KEYS = "'oa_appsecret','cert_private_key','api_v3_key','api_key','api_secret','callback_secret','mch_key','secret'"


def upgrade() -> None:
    op.execute("ALTER TABLE public.connectors DROP CONSTRAINT IF EXISTS ck_connectors_config_has_no_plaintext_secrets")
    op.execute(
        "ALTER TABLE public.connectors ADD CONSTRAINT ck_connectors_config_has_no_plaintext_secrets "
        f"CHECK (NOT (config::jsonb ?| ARRAY[{_OLD_KEYS},{_NEW_KEYS}]))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.connectors DROP CONSTRAINT IF EXISTS ck_connectors_config_has_no_plaintext_secrets")
    op.execute(
        "ALTER TABLE public.connectors ADD CONSTRAINT ck_connectors_config_has_no_plaintext_secrets "
        f"CHECK (NOT (config::jsonb ?| ARRAY[{_OLD_KEYS}]))"
    )
