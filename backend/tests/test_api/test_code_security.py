"""码管理安全加固测试（P0 修复验证）"""

import uuid
from collections.abc import AsyncGenerator
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import DataError, DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import code_batches as code_batches_api
from app.api.v1 import imports
from app.core.database import get_db
from app.main import app
from app.models.code import CodeBatch, CodeBatchStatus, CodeItem, CodeItemStatus, CodeType
from app.models.export_log import ExportLog
from app.models.product import SKU, BatchStatus, Brand, Product, ProductionBatch
from app.services import code as code_service
from app.services import import_admission
from app.services import import_service as import_service_module
from app.services.import_service import ExcelImportService
from app.services.public_id import generate_public_id
from app.services.redis_cache import SharedSecurityCacheUnavailable
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=test-platform-csrf",
        "Origin": "http://localhost:3002",
        "X-Platform-CSRF": "test-platform-csrf",
    }


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def allow_shared_import_rate_limit(monkeypatch):
    monkeypatch.setattr(
        import_admission._import_rate_cache,
        "rate_limit_check_shared",
        AsyncMock(return_value=(True, 9)),
    )


@pytest.fixture
async def tenant_with_auth(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "码测试",
            "admin_email": "code@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return tid, {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def sku_with_auth(client: AsyncClient, tenant_with_auth):
    tid, headers = tenant_with_auth
    brand_resp = await client.post("/api/v1/brands", json={"name": "码品牌"}, headers=headers)
    brand_id = brand_resp.json()["id"]
    prod_resp = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": "码产品"},
        headers=headers,
    )
    product_id = prod_resp.json()["id"]
    sku_resp = await client.post(
        "/api/v1/skus",
        json={"product_id": product_id, "code": "SKU-001", "name": "测试 SKU"},
        headers=headers,
    )
    sku_id = sku_resp.json()["id"]
    pb_resp = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "batch_code": "PB-001",
            "production_date": "2024-01-01",
            "expiry_date": str(date.today() + timedelta(days=365)),
        },
        headers=headers,
    )
    production_batch_id = pb_resp.json()["id"]
    return product_id, sku_id, production_batch_id


@pytest.fixture
async def code_batch_with_auth(client: AsyncClient, tenant_with_auth, sku_with_auth):
    tid, headers = tenant_with_auth
    product_id, sku_id, production_batch_id = sku_with_auth
    resp = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "production_batch_id": production_batch_id,
            "quantity": 5,
        },
        headers={**headers, "Idempotency-Key": "11111111-1111-4111-8111-111111111111"},
    )
    assert resp.status_code == 201
    batch_id = uuid.UUID(resp.json()["id"])

    # 获取批次下的码项
    items_resp = await client.get("/api/v1/code-items", params={"code_batch_id": batch_id}, headers=headers)
    items = items_resp.json()["items"]
    item_id = uuid.UUID(items[0]["id"])
    return tid, headers, batch_id, item_id


