import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.api.v1.open_api import SkuCreateRequest
from app.models.product import BatchStatus, ProductAssetType
from app.schemas.product import (
    BrandCreate,
    ProductAssetCreate,
    ProductCreate,
    ProductionBatchCreate,
    ProductionBatchRead,
    SKUCreate,
)
from app.services.product import effective_production_batch_status
from app.utils import china_business_date
from app.utils.public_url import normalize_public_url


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "http://example.com/file.pdf",
        "https://user:pass@example.com/file.pdf",
        "https://localhost/file.pdf",
        "https://127.0.0.1/file.pdf",
        "https://10.0.0.8/file.pdf",
        "https://169.254.1.2/file.pdf",
        "file:///tmp/report.pdf",
    ],
)
def test_public_url_rejects_unsafe_targets(value: str):
    with pytest.raises(ValueError):
        normalize_public_url(value)


def test_public_url_accepts_https_and_managed_public_files():
    assert normalize_public_url(" https://assets.example.com/report.pdf ") == "https://assets.example.com/report.pdf"
    assert normalize_public_url("/api/v1/files/public/report.pdf") == "/api/v1/files/public/report.pdf"


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/api/v1/files/public/a/../../../auth/logout",
        "/api/v1/files/public/a/%2e%2e/%2e%2e/auth/logout",
        "/api/v1/files/public/a\\..\\..\\auth\\logout",
    ],
)
def test_public_url_rejects_managed_path_escape(unsafe_path):
    with pytest.raises(ValueError):
        normalize_public_url(unsafe_path)


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (BrandCreate, {"name": "   "}),
        (ProductCreate, {"brand_id": "00000000-0000-0000-0000-000000000001", "name": "   "}),
        (
            SKUCreate,
            {
                "product_id": "00000000-0000-0000-0000-000000000001",
                "code": "   ",
                "name": "SKU",
            },
        ),
    ],
)
def test_catalog_schemas_reject_blank_identity_fields(schema, payload):
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_catalog_schemas_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        BrandCreate.model_validate({"name": "品牌", "unexpected": True})


def test_open_api_specifications_are_trimmed_and_bounded():
    parsed = SkuCreateRequest.model_validate(
        {
            "product_id": "00000000-0000-0000-0000-000000000001",
            "code": "SKU",
            "name": "规格",
            "specifications": {" 容量 ": " 500ml "},
        }
    )
    assert parsed.specifications == {"容量": "500ml"}
    with pytest.raises(ValidationError):
        SkuCreateRequest.model_validate(
            {
                "product_id": "00000000-0000-0000-0000-000000000001",
                "code": "SKU",
                "name": "规格",
                "specifications": {"   ": "500ml"},
            }
        )


def test_batch_schema_rejects_inverted_dates():
    with pytest.raises(ValidationError):
        ProductionBatchCreate.model_validate(
            {
                "product_id": "00000000-0000-0000-0000-000000000001",
                "sku_id": "00000000-0000-0000-0000-000000000002",
                "batch_code": "B-1",
                "production_date": "2026-08-10",
                "expiry_date": "2026-08-09",
            }
        )


def test_production_batch_expiry_uses_shanghai_business_date_with_recall_priority(monkeypatch):
    frozen_utc = datetime(2026, 8, 10, 16, 30, tzinfo=UTC)
    current_date = china_business_date(frozen_utc)
    assert current_date == date(2026, 8, 11)

    active_batch = ProductionBatchRead(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        sku_id=uuid.uuid4(),
        batch_code="SHANGHAI-BOUNDARY",
        production_date=date(2026, 8, 1),
        expiry_date=date(2026, 8, 10),
        status=BatchStatus.active,
    )
    recalled_batch = active_batch.model_copy(update={"status": BatchStatus.recalled})

    assert effective_production_batch_status(active_batch, current_date=current_date) == BatchStatus.expired
    assert effective_production_batch_status(recalled_batch, current_date=current_date) == BatchStatus.recalled
    monkeypatch.setattr("app.schemas.product.china_business_date", lambda: current_date)
    assert active_batch.effective_status == BatchStatus.expired
    assert recalled_batch.effective_status == BatchStatus.recalled


def test_active_trust_asset_requires_current_public_evidence():
    base = {
        "asset_type": ProductAssetType.test_report,
        "name": "检测报告",
        "issuer": "检测机构",
    }
    with pytest.raises(ValidationError):
        ProductAssetCreate.model_validate(base)
    with pytest.raises(ValidationError):
        ProductAssetCreate.model_validate({**base, "file_url": "javascript:alert(1)"})
    with pytest.raises(ValidationError):
        ProductAssetCreate.model_validate(
            {
                **base,
                "file_url": "https://assets.example.com/report.pdf",
                "valid_until": date.today() - timedelta(days=1),
            }
        )

    asset = ProductAssetCreate.model_validate(
        {**base, "file_url": "/api/v1/files/public/report.pdf", "valid_until": date.today()}
    )
    assert asset.file_url == "/api/v1/files/public/report.pdf"
