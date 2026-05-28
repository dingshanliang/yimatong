"""A3-006: CLI seed 工具 - 产品种子测试"""

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


class TestSeedProduct:
    @patch("app.cli.seed.create_sku_if_needed", new_callable=AsyncMock)
    @patch("app.cli.seed.create_product_if_needed", new_callable=AsyncMock)
    @patch("app.cli.seed.create_brand_if_needed", new_callable=AsyncMock)
    @patch("app.cli.seed._get_tenant_by_slug", new_callable=AsyncMock)
    @patch("app.cli.seed.async_session")
    def test_seed_product_chain(
        self, mock_session, mock_get_tenant, mock_brand, mock_product, mock_sku
    ):
        mock_db = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_get_tenant.return_value = MagicMock(id="t1", slug="test")
        mock_brand.return_value = MagicMock(id="b1", name="测试品牌")
        mock_product.return_value = MagicMock(id="p1", name="测试产品")
        mock_sku.return_value = MagicMock(id="s1", code="SKU001")

        result = runner.invoke(
            app,
            ["product", "--tenant", "test", "--brand", "测试品牌", "--product-name", "测试产品", "--sku", "SKU001"],
        )
        assert result.exit_code == 0
        assert "测试品牌" in result.output
        assert "SKU001" in result.output
