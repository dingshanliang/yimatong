"""A4-005: 码包导出 CSV API 验收测试"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.code import CodeBatch, CodeBatchStatus, CodeItem
from app.models.export_log import ExportLog
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


async def _export_code_batch(
    client: AsyncClient,
    batch_id: str,
    headers: dict[str, str],
    *,
    reason: str = "交付印刷码表",
):
    return await client.post(
        f"/api/v1/code-batches/{batch_id}/export",
        json={"reason": reason},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )


def _platform_admin_headers() -> dict:
    from app.utils.security import create_access_token

    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=test-platform-csrf",
        "Origin": "http://localhost:3002",
        "X-Platform-CSRF": "test-platform-csrf",
    }


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def batch_with_codes(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "导出测试",
            "admin_email": "export@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "导出品牌"}, headers=headers)
    brand_id = brand.json()["id"]
    prod = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": "导出产品"},
        headers=headers,
    )
    product_id = prod.json()["id"]
    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": product_id, "code": "EXP-SKU", "name": "EXP SKU"},
        headers=headers,
    )
    sku_id = sku.json()["id"]
    production_batch = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "batch_code": "EXP-001",
            "production_date": "2026-05-31",
            "expiry_date": "2027-05-31",
        },
        headers=headers,
    )

    batch = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "production_batch_id": production_batch.json()["id"],
            "quantity": 5,
        },
        headers={**headers, "Idempotency-Key": "11111111-1111-4111-8111-111111111111"},
    )
    batch_id = batch.json()["id"]
    return tid, headers, batch_id


class TestCodeExport:
    @pytest.mark.anyio
    @pytest.mark.parametrize("reason", [None, "   ", "x" * 501])
    async def test_reason_validation_precedes_export_state_and_ledger(
        self, client: AsyncClient, db_session: AsyncSession, batch_with_codes, reason
    ):
        tenant_id, headers, batch_id = batch_with_codes
        before = await db_session.scalar(
            select(func.count()).select_from(ExportLog).where(ExportLog.tenant_id == uuid.UUID(tenant_id))
        )
        body = {} if reason is None else {"reason": reason}

        response = await client.post(
            f"/api/v1/code-batches/{batch_id}/export",
            json=body,
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )

        assert response.status_code == 422
        batch = await db_session.get(CodeBatch, uuid.UUID(batch_id))
        assert batch is not None and batch.status == CodeBatchStatus.completed
        after = await db_session.scalar(
            select(func.count()).select_from(ExportLog).where(ExportLog.tenant_id == uuid.UUID(tenant_id))
        )
        assert after == before

    @pytest.mark.anyio
    async def test_trigger_export_returns_csv(self, client: AsyncClient, batch_with_codes):
        _, headers, batch_id = batch_with_codes
        resp = await _export_code_batch(client, batch_id, headers)
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert f"codes-{batch_id}.csv" in resp.headers.get("content-disposition", "")
        assert "public_id" in resp.text
        assert resp.headers["X-Code-Manifest-Version"] == "1"
        assert resp.headers["X-Code-Item-Count"] == "5"
        assert len(resp.headers["X-Content-SHA256"]) == 64

    @pytest.mark.anyio
    async def test_export_contains_code_url(self, client: AsyncClient, batch_with_codes):
        _, headers, batch_id = batch_with_codes
        resp = await _export_code_batch(client, batch_id, headers)
        assert resp.status_code == 200
        assert "qr.yimatong.cn" in resp.text

    @pytest.mark.anyio
    async def test_export_neutralizes_spreadsheet_formula_metadata(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        batch_with_codes,
    ):
        _, headers, batch_id = batch_with_codes
        batch = await db_session.get(CodeBatch, uuid.UUID(batch_id))
        batch.product.name = '=HYPERLINK("https://evil.invalid")'
        await db_session.flush()

        response = await _export_code_batch(client, batch_id, headers)

        assert response.status_code == 200
        assert "'=HYPERLINK" in response.text

    @pytest.mark.anyio
    async def test_export_count_mismatch_creates_no_manifest(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        batch_with_codes,
    ):
        _, headers, batch_id = batch_with_codes
        batch_uuid = uuid.UUID(batch_id)
        item = await db_session.scalar(select(CodeItem).where(CodeItem.code_batch_id == batch_uuid).limit(1))
        await db_session.delete(item)
        await db_session.flush()

        response = await _export_code_batch(client, batch_id, headers)

        assert response.status_code == 409
        batch = await db_session.get(CodeBatch, batch_uuid)
        assert batch.status == CodeBatchStatus.completed
        assert batch.export_manifest_id is None

    @pytest.mark.anyio
    async def test_legacy_completed_batch_upgrades_contract_only_during_successful_export(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        batch_with_codes,
    ):
        _, headers, batch_id = batch_with_codes
        batch = await db_session.get(CodeBatch, uuid.UUID(batch_id))
        batch.contract_version = 0
        await db_session.flush()

        response = await _export_code_batch(client, batch_id, headers)

        assert response.status_code == 200
        await db_session.refresh(batch)
        assert batch.contract_version == 1
        assert batch.status == CodeBatchStatus.exported

    @pytest.mark.anyio
    async def test_legacy_invalid_shape_is_not_upgraded_or_exported(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        batch_with_codes,
    ):
        _, headers, batch_id = batch_with_codes
        batch = await db_session.get(CodeBatch, uuid.UUID(batch_id))
        batch.contract_version = 0
        batch.expected_item_count = 4
        await db_session.flush()

        response = await _export_code_batch(client, batch_id, headers)

        assert response.status_code == 409
        await db_session.refresh(batch)
        assert batch.contract_version == 0
        assert batch.status == CodeBatchStatus.completed
        assert batch.export_manifest_id is None

    @pytest.mark.anyio
    async def test_export_retry_returns_identical_manifest_bytes_without_duplicate_audit(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        batch_with_codes,
    ):
        _, headers, batch_id = batch_with_codes

        first = await _export_code_batch(client, batch_id, headers)
        replay = await _export_code_batch(client, batch_id, headers)

        assert first.status_code == replay.status_code == 200
        assert replay.content == first.content
        assert replay.headers["X-Content-SHA256"] == first.headers["X-Content-SHA256"]
        assert (
            await db_session.scalar(
                select(func.count()).select_from(ExportLog).where(ExportLog.code_batch_id == uuid.UUID(batch_id))
            )
            == 1
        )

    @pytest.mark.anyio
    async def test_export_retry_uses_persisted_bytes_after_catalog_metadata_changes(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        batch_with_codes,
    ):
        _, headers, batch_id = batch_with_codes
        batch = await db_session.get(CodeBatch, uuid.UUID(batch_id))

        first = await _export_code_batch(client, batch_id, headers)
        batch.product.name = "导出后修改的产品名"
        batch.sku.name = "导出后修改的SKU名"
        batch.sku.code = "CHANGED-AFTER-EXPORT"
        batch.production_batch.origin = "导出后修改的产地"
        await db_session.flush()
        replay = await _export_code_batch(client, batch_id, headers)

        assert first.status_code == replay.status_code == 200
        assert replay.content == first.content
        assert replay.headers["X-Content-SHA256"] == first.headers["X-Content-SHA256"]

    @pytest.mark.anyio
    async def test_export_artifact_tampering_fails_closed_without_log_leakage(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        batch_with_codes,
        caplog,
    ):
        _, headers, batch_id = batch_with_codes
        first = await _export_code_batch(client, batch_id, headers)
        manifest = await db_session.scalar(select(ExportLog).where(ExportLog.code_batch_id == uuid.UUID(batch_id)))
        public_id = first.text.splitlines()[1].split(",", 1)[0]
        original_ciphertext = bytes(manifest.artifact_ciphertext)
        assert first.content not in original_ciphertext
        manifest.artifact_ciphertext = bytes([original_ciphertext[0] ^ 1]) + original_ciphertext[1:]
        await db_session.flush()

        replay = await _export_code_batch(client, batch_id, headers)

        assert replay.status_code == 409
        assert public_id not in caplog.text
        assert original_ciphertext.hex() not in caplog.text

    @pytest.mark.anyio
    async def test_export_print_delivery_activation_requires_complete_evidence(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        batch_with_codes,
    ):
        _, headers, batch_id = batch_with_codes
        batch_uuid = uuid.UUID(batch_id)

        premature_print = await client.post(f"/api/v1/code-batches/{batch_id}/mark-printing", headers=headers)
        assert premature_print.status_code == 409

        exported = await _export_code_batch(client, batch_id, headers)
        printing = await client.post(f"/api/v1/code-batches/{batch_id}/mark-printing", headers=headers)
        missing_evidence = await client.post(f"/api/v1/code-batches/{batch_id}/mark-delivered", headers=headers)
        delivered = await client.post(
            f"/api/v1/code-batches/{batch_id}/mark-delivered",
            json={"reason": "printer handoff", "recipient": "华东包装厂", "confirm": "deliver"},
            headers=headers,
        )
        activated = await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)

        assert exported.status_code == printing.status_code == delivered.status_code == activated.status_code == 200
        assert missing_evidence.status_code == 422
        batch = await db_session.get(CodeBatch, batch_uuid)
        await db_session.refresh(batch)
        assert batch.status == CodeBatchStatus.activated
        assert batch.export_manifest_id is not None
        assert batch.exported_at is not None
        assert batch.printing_at is not None
        assert batch.delivered_at is not None
        assert batch.delivery_recipient == "华东包装厂"


async def _revoke_item(client: AsyncClient, headers: dict[str, str], item_id: str) -> None:
    resp = await client.post(
        f"/api/v1/code-items/{item_id}/revoke",
        json={"reason": "排除式导出测试作废", "confirm": "void"},
        headers=headers,
    )
    assert resp.status_code == 200


class TestCodeExportExcludeVoided:
    @pytest.mark.anyio
    async def test_first_export_excludes_voided_items_when_requested(
        self, client: AsyncClient, db_session: AsyncSession, batch_with_codes
    ):
        _, headers, batch_id = batch_with_codes
        batch_uuid = uuid.UUID(batch_id)
        item = await db_session.scalar(select(CodeItem).where(CodeItem.code_batch_id == batch_uuid).limit(1))
        await _revoke_item(client, headers, str(item.id))

        resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/export",
            json={"reason": "排除作废码补印", "exclude_voided": True},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )

        assert resp.status_code == 200
        assert resp.headers["X-Code-Item-Count"] == "4"
        assert resp.headers["X-Excluded-Item-Count"] == "1"
        assert item.public_id not in resp.text
        batch = await db_session.get(CodeBatch, batch_uuid)
        await db_session.refresh(batch)
        assert batch.status == CodeBatchStatus.exported
        assert batch.exported_item_count == 4

    @pytest.mark.anyio
    async def test_first_export_with_voided_items_still_blocked_without_flag(
        self, client: AsyncClient, db_session: AsyncSession, batch_with_codes
    ):
        _, headers, batch_id = batch_with_codes
        batch_uuid = uuid.UUID(batch_id)
        item = await db_session.scalar(select(CodeItem).where(CodeItem.code_batch_id == batch_uuid).limit(1))
        await _revoke_item(client, headers, str(item.id))

        resp = await _export_code_batch(client, batch_id, headers)

        assert resp.status_code == 409
        assert resp.json()["error_code"] == "CODE_BATCH_ITEM_NOT_DELIVERABLE"
        batch = await db_session.get(CodeBatch, batch_uuid)
        await db_session.refresh(batch)
        assert batch.status == CodeBatchStatus.completed
        assert batch.export_manifest_id is None

    @pytest.mark.anyio
    async def test_replay_export_regenerates_fresh_bytes_excluding_voided_items(
        self, client: AsyncClient, db_session: AsyncSession, batch_with_codes
    ):
        _, headers, batch_id = batch_with_codes
        batch_uuid = uuid.UUID(batch_id)
        first = await _export_code_batch(client, batch_id, headers)
        assert first.status_code == 200
        batch = await db_session.get(CodeBatch, batch_uuid)
        manifest_id_before = batch.export_manifest_id

        items = (
            await db_session.execute(select(CodeItem).where(CodeItem.code_batch_id == batch_uuid).limit(1))
        ).scalars().all()
        voided = items[0]
        await _revoke_item(client, headers, str(voided.id))

        reprint = await client.post(
            f"/api/v1/code-batches/{batch_id}/export",
            json={"reason": "作废后补印", "exclude_voided": True},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert reprint.status_code == 200
        assert reprint.headers["X-Code-Item-Count"] == "4"
        assert reprint.headers["X-Excluded-Item-Count"] == "1"
        assert voided.public_id not in reprint.text
        assert reprint.content != first.content
        await db_session.refresh(batch)
        assert batch.export_manifest_id == manifest_id_before
        assert batch.exported_item_count == 5

        plain_replay = await _export_code_batch(client, batch_id, headers)
        assert plain_replay.status_code == 200
        assert plain_replay.content == first.content

    @pytest.mark.anyio
    async def test_exclusion_export_without_voided_items_replays_original_bytes(
        self, client: AsyncClient, batch_with_codes
    ):
        _, headers, batch_id = batch_with_codes
        first = await _export_code_batch(client, batch_id, headers)

        replay = await client.post(
            f"/api/v1/code-batches/{batch_id}/export",
            json={"reason": "无作废码排除导出", "exclude_voided": True},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )

        assert replay.status_code == 200
        assert replay.content == first.content
        assert replay.headers["X-Excluded-Item-Count"] == "0"