@pytest.fixture
async def imported_code_batch_with_auth(client: AsyncClient, tenant_with_auth, sku_with_auth):
    tid, headers = tenant_with_auth
    product_id, sku_id, production_batch_id = sku_with_auth
    response = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "production_batch_id": production_batch_id,
            "quantity": 2,
            "source": "imported",
        },
        headers={**headers, "Idempotency-Key": "22222222-2222-4222-8222-222222222222"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == CodeBatchStatus.generating
    assert response.json()["generated_count"] == 0
    assert response.json()["expected_item_count"] == 2
    assert response.json()["source"] == "imported"
    return tid, headers, uuid.UUID(response.json()["id"])


class TestStatusMachineProtection:
    """1.1 状态机绕过修复测试"""

    @pytest.mark.anyio
    async def test_patch_status_directly_rejected(self, client: AsyncClient, code_batch_with_auth):
        _, headers, _, item_id = code_batch_with_auth
        resp = await client.patch(
            f"/api/v1/code-items/{item_id}",
            json={"status": "expired"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert "Direct status modification is not allowed" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_patch_other_fields_allowed(self, client: AsyncClient, code_batch_with_auth):
        _, headers, _, item_id = code_batch_with_auth
        # 当前 update_code_item 只允许修改 status（已被禁用），所以 PATCH 任何字段都会 400
        # 如果将来有其他可修改字段，此测试需更新
        resp = await client.patch(
            f"/api/v1/code-items/{item_id}",
            json={"status": "activated"},
            headers=headers,
        )
        assert resp.status_code == 400


class TestSingleCodeVoidProtection:
    @pytest.mark.anyio
    async def test_single_code_void_requires_reason_and_confirmation(
        self,
        client: AsyncClient,
        code_batch_with_auth,
    ):
        _, headers, _, item_id = code_batch_with_auth

        response = await client.post(f"/api/v1/code-items/{item_id}/revoke", headers=headers)

        assert response.status_code == 422

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "body",
        [
            {"reason": "   ", "confirm": "void"},
            {"reason": "x" * 201, "confirm": "void"},
            {"reason": "confirmed incident", "confirm": "revoke"},
            {"reason": "confirmed incident", "confirm": "void", "unexpected": True},
        ],
    )
    async def test_single_code_void_rejects_invalid_body(
        self,
        client: AsyncClient,
        code_batch_with_auth,
        body,
    ):
        _, headers, _, item_id = code_batch_with_auth

        response = await client.post(
            f"/api/v1/code-items/{item_id}/revoke",
            json=body,
            headers=headers,
        )

        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_single_code_void_records_trimmed_reason_and_actor(
        self,
        client: AsyncClient,
        code_batch_with_auth,
    ):
        _, headers, _, item_id = code_batch_with_auth

        response = await client.post(
            f"/api/v1/code-items/{item_id}/revoke",
            json={"reason": "  confirmed incident  ", "confirm": "void"},
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["status"] == "revoked"
        audit = await client.get("/api/v1/audit-logs?action=code_void", headers=headers)
        assert audit.status_code == 200
        assert audit.json()["total"] == 1
        event = audit.json()["items"][0]
        assert event["operator"]["id"] == "00000000-0000-0000-0000-000000000001"
        assert event["details"] == {
            "reason": "confirmed incident",
            "before": {"status": "created"},
            "after": {"status": "revoked"},
        }

    @pytest.mark.anyio
    async def test_single_code_void_audit_failure_rolls_back_lifecycle(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        code_batch_with_auth,
        monkeypatch,
    ):
        _, headers, _, item_id = code_batch_with_auth

        async def rollbacking_get_db():
            transaction = await db_session.begin_nested()
            try:
                yield db_session
                await transaction.commit()
            except BaseException:
                await transaction.rollback()
                raise

        app.dependency_overrides[get_db] = rollbacking_get_db
        monkeypatch.setattr(
            "app.services.audit.write_audit_log",
            AsyncMock(side_effect=RuntimeError("audit unavailable")),
        )

        with pytest.raises(RuntimeError, match="audit unavailable"):
            await client.post(
                f"/api/v1/code-items/{item_id}/revoke",
                json={"reason": "confirmed incident", "confirm": "void"},
                headers=headers,
            )

        item = await db_session.get(CodeItem, item_id)
        await db_session.refresh(item)
        assert item.status == CodeItemStatus.created
        assert item.revoked_at is None


class TestPermissionEnforcement:
    """1.2 权限校验测试"""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "path_template",
        [
            "/api/v1/code-items?code_batch_id={batch_id}",
            "/api/v1/code-items/{item_id}",
            "/api/v1/code-items/{item_id}/pair",
        ],
    )
    async def test_viewer_cannot_read_raw_code_item_identifiers(
        self,
        client: AsyncClient,
        code_batch_with_auth,
        tenant_with_auth,
        path_template,
    ):
        tenant_id, _ = tenant_with_auth
        _, _, batch_id, item_id = code_batch_with_auth
        viewer_token = create_access_token(
            tenant_id,
            "00000000-0000-0000-0000-000000000003",
            "viewer",
        )

        response = await client.get(
            path_template.format(batch_id=batch_id, item_id=item_id),
            headers={"Authorization": f"Bearer {viewer_token}"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Missing permission: code:export"


class TestCodeOperationAdmission:
    @pytest.mark.anyio
    async def test_shared_bucket_hides_tenant_and_durable_principal(self, monkeypatch, caplog):
        keys: list[str] = []

        async def rate_check(key, *_args):
            keys.append(key)
            return True, 9

        monkeypatch.setattr(
            code_service._code_operation_rate_cache,
            "rate_limit_check_shared",
            rate_check,
        )
        tenant_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
        account_id = uuid.UUID("22222222-2222-4222-8222-222222222222")

        await code_service.enforce_code_operation_rate_limit(tenant_id, account_id)

        assert len(keys) == 1
        assert str(tenant_id) not in keys[0]
        assert str(account_id) not in keys[0]
        assert str(tenant_id) not in caplog.text
        assert str(account_id) not in caplog.text

    @pytest.mark.anyio
    async def test_generation_limit_rejects_before_service(
        self,
        client,
        tenant_with_auth,
        sku_with_auth,
        monkeypatch,
    ):
        _, headers = tenant_with_auth
        product_id, sku_id, production_batch_id = sku_with_auth
        create = AsyncMock()
        monkeypatch.setattr(code_batches_api, "create_code_batch", create)
        monkeypatch.setattr(
            code_service._code_operation_rate_cache,
            "rate_limit_check_shared",
            AsyncMock(return_value=(False, 0)),
        )

        response = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 1,
            },
            headers={**headers, "Idempotency-Key": "11111111-1111-4111-8111-111111111111"},
        )

        assert response.status_code == 429
        assert response.headers["Retry-After"] == str(code_service.CODE_OPERATION_RATE_LIMIT_WINDOW_SECONDS)
        create.assert_not_awaited()

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "idempotency_key",
        [
            None,
            "not-a-uuid",
            "11111111-1111-4111-8111-11111111111A",
            "{11111111-1111-4111-8111-111111111111}",
        ],
    )
    async def test_generation_requires_canonical_uuid_idempotency_key(
        self,
        client,
        tenant_with_auth,
        sku_with_auth,
        monkeypatch,
        idempotency_key,
    ):
        _, headers = tenant_with_auth
        product_id, sku_id, production_batch_id = sku_with_auth
        create = AsyncMock()
        monkeypatch.setattr(code_batches_api, "create_code_batch", create)
        request_headers = dict(headers)
        if idempotency_key is not None:
            request_headers["Idempotency-Key"] = idempotency_key

        response = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 1,
            },
            headers=request_headers,
        )

        assert response.status_code == 422
        create.assert_not_awaited()

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "overrides",
        [
            {"source": "unsupported"},
            {"source": "imported", "code_type": "paired"},
            {"source": "imported", "generation_mode": "batch_level"},
        ],
    )
    async def test_imported_batch_request_has_strict_shape(
        self,
        client,
        tenant_with_auth,
        sku_with_auth,
        monkeypatch,
        overrides,
    ):
        _, headers = tenant_with_auth
        product_id, sku_id, production_batch_id = sku_with_auth
        create = AsyncMock()
        monkeypatch.setattr(code_batches_api, "create_code_batch", create)
        payload = {
            "product_id": product_id,
            "sku_id": sku_id,
            "production_batch_id": production_batch_id,
            "quantity": 1,
            **overrides,
        }

        response = await client.post(
            "/api/v1/code-batches",
            json=payload,
            headers={**headers, "Idempotency-Key": "11111111-1111-4111-8111-111111111111"},
        )

        assert response.status_code == 422
        create.assert_not_awaited()

    @pytest.mark.anyio
    async def test_export_fails_closed_before_service_and_audit(
        self,
        client,
        code_batch_with_auth,
        monkeypatch,
    ):
        _, headers, batch_id, _ = code_batch_with_auth
        export = AsyncMock()
        audit = AsyncMock()
        monkeypatch.setattr(code_batches_api, "generate_authorized_code_csv", export)
        monkeypatch.setattr("app.services.export_audit.log_export", audit)
        monkeypatch.setattr(
            code_service._code_operation_rate_cache,
            "rate_limit_check_shared",
            AsyncMock(side_effect=SharedSecurityCacheUnavailable("redis unavailable")),
        )

        response = await client.post(
            f"/api/v1/code-batches/{batch_id}/export",
            json={"reason": "Test export"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )

        assert response.status_code == 503
        export.assert_not_awaited()
        audit.assert_not_awaited()

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("code_type", "quantity"),
        [
            ("outer", 1),
            ("unsupported", 1),
            ("single", 10_001),
            ("paired", 5_001),
        ],
    )
    async def test_code_batch_create_rejects_invalid_type_or_physical_item_limit(
        self,
        client: AsyncClient,
        tenant_with_auth,
        sku_with_auth,
        code_type,
        quantity,
    ):
        _, headers = tenant_with_auth
        product_id, sku_id, production_batch_id = sku_with_auth

        response = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": quantity,
                "code_type": code_type,
            },
            headers={**headers, "Idempotency-Key": "11111111-1111-4111-8111-111111111111"},
        )

        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_operator_cannot_manage_batch(self, client: AsyncClient, code_batch_with_auth, tenant_with_auth):
        tid, _ = tenant_with_auth
        _, _, batch_id, _ = code_batch_with_auth
        # 使用 operator token 尝试激活批次
        op_token = create_access_token(tid, "00000000-0000-0000-0000-000000000002", "operator")
        op_headers = {"Authorization": f"Bearer {op_token}"}

        resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/activate",
            headers=op_headers,
        )
        # operator 没有 code:manage，应返回 403
        assert resp.status_code == 403

    @pytest.mark.anyio
    async def test_operator_cannot_patch_batch_and_schema_is_strict(
        self, client: AsyncClient, code_batch_with_auth, tenant_with_auth
    ):
        tid, admin_headers = tenant_with_auth
        _, _, batch_id, _ = code_batch_with_auth
        op_token = create_access_token(tid, "00000000-0000-0000-0000-000000000002", "operator")
        denied = await client.patch(
            f"/api/v1/code-batches/{batch_id}",
            json={"batch_code": "OPERATOR-EDIT"},
            headers={"Authorization": f"Bearer {op_token}"},
        )
        assert denied.status_code == 403

        extra = await client.patch(
            f"/api/v1/code-batches/{batch_id}",
            json={"batch_code": "SAFE", "status": "activated"},
            headers=admin_headers,
        )
        too_long = await client.patch(
            f"/api/v1/code-batches/{batch_id}",
            json={"batch_code": "X" * 101},
            headers=admin_headers,
        )
        assert extra.status_code == 422
        assert too_long.status_code == 422

    @pytest.mark.anyio
    @pytest.mark.parametrize("batch_status", [BatchStatus.recalled, BatchStatus.expired])
    async def test_existing_code_import_rejects_non_active_production_batch_before_writes(
        self,
        client,
        db_session,
        tenant_with_auth,
        sku_with_auth,
        code_batch_with_auth,
        monkeypatch,
        batch_status,
    ):
        _, headers = tenant_with_auth
        _, _, production_batch_id = sku_with_auth
        _, _, code_batch_id, _ = code_batch_with_auth
        if batch_status == BatchStatus.recalled:
            recalled = await client.post(
                f"/api/v1/production-batches/{production_batch_id}/recall",
                json={"reason": "safety recall", "confirm": "recall"},
                headers=headers,
            )
            assert recalled.status_code == 200
        else:
            production_batch = await db_session.get(ProductionBatch, uuid.UUID(production_batch_id))
            production_batch.status = BatchStatus.expired
            await db_session.flush()

        import_audit = AsyncMock()
        monkeypatch.setattr("app.api.v1.imports.write_audit_log", import_audit)
        public_id = generate_public_id()

        response = await client.post(
            "/api/v1/imports/existing-codes",
            params={"code_batch_id": str(code_batch_id)},
            files={"file": ("codes.csv", f"public_id\n{public_id}\n".encode(), "text/csv")},
            headers=headers,
        )

        assert response.status_code == 409
        assert await db_session.scalar(select(CodeItem).where(CodeItem.public_id == public_id)) is None
        import_audit.assert_not_awaited()

    @pytest.mark.anyio
    async def test_existing_code_import_rejects_generated_source_without_writes(
        self,
        client,
        db_session,
        code_batch_with_auth,
    ):
        _, headers, code_batch_id, _ = code_batch_with_auth
        public_id = generate_public_id()

        response = await client.post(
            "/api/v1/imports/existing-codes",
            params={"code_batch_id": str(code_batch_id)},
            files={"file": ("codes.csv", f"public_id\n{public_id}\n".encode(), "text/csv")},
            headers=headers,
        )

        assert response.status_code == 409
        assert await db_session.scalar(select(CodeItem).where(CodeItem.public_id == public_id)) is None

    @pytest.mark.anyio
    async def test_existing_code_import_exact_valid_file_completes_authoritative_batch(
        self,
        client,
        db_session,
        imported_code_batch_with_auth,
    ):
        _, headers, code_batch_id = imported_code_batch_with_auth
        public_ids = [generate_public_id(), generate_public_id()]

        response = await client.post(
            "/api/v1/imports/existing-codes",
            params={"code_batch_id": str(code_batch_id)},
            files={
                "file": (
                    "codes.csv",
                    f"public_id\n{public_ids[0]}\n{public_ids[1]}\n".encode(),
                    "text/csv",
                )
            },
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json() == {
            "imported": 2,
            "skipped": 0,
            "failed": 0,
            "total": 2,
            "batch_id": str(code_batch_id),
            "errors": [],
        }
        batch = await db_session.get(CodeBatch, code_batch_id)
        await db_session.refresh(batch)
        assert batch.status == CodeBatchStatus.completed
        items = list(await db_session.scalars(select(CodeItem).where(CodeItem.code_batch_id == code_batch_id)))
        assert {item.public_id for item in items} == set(public_ids)
        assert all(item.status == CodeItemStatus.created for item in items)
        assert all(item.code_type == CodeType.single and item.pair_id is None for item in items)

    @pytest.mark.anyio
    @pytest.mark.parametrize("content", ["public_id\n", "public_id\ninvalid\n"])
    async def test_existing_code_import_requires_exact_all_valid_file_or_writes_nothing(
        self,
        client,
        db_session,
        imported_code_batch_with_auth,
        content,
    ):
        _, headers, code_batch_id = imported_code_batch_with_auth

        response = await client.post(
            "/api/v1/imports/existing-codes",
            params={"code_batch_id": str(code_batch_id)},
            files={"file": ("codes.csv", content.encode(), "text/csv")},
            headers=headers,
        )

        assert response.status_code == 422
        assert (
            await db_session.scalar(
                select(func.count()).select_from(CodeItem).where(CodeItem.code_batch_id == code_batch_id)
            )
            == 0
        )
        batch = await db_session.get(CodeBatch, code_batch_id)
        assert batch.status == CodeBatchStatus.generating

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("path", "filename", "content_type"),
        [
            (
                "/api/v1/imports/excel",
                "catalog.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            ("/api/v1/imports/products", "products.csv", "text/csv"),
            ("/api/v1/imports/existing-codes", "codes.csv", "text/csv"),
        ],
    )
    async def test_import_rate_limit_rejects_before_upload_read(
        self,
        client,
        tenant_with_auth,
        code_batch_with_auth,
        monkeypatch,
        path,
        filename,
        content_type,
    ):
        _, headers = tenant_with_auth
        _, _, code_batch_id, _ = code_batch_with_auth
        rate_check = AsyncMock(return_value=(False, 0))
        read_upload = AsyncMock(return_value=b"public_id\n")
        to_thread = AsyncMock()
        monkeypatch.setattr(import_admission, "_import_rate_cache", SimpleNamespace(rate_limit_check_shared=rate_check))
        monkeypatch.setattr(imports, "_read_upload", read_upload)
        monkeypatch.setattr(import_service_module.asyncio, "to_thread", to_thread)

        response = await client.post(
            path,
            params={"code_batch_id": str(code_batch_id)},
            files={"file": (filename, b"payload", content_type)},
            headers=headers,
        )

        assert response.status_code == 429
        assert response.headers["Retry-After"] == str(import_admission.IMPORT_RATE_LIMIT_WINDOW_SECONDS)
        read_upload.assert_not_awaited()
        to_thread.assert_not_awaited()

    @pytest.mark.anyio
    async def test_import_rate_limit_fails_closed_before_upload_read(
        self,
        client,
        tenant_with_auth,
        monkeypatch,
    ):
        _, headers = tenant_with_auth
        rate_check = AsyncMock(side_effect=SharedSecurityCacheUnavailable("redis unavailable"))
        read_upload = AsyncMock(return_value=b"product_name\n")
        to_thread = AsyncMock()
        monkeypatch.setattr(import_admission, "_import_rate_cache", SimpleNamespace(rate_limit_check_shared=rate_check))
        monkeypatch.setattr(imports, "_read_upload", read_upload)
        monkeypatch.setattr(import_service_module.asyncio, "to_thread", to_thread)

        response = await client.post(
            "/api/v1/imports/products",
            files={"file": ("products.csv", b"payload", "text/csv")},
            headers=headers,
        )

        assert response.status_code == 503
        read_upload.assert_not_awaited()
        to_thread.assert_not_awaited()

    @pytest.mark.anyio
    async def test_excel_capacity_rejects_before_upload_read_or_thread_parse(
        self,
        client,
        tenant_with_auth,
        monkeypatch,
    ):
        tenant_id, headers = tenant_with_auth
        lease = ExcelImportService().reserve_parse_capacity(uuid.UUID(tenant_id))
        assert lease is not None
        read_upload = AsyncMock(return_value=b"workbook")
        to_thread = AsyncMock()
        monkeypatch.setattr(imports, "_read_upload", read_upload)
        monkeypatch.setattr(import_service_module.asyncio, "to_thread", to_thread)
        try:
            response = await client.post(
                "/api/v1/imports/excel",
                files={
                    "file": (
                        "catalog.xlsx",
                        b"payload",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
                headers=headers,
            )
        finally:
            lease.release()

        assert response.status_code == 503
        read_upload.assert_not_awaited()
        to_thread.assert_not_awaited()

    @pytest.mark.anyio
    async def test_import_rate_limit_key_hides_tenant_and_principal(self, monkeypatch, caplog):
        tenant_id = uuid.uuid4()
        account_id = uuid.uuid4()
        rate_check = AsyncMock(return_value=(True, 9))
        monkeypatch.setattr(
            import_admission,
            "_import_rate_cache",
            SimpleNamespace(rate_limit_check_shared=rate_check),
        )

        await import_admission.enforce_import_rate_limit(tenant_id, account_id)

        key = rate_check.await_args.args[0]
        assert str(tenant_id) not in key
        assert str(account_id) not in key
        assert str(tenant_id) not in caplog.text
        assert str(account_id) not in caplog.text

    @pytest.mark.anyio
    async def test_existing_code_import_rejects_active_batch_with_past_expiry_before_writes(
        self,
        client,
        db_session,
        tenant_with_auth,
        sku_with_auth,
        code_batch_with_auth,
        monkeypatch,
    ):
        _, headers = tenant_with_auth
        _, _, production_batch_id = sku_with_auth
        _, _, code_batch_id, _ = code_batch_with_auth
        production_batch = await db_session.get(ProductionBatch, uuid.UUID(production_batch_id))
        production_batch.expiry_date = date.today() - timedelta(days=1)
        await db_session.flush()

        import_audit = AsyncMock()
        monkeypatch.setattr("app.api.v1.imports.write_audit_log", import_audit)
        public_id = generate_public_id()
        response = await client.post(
            "/api/v1/imports/existing-codes",
            params={"code_batch_id": str(code_batch_id)},
            files={"file": ("codes.csv", f"public_id\n{public_id}\n".encode(), "text/csv")},
            headers=headers,
        )

        assert response.status_code == 409
        assert await db_session.scalar(select(CodeItem).where(CodeItem.public_id == public_id)) is None
        import_audit.assert_not_awaited()

    @pytest.mark.anyio
    async def test_existing_code_import_revalidates_locked_code_batch_status_before_writes(
        self,
        client,
        db_session,
        tenant_with_auth,
        code_batch_with_auth,
        monkeypatch,
    ):
        _, headers = tenant_with_auth
        _, _, code_batch_id, _ = code_batch_with_auth
        code_batch = await db_session.get(CodeBatch, code_batch_id)
        code_batch.status = CodeBatchStatus.activated
        await db_session.flush()
        public_id = generate_public_id()
        import_audit = AsyncMock()
        monkeypatch.setattr(imports, "write_audit_log", import_audit)

        response = await client.post(
            "/api/v1/imports/existing-codes",
            params={"code_batch_id": str(code_batch_id)},
            files={"file": ("codes.csv", f"public_id\n{public_id}\n".encode(), "text/csv")},
            headers=headers,
        )

        assert response.status_code == 409
        assert await db_session.scalar(select(CodeItem).where(CodeItem.public_id == public_id)) is None
        import_audit.assert_not_awaited()

    @pytest.mark.anyio
    async def test_existing_code_import_audit_failure_rolls_back_items(
        self,
        client,
        db_session,
        tenant_with_auth,
        imported_code_batch_with_auth,
        monkeypatch,
    ):
        _, headers = tenant_with_auth
        _, _, code_batch_id = imported_code_batch_with_auth
        public_ids = [generate_public_id(), generate_public_id()]

        async def rollbacking_get_db():
            transaction = await db_session.begin_nested()
            try:
                yield db_session
                await transaction.commit()
            except BaseException:
                await transaction.rollback()
                raise

        app.dependency_overrides[get_db] = rollbacking_get_db
        monkeypatch.setattr(
            "app.api.v1.imports.write_audit_log",
            AsyncMock(side_effect=RuntimeError("audit unavailable")),
        )

        with pytest.raises(RuntimeError, match="audit unavailable"):
            await client.post(
                "/api/v1/imports/existing-codes",
                params={"code_batch_id": str(code_batch_id)},
                files={
                    "file": (
                        "codes.csv",
                        f"public_id\n{public_ids[0]}\n{public_ids[1]}\n".encode(),
                        "text/csv",
                    )
                },
                headers=headers,
            )

        assert await db_session.scalar(select(CodeItem).where(CodeItem.public_id.in_(public_ids))) is None

    @pytest.mark.anyio
    async def test_operator_can_export(self, client: AsyncClient, code_batch_with_auth, tenant_with_auth):
        tid, _ = tenant_with_auth
        _, _, batch_id, _ = code_batch_with_auth
        op_token = create_access_token(tid, "00000000-0000-0000-0000-000000000002", "operator")
        op_headers = {"Authorization": f"Bearer {op_token}"}

        resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/export",
            json={"reason": "Test export"},
            headers={**op_headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        # operator 有 code:export 权限，应返回 200
        assert resp.status_code == 200


class TestPublicIdConflictHandling:
    """1.3 public_id 冲突检测测试"""

    @pytest.mark.anyio
    async def test_batch_generation_no_duplicate_ids(self, client: AsyncClient, tenant_with_auth, sku_with_auth):
        tid, headers = tenant_with_auth
        product_id, sku_id, production_batch_id = sku_with_auth
        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 100,
            },
            headers={**headers, "Idempotency-Key": "33333333-3333-4333-8333-333333333333"},
        )
        assert resp.status_code == 201
        batch_id = uuid.UUID(resp.json()["id"])

        # 获取所有码项，验证 public_id 唯一
        items_resp = await client.get(
            "/api/v1/code-items",
            params={"code_batch_id": batch_id, "page_size": 100},
            headers=headers,
        )
        items = items_resp.json()["items"]
        public_ids = [i["public_id"] for i in items]
        assert len(public_ids) == len(set(public_ids)), "Duplicate public_id found within batch"


class TestBatchStateMachine:
    """3.3 批次状态机测试"""

    @pytest.mark.anyio
    async def test_activation_lock_conflict_maps_to_stable_busy_response(
        self,
        client: AsyncClient,
        tenant_with_auth,
        monkeypatch,
    ):
        class LockConflict(Exception):
            sqlstate = "55P03"

        _, headers = tenant_with_auth
        monkeypatch.setattr(
            code_batches_api,
            "activate_batch",
            AsyncMock(side_effect=DBAPIError("statement", {}, LockConflict())),
        )

        response = await client.post(
            "/api/v1/code-batches/00000000-0000-0000-0000-000000000999/activate",
            headers=headers,
        )

        assert response.status_code == 409
        assert response.json()["error_code"] == "CODE_LIFECYCLE_BUSY"

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("sqlstate", "expected_status", "expected_error_code"),
        [
            ("23503", 404, "NOT_FOUND"),
            ("22023", 409, "CODE_LIFECYCLE_CONFLICT"),
            ("55P03", 409, "CODE_LIFECYCLE_BUSY"),
            ("42501", 401, None),
        ],
    )
    async def test_freeze_database_failures_have_stable_public_mapping(
        self,
        client: AsyncClient,
        tenant_with_auth,
        monkeypatch,
        sqlstate,
        expected_status,
        expected_error_code,
    ):
        class LifecycleFailure(Exception):
            pass

        failure = LifecycleFailure()
        failure.sqlstate = sqlstate
        _, headers = tenant_with_auth
        monkeypatch.setattr(
            code_batches_api,
            "freeze_batch",
            AsyncMock(side_effect=DBAPIError("statement", {}, failure)),
        )

        response = await client.post(
            "/api/v1/code-batches/00000000-0000-0000-0000-000000000999/freeze",
            json={"reason": "investigation", "confirm": "freeze"},
            headers=headers,
        )

        assert response.status_code == expected_status
        if expected_error_code is not None:
            assert response.json()["error_code"] == expected_error_code

    @pytest.mark.anyio
    async def test_mark_printing_invalid_transition(self, client: AsyncClient, code_batch_with_auth):
        _, headers, batch_id, _ = code_batch_with_auth
        exported = await client.post(
            f"/api/v1/code-batches/{batch_id}/export",
            json={"reason": "Test export"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert exported.status_code == 200
        resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/mark-printing",
            headers=headers,
        )
        assert resp.status_code == 200

        # 再次标记 printing 应失败（状态机不允许 printing -> printing）
        resp2 = await client.post(
            f"/api/v1/code-batches/{batch_id}/mark-printing",
            headers=headers,
        )
        assert resp2.status_code == 409

    @pytest.mark.anyio
    async def test_mark_delivered_requires_printing(self, client: AsyncClient, code_batch_with_auth):
        _, headers, batch_id, _ = code_batch_with_auth
        exported = await client.post(
            f"/api/v1/code-batches/{batch_id}/export",
            json={"reason": "Test export"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert exported.status_code == 200
        resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/mark-delivered",
            json={"reason": "handoff", "recipient": "recipient", "confirm": "deliver"},
            headers=headers,
        )
        assert resp.status_code == 409

    @pytest.mark.anyio
    async def test_delivered_after_printing(self, client: AsyncClient, code_batch_with_auth):
        _, headers, batch_id, _ = code_batch_with_auth
        exported = await client.post(
            f"/api/v1/code-batches/{batch_id}/export",
            json={"reason": "Test export"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert exported.status_code == 200
        resp1 = await client.post(
            f"/api/v1/code-batches/{batch_id}/mark-printing",
            headers=headers,
        )
        assert resp1.status_code == 200

        # 再标记 delivered
        resp2 = await client.post(
            f"/api/v1/code-batches/{batch_id}/mark-delivered",
            json={"reason": "handoff", "recipient": "recipient", "confirm": "deliver"},
            headers=headers,
        )
        assert resp2.status_code == 200

    @pytest.mark.anyio
    @pytest.mark.parametrize("transition", ["mark-printing", "mark-delivered"])
    @pytest.mark.parametrize("production_batch_state", ["recalled", "past_expiry"])
    async def test_non_active_production_batch_blocks_operational_transition_with_stable_conflict(
        self,
        client,
        db_session,
        tenant_with_auth,
        sku_with_auth,
        code_batch_with_auth,
        monkeypatch,
        transition,
        production_batch_state,
    ):
        _, headers = tenant_with_auth
        _, _, production_batch_id = sku_with_auth
        _, _, code_batch_id, _ = code_batch_with_auth
        exported = await client.post(
            f"/api/v1/code-batches/{code_batch_id}/export",
            json={"reason": "Test export"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert exported.status_code == 200
        if transition == "mark-delivered":
            printing = await client.post(f"/api/v1/code-batches/{code_batch_id}/mark-printing", headers=headers)
            assert printing.status_code == 200
        if production_batch_state == "recalled":
            recalled = await client.post(
                f"/api/v1/production-batches/{production_batch_id}/recall",
                json={"reason": "operational stop", "confirm": "recall"},
                headers=headers,
            )
            assert recalled.status_code == 200
        else:
            production_batch = await db_session.get(ProductionBatch, uuid.UUID(production_batch_id))
            production_batch.expiry_date = date.today() - timedelta(days=1)
            await db_session.flush()
        audit = AsyncMock()
        monkeypatch.setattr("app.services.audit.write_audit_log", audit)

        request_kwargs = {"headers": headers}
        if transition == "mark-delivered":
            request_kwargs["json"] = {"reason": "handoff", "recipient": "recipient", "confirm": "deliver"}
        response = await client.post(f"/api/v1/code-batches/{code_batch_id}/{transition}", **request_kwargs)

        assert response.status_code == 409
        assert response.json()["error_code"] == "PRODUCTION_BATCH_NOT_ACTIVE"
        assert response.json()["detail"] == "Production batch is not active"
        batch = await db_session.get(CodeBatch, code_batch_id)
        assert batch.status == (CodeBatchStatus.exported if transition == "mark-printing" else CodeBatchStatus.printing)
        audit.assert_not_awaited()

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("transition", "expected_status"),
        [
            ("mark-printing", CodeBatchStatus.exported),
            ("mark-delivered", CodeBatchStatus.printing),
        ],
    )
    async def test_operational_transition_audit_failure_rolls_back_status(
        self,
        client,
        db_session,
        tenant_with_auth,
        code_batch_with_auth,
        monkeypatch,
        transition,
        expected_status,
    ):
        _, headers = tenant_with_auth
        _, _, code_batch_id, _ = code_batch_with_auth
        exported = await client.post(
            f"/api/v1/code-batches/{code_batch_id}/export",
            json={"reason": "Test export"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert exported.status_code == 200
        if transition == "mark-delivered":
            printing = await client.post(f"/api/v1/code-batches/{code_batch_id}/mark-printing", headers=headers)
            assert printing.status_code == 200

        async def rollbacking_get_db():
            transaction = await db_session.begin_nested()
            try:
                yield db_session
                await transaction.commit()
            except BaseException:
                await transaction.rollback()
                raise

        app.dependency_overrides[get_db] = rollbacking_get_db
        monkeypatch.setattr(
            "app.services.audit.write_audit_log",
            AsyncMock(side_effect=RuntimeError("audit unavailable")),
        )

        with pytest.raises(RuntimeError, match="audit unavailable"):
            request_kwargs = {"headers": headers}
            if transition == "mark-delivered":
                request_kwargs["json"] = {"reason": "handoff", "recipient": "recipient", "confirm": "deliver"}
            await client.post(f"/api/v1/code-batches/{code_batch_id}/{transition}", **request_kwargs)

        batch = await db_session.get(CodeBatch, code_batch_id)
        await db_session.refresh(batch)
        assert batch.status == expected_status


class TestExportNotFound:
    """1.4 导出 404 测试"""

    @pytest.mark.anyio
    async def test_export_nonexistent_batch_returns_404(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        resp = await client.post(
            "/api/v1/code-batches/00000000-0000-0000-0000-000000000999/export",
            json={"reason": "Test export"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert resp.status_code == 404


class TestAuthoritativeCodeExportBoundary:
    @pytest.mark.anyio
    async def test_incomplete_pair_cannot_create_export_manifest(
        self,
        client,
        db_session,
        tenant_with_auth,
        sku_with_auth,
    ):
        _, headers = tenant_with_auth
        product_id, sku_id, production_batch_id = sku_with_auth
        created = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 1,
                "code_type": "paired",
            },
            headers={**headers, "Idempotency-Key": "44444444-4444-4444-8444-444444444444"},
        )
        assert created.status_code == 201
        batch_id = uuid.UUID(created.json()["id"])
        item = await db_session.scalar(select(CodeItem).where(CodeItem.code_batch_id == batch_id).limit(1))
        item.pair_id = None
        await db_session.flush()

        response = await client.post(
            f"/api/v1/code-batches/{batch_id}/export",
            json={"reason": "Test export"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )

        assert response.status_code == 409
        assert (
            await db_session.scalar(
                select(func.count()).select_from(ExportLog).where(ExportLog.code_batch_id == batch_id)
            )
            == 0
        )

    @pytest.mark.anyio
    @pytest.mark.parametrize("blocked_by", ["recall", "void"])
    async def test_recalled_or_voided_codes_cannot_create_export_manifest(
        self,
        client,
        db_session,
        tenant_with_auth,
        sku_with_auth,
        code_batch_with_auth,
        blocked_by,
    ):
        _, headers = tenant_with_auth
        _, _, production_batch_id = sku_with_auth
        _, _, code_batch_id, _ = code_batch_with_auth
        if blocked_by == "recall":
            blocked = await client.post(
                f"/api/v1/production-batches/{production_batch_id}/recall",
                json={"reason": "safety recall", "confirm": "recall"},
                headers=headers,
            )
        else:
            blocked = await client.post(
                f"/api/v1/code-batches/{code_batch_id}/void",
                params={"reason": "packaging invalid", "confirm": "void"},
                headers=headers,
            )
        assert blocked.status_code == 200

        response = await client.post(
            f"/api/v1/code-batches/{code_batch_id}/export",
            json={"reason": "Test export"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )

        assert response.status_code == 409
        assert (
            await db_session.scalar(
                select(func.count()).select_from(ExportLog).where(ExportLog.code_batch_id == code_batch_id)
            )
            == 0
        )


class TestProductCSVImportSafety:
    @pytest.mark.anyio
    async def test_row_fields_are_trimmed_before_catalog_writes(self, client, db_session, tenant_with_auth):
        tenant_id, headers = tenant_with_auth
        content = (
            "brand_name,product_name,category,description,sku_code,sku_name\n"
            "  修剪品牌  ,  修剪产品  ,  食品  ,  产品说明  ,  TRIM-SKU  ,  默认规格  \n"
        )

        response = await client.post(
            "/api/v1/imports/products",
            files={"file": ("products.csv", content.encode(), "text/csv")},
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json() == {"imported": 1, "errors": []}
        tenant_uuid = uuid.UUID(tenant_id)
        brand = await db_session.scalar(select(Brand).where(Brand.tenant_id == tenant_uuid))
        product = await db_session.scalar(select(Product).where(Product.tenant_id == tenant_uuid))
        sku = await db_session.scalar(select(SKU).where(SKU.tenant_id == tenant_uuid))
        assert (brand.name, product.name, product.category, product.description, sku.code, sku.name) == (
            "修剪品牌",
            "修剪产品",
            "食品",
            "产品说明",
            "TRIM-SKU",
            "默认规格",
        )

    @pytest.mark.anyio
    async def test_unexpected_csv_column_is_rejected_before_catalog_writes(self, client, db_session, tenant_with_auth):
        tenant_id, headers = tenant_with_auth
        content = "brand_name,product_name,unexpected\n安全品牌,安全产品,SENSITIVE-EXTRA\n"

        response = await client.post(
            "/api/v1/imports/products",
            files={"file": ("products.csv", content.encode(), "text/csv")},
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["imported"] == 0
        assert response.json()["errors"][0]["code"] == "IMPORT_ROW_INVALID"
        assert (
            await db_session.scalar(
                select(func.count()).select_from(Product).where(Product.tenant_id == uuid.UUID(tenant_id))
            )
            == 0
        )

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("field", "oversized_value"),
        [
            ("brand_name", "B" * 101),
            ("product_name", "P" * 201),
            ("category", "C" * 101),
            ("description", "D" * 1001),
            ("sku_code", "S" * 101),
            ("sku_name", "N" * 201),
        ],
    )
    async def test_oversized_row_is_rejected_before_catalog_writes(
        self, client, db_session, tenant_with_auth, field, oversized_value
    ):
        tenant_id, headers = tenant_with_auth
        row = {
            "brand_name": "安全品牌",
            "product_name": "安全产品",
            "category": "食品",
            "description": "产品说明",
            "sku_code": "SAFE-SKU",
            "sku_name": "默认规格",
        }
        row[field] = oversized_value
        columns = tuple(row)
        content = ",".join(columns) + "\n" + ",".join(row[column] for column in columns) + "\n"

        response = await client.post(
            "/api/v1/imports/products",
            files={"file": ("products.csv", content.encode(), "text/csv")},
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["imported"] == 0
        error = response.json()["errors"][0]
        assert set(error) == {"code", "message", "reference_id"}
        assert error["code"] == "IMPORT_ROW_INVALID"
        assert error["message"] == "该行商品数据无效"
        assert error["reference_id"].startswith("imp_")
        assert (
            await db_session.scalar(
                select(func.count()).select_from(Product).where(Product.tenant_id == uuid.UUID(tenant_id))
            )
            == 0
        )

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("exception_factory", "exception_type"),
        [
            (
                lambda secret: DataError("INSERT INTO products VALUES (?)", {"name": secret}, RuntimeError(secret)),
                "DataError",
            ),
            (
                lambda secret: DBAPIError("UPDATE products SET name=?", {"name": secret}, RuntimeError(secret)),
                "DBAPIError",
            ),
            (lambda secret: RuntimeError(secret), "RuntimeError"),
        ],
    )
    async def test_row_failure_returns_sanitized_diagnostic_and_recovers_savepoint(
        self,
        client,
        db_session,
        tenant_with_auth,
        monkeypatch,
        caplog,
        exception_factory,
        exception_type,
    ):
        tenant_id, headers = tenant_with_auth
        secret = "SENSITIVE-UPLOAD-AND-SQL-PARAM"
        monkeypatch.setattr(imports, "reserve_quota", AsyncMock(side_effect=exception_factory(secret)))
        caplog.set_level("WARNING", logger="app.api.v1.imports")
        content = (
            "brand_name,product_name,category,description,sku_code,sku_name\n"
            "秘密品牌,秘密产品,食品,秘密描述,SECRET-SKU,秘密规格\n"
        )

        response = await client.post(
            "/api/v1/imports/products",
            files={"file": ("products.csv", content.encode(), "text/csv")},
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["imported"] == 0
        error = response.json()["errors"][0]
        assert set(error) == {"code", "message", "reference_id"}
        assert error["code"] == "IMPORT_ROW_FAILED"
        assert error["message"] == "该行商品导入失败"
        assert error["reference_id"].startswith("imp_")
        assert error["reference_id"] in caplog.text
        assert f"exception_type={exception_type}" in caplog.text
        assert secret not in response.text
        assert secret not in caplog.text
        assert "INSERT INTO products" not in caplog.text
        assert "UPDATE products" not in caplog.text
        assert (
            await db_session.scalar(
                select(func.count()).select_from(Brand).where(Brand.tenant_id == uuid.UUID(tenant_id))
            )
            == 0
        )

    @pytest.mark.anyio
    async def test_failed_middle_row_does_not_corrupt_import_count_or_brand_reuse(
        self, client, db_session, tenant_with_auth, monkeypatch, caplog
    ):
        tenant_id, headers = tenant_with_auth
        secret = "SECOND-ROW-SENSITIVE-VALUE"
        monkeypatch.setattr(
            imports,
            "reserve_quota",
            AsyncMock(side_effect=[None, RuntimeError(secret), None]),
        )
        caplog.set_level("WARNING", logger="app.api.v1.imports")
        content = (
            "brand_name,product_name,category,description,sku_code,sku_name\n"
            "共享品牌,成功产品一,食品,说明一,SAFE-SKU-1,规格一\n"
            "回滚品牌,失败产品,食品,秘密描述,FAILED-SKU,失败规格\n"
            "共享品牌,成功产品二,食品,说明二,SAFE-SKU-2,规格二\n"
        )

        response = await client.post(
            "/api/v1/imports/products",
            files={"file": ("products.csv", content.encode(), "text/csv")},
            headers=headers,
        )

        payload = response.json()
        assert response.status_code == 200
        assert payload["imported"] == 2
        assert len(payload["errors"]) == 1
        assert payload["errors"][0]["code"] == "IMPORT_ROW_FAILED"
        assert secret not in response.text
        assert secret not in caplog.text
        tenant_uuid = uuid.UUID(tenant_id)
        assert (
            await db_session.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == tenant_uuid))
            == 2
        )
        assert await db_session.scalar(select(func.count()).select_from(SKU).where(SKU.tenant_id == tenant_uuid)) == 2
        assert (
            await db_session.scalar(select(func.count()).select_from(Brand).where(Brand.tenant_id == tenant_uuid)) == 1
        )
