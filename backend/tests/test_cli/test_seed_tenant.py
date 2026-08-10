"""A3-006: CLI seed 工具 - 租户种子测试"""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from typer.testing import CliRunner

backend_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(backend_dir))
os.environ.setdefault("database_url", "sqlite+aiosqlite://")
os.environ.setdefault("redis_url", "redis://localhost:6379/0")
os.environ.setdefault("secret_key", "test-secret-key-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ")

from app.cli.seed import app, settings  # noqa: E402

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

    @patch("app.cli.seed._ensure_tenant", new_callable=AsyncMock)
    def test_all_refuses_non_demo_target_without_explicit_override(self, mock_ensure):
        result = runner.invoke(app, ["all", "--slug", "customer"])

        assert result.exit_code == 2
        assert "非 demo 租户" in result.output
        mock_ensure.assert_not_awaited()

    @patch("app.cli.seed._ensure_tenant", new_callable=AsyncMock)
    def test_all_refuses_production_without_explicit_override(self, mock_ensure, monkeypatch):
        monkeypatch.setattr(settings, "environment", "production")

        result = runner.invoke(app, ["all"])

        assert result.exit_code == 2
        assert "production" in result.output
        mock_ensure.assert_not_awaited()

    @patch("subprocess.run")
    def test_demo_wrapper_passes_explicit_target(self, run):
        run.return_value = Mock(returncode=0)

        result = runner.invoke(app, ["demo"])

        assert result.exit_code == 0
        command = run.call_args.args[0]
        assert command[-3:] == ["generate", "--target", "demo"]
        assert "--allow-production" not in command

    @patch("subprocess.run")
    def test_demo_wrapper_rejects_explicit_production_authority_before_subprocess(self, run, monkeypatch):
        run.return_value = Mock(returncode=0)
        monkeypatch.setattr(settings, "environment", "production")

        result = runner.invoke(app, ["demo", "--allow-production"])

        assert result.exit_code == 2
        assert "内置演示账号或密码" in result.output
        run.assert_not_called()
