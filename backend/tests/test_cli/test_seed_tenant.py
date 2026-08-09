"""A3-006: CLI seed 工具 - 租户种子测试"""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

backend_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(backend_dir))
os.environ.setdefault("database_url", "sqlite+aiosqlite://")
os.environ.setdefault("redis_url", "redis://localhost:6379/0")
os.environ.setdefault("secret_key", "test-secret-key-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ")

from app.cli.seed import app  # noqa: E402

runner = CliRunner()


class TestSeedTenant:
    @patch("app.cli.seed._ensure_tenant", new_callable=AsyncMock)
    def test_seed_tenant_creates_new(self, mock_ensure):
        mock_ensure.return_value = ("test-id", True)

        result = runner.invoke(app, ["tenant", "--name", "测试租户", "--slug", "test-tenant"])
        assert result.exit_code == 0
        assert "测试租户" in result.output

    @patch("app.cli.seed._ensure_tenant", new_callable=AsyncMock)
    def test_seed_tenant_idempotent(self, mock_ensure):
        mock_ensure.return_value = ("existing-id", False)

        result = runner.invoke(app, ["tenant", "--name", "T1", "--slug", "s1"])
        assert result.exit_code == 0
        assert "already exists" in result.output
