"""A3-006: CLI seed 工具 - 码种子测试"""

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


class TestSeedCode:
    @patch("app.cli.seed._generate_codes", new_callable=AsyncMock)
    @patch("app.cli.seed._get_tenant_by_slug", new_callable=AsyncMock)
    @patch("app.cli.seed.async_session")
    def test_seed_code_generates_count(self, mock_session, mock_get_tenant, mock_gen):
        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_get_tenant.return_value = MagicMock(id="t1", slug="test")
        mock_gen.return_value = 100

        result = runner.invoke(
            app,
            ["code", "--tenant", "test", "--batch-code", "BATCH001", "--count", "100"],
        )
        assert result.exit_code == 0
        assert "100" in result.output
