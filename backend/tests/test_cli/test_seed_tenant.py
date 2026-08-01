"""A3-006: CLI seed 工具 - 租户种子测试"""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

backend_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(backend_dir))
os.environ.setdefault("database_url", "sqlite+aiosqlite://")
os.environ.setdefault("redis_url", "redis://localhost:6379/0")
os.environ.setdefault("secret_key", "test-secret-key")

from app.cli.seed import app  # noqa: E402

runner = CliRunner()


class TestSeedTenant:
    @patch("app.cli.seed.create_tenant", new_callable=AsyncMock)
    @patch("app.cli.seed._get_tenant_by_slug", new_callable=AsyncMock)
    @patch("app.cli.seed.async_session")
    def test_seed_tenant_creates_new(self, mock_session, mock_get, mock_create):
        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get.return_value = None  # No existing tenant
        mock_create.return_value = MagicMock(
            id="test-id",
            name="测试租户",
            slug="test-tenant",
        )

        result = runner.invoke(app, ["tenant", "--name", "测试租户", "--slug", "test-tenant"])
        assert result.exit_code == 0
        assert "测试租户" in result.output

    @patch("app.cli.seed._get_tenant_by_slug", new_callable=AsyncMock)
    @patch("app.cli.seed.async_session")
    def test_seed_tenant_idempotent(self, mock_session, mock_get):
        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
        # Existing tenant found
        mock_get.return_value = MagicMock(id="existing-id", name="T1", slug="s1")

        result = runner.invoke(app, ["tenant", "--name", "T1", "--slug", "s1"])
        assert result.exit_code == 0
        assert "already exists" in result.output
