"""A4-001: CodeBatch 与 CodeItem 数据模型测试"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

backend_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(backend_dir))
os.environ.setdefault("database_url", "sqlite+aiosqlite://")
os.environ.setdefault("redis_url", "redis://localhost:6379/0")
os.environ.setdefault("secret_key", "test-secret-key-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ")

from app.models.base import Base  # noqa: E402
from app.models.code import CodeBatch, CodeBatchGenerationReceipt, CodeItem, CodeItemStatus  # noqa: E402
from app.models.export_log import ExportLog  # noqa: E402
from app.models.product import SKU, Product, ProductionBatch  # noqa: E402,F401

engine = create_async_engine("sqlite+aiosqlite://")
TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def setup_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


class TestCodeBatchModel:
    @pytest.mark.anyio
    async def test_create_code_batch(self):
        async with TestSession() as db:
            tenant_id, product_id, sku_id = uuid(), uuid(), uuid()
            production_batch = _production_batch(tenant_id, product_id, sku_id)
            batch = CodeBatch(
                tenant_id=tenant_id,
                product_id=product_id,
                sku_id=sku_id,
                production_batch_id=production_batch.id,
                batch_code="BATCH-001",
                quantity=1000,
                created_by=uuid(),
            )
            db.add_all([production_batch, batch])
            await db.flush()
            await db.refresh(batch)
            assert batch.id is not None
            assert batch.batch_code == "BATCH-001"
            assert batch.quantity == 1000
            assert batch.status == "pending"
            assert batch.production_batch_id == production_batch.id

    @pytest.mark.anyio
    async def test_code_batch_has_required_fields(self):
        mapper = inspect(CodeBatch)
        col_names = {c.key for c in mapper.mapper.column_attrs}
        required = {
            "id",
            "tenant_id",
            "product_id",
            "sku_id",
            "production_batch_id",
            "batch_code",
            "quantity",
            "status",
            "generation_mode",
            "created_by",
        }
        assert required.issubset(col_names)


class TestCodeItemModel:
    @pytest.mark.anyio
    async def test_create_code_item(self):
        async with TestSession() as db:
            tenant_id, product_id, sku_id = uuid(), uuid(), uuid()
            production_batch = _production_batch(tenant_id, product_id, sku_id)
            batch = CodeBatch(
                tenant_id=tenant_id,
                product_id=product_id,
                sku_id=sku_id,
                production_batch_id=production_batch.id,
                batch_code="BATCH-002",
                quantity=10,
                created_by=uuid(),
            )
            db.add_all([production_batch, batch])
            await db.flush()

            item = CodeItem(
                tenant_id=batch.tenant_id,
                code_batch_id=batch.id,
                public_id="ABC12345678",
            )
            db.add(item)
            await db.flush()
            await db.refresh(item)
            assert item.id is not None
            assert item.public_id == "ABC12345678"
            assert item.status == CodeItemStatus.created
            assert batch.production_batch_id == production_batch.id

    @pytest.mark.anyio
    async def test_code_item_has_required_fields(self):
        mapper = inspect(CodeItem)
        col_names = {c.key for c in mapper.mapper.column_attrs}
        required = {
            "id",
            "tenant_id",
            "code_batch_id",
            "public_id",
            "status",
            "activated_at",
            "bound_at",
            "revoked_at",
        }
        assert required.issubset(col_names)

    @pytest.mark.anyio
    async def test_code_item_status_enum(self):
        statuses = {s.value for s in CodeItemStatus}
        assert statuses == {"created", "activated", "bound", "expired", "revoked", "frozen"}


class TestCodeDeliveryConstraintModels:
    @pytest.mark.anyio
    @pytest.mark.parametrize("field", ["idempotency_digest", "request_fingerprint"])
    async def test_generation_receipt_rejects_non_hex_digest(self, field: str):
        values = {
            "tenant_id": uuid(),
            "created_by": uuid(),
            "idempotency_digest": "a" * 64,
            "request_fingerprint": "b" * 64,
        }
        values[field] = "g" * 64
        async with TestSession() as db:
            with pytest.raises(IntegrityError):
                db.add(CodeBatchGenerationReceipt(**values))
                await db.flush()

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("checksum", "key_id"),
        [
            ("g" * 64, "aes-master-v1"),
            ("a" * 64, "invalid key id"),
        ],
    )
    async def test_export_manifest_rejects_noncanonical_crypto_metadata(self, checksum: str, key_id: str):
        plaintext_size = 32
        async with TestSession() as db:
            with pytest.raises(IntegrityError):
                batch_id = uuid()
                db.add(
                    ExportLog(
                        tenant_id=uuid(),
                        account_id=uuid(),
                        export_type="code_csv",
                        resource_id=batch_id,
                        row_count=1,
                        status="completed",
                        code_batch_id=batch_id,
                        manifest_version=1,
                        checksum_sha256=checksum,
                        artifact_size_bytes=plaintext_size,
                        artifact_ciphertext=b"c" * (plaintext_size + 16),
                        artifact_nonce=b"n" * 12,
                        artifact_scheme="aes-256-gcm-v1",
                        artifact_key_id=key_id,
                    )
                )
                await db.flush()


def uuid():
    from uuid6 import uuid7

    return uuid7()


def _production_batch(tenant_id, product_id, sku_id):
    batch_id = uuid()
    return ProductionBatch(
        id=batch_id,
        tenant_id=tenant_id,
        product_id=product_id,
        sku_id=sku_id,
        batch_code=f"PB-{batch_id.hex[:12]}",
        production_date=date.today(),
        expiry_date=date.today() + timedelta(days=365),
    )
