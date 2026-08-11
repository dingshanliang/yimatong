"""外部权益连接器 API 集成测试"""

import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.connector import BenefitDelivery, Connector, CouponPool
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


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
async def setup_tenant(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "连接器测试租户",
            "admin_email": "connector@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


class TestCouponPool:
    """券码池导入与发放"""

    @pytest.mark.anyio
    async def test_import_coupon_codes(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={
                "name": "测试优惠券池",
                "codes": ["COUPON001", "COUPON002", "COUPON003"],
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "测试优惠券池"
        assert data["total_codes"] == 3
        assert data["remaining"] == 3

    @pytest.mark.anyio
    async def test_coupon_pool_request_bounds_fail_before_database_writes(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_tenant,
    ):
        _tenant_id, headers = setup_tenant
        before = (await db_session.execute(select(func.count()).select_from(CouponPool))).scalar_one()

        too_many = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={"name": "too-many", "codes": [f"CODE-{index}" for index in range(5_001)]},
            headers=headers,
        )
        duplicates = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={"name": "duplicates", "codes": ["SAME", "SAME"]},
            headers=headers,
        )
        oversized_code = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={"name": "oversized", "codes": ["X" * 101]},
            headers=headers,
        )
        oversized_body = await client.post(
            "/api/v1/connectors/coupon-pools",
            content=json.dumps({"name": "body", "codes": ["X" * 1_048_576]}),
            headers={**headers, "Content-Type": "application/json"},
        )

        async def oversized_chunks():
            yield b'{"name":"body","codes":["'
            yield b"X" * 600_000
            yield b"Y" * 600_000
            yield b'"]}'

        oversized_stream = await client.post(
            "/api/v1/connectors/coupon-pools",
            content=oversized_chunks(),
            headers={**headers, "Content-Type": "application/json"},
        )
        oversized_vendor_json = await client.post(
            "/api/v1/connectors/coupon-pools",
            content=json.dumps({"name": "body", "codes": ["X" * 1_048_576]}),
            headers={**headers, "Content-Type": "application/vnd.api+json"},
        )
        oversized_callback = await client.post(
            f"/api/v1/connectors/connectors/{uuid.uuid4()}/callback",
            content=b"X" * 1_048_577,
            headers={"Content-Type": "application/octet-stream"},
        )

        assert too_many.status_code == 422
        assert duplicates.status_code == 422
        assert oversized_code.status_code == 422
        assert oversized_body.status_code == 413
        assert oversized_stream.status_code == 413
        assert oversized_vendor_json.status_code == 413
        assert oversized_callback.status_code == 413
        after = (await db_session.execute(select(func.count()).select_from(CouponPool))).scalar_one()
        assert after == before

    @pytest.mark.anyio
    async def test_coupon_pool_list_is_stably_paginated(self, client: AsyncClient, setup_tenant):
        _tenant_id, headers = setup_tenant
        for index in range(3):
            response = await client.post(
                "/api/v1/connectors/coupon-pools",
                json={"name": f"page-pool-{index}", "codes": [f"PAGE-{index}"]},
                headers=headers,
            )
            assert response.status_code == 201

        first = await client.get(
            "/api/v1/connectors/coupon-pools",
            params={"page": 1, "page_size": 2},
            headers=headers,
        )
        second = await client.get(
            "/api/v1/connectors/coupon-pools",
            params={"page": 2, "page_size": 2},
            headers=headers,
        )

        assert first.status_code == second.status_code == 200
        assert first.json()["total"] == second.json()["total"] == 3
        assert len(first.json()["items"]) == 2
        assert len(second.json()["items"]) == 1
        assert {item["id"] for item in first.json()["items"]}.isdisjoint(
            {item["id"] for item in second.json()["items"]}
        )
        too_large_page = await client.get(
            "/api/v1/connectors/coupon-pools",
            params={"page": 1, "page_size": 101},
            headers=headers,
        )
        assert too_large_page.status_code == 422

    @pytest.mark.anyio
    async def test_coupon_code_reuse_returns_stable_conflict(self, client: AsyncClient, setup_tenant):
        _tenant_id, headers = setup_tenant
        first = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={"name": "first", "codes": ["TENANT-UNIQUE-CODE"]},
            headers=headers,
        )
        duplicate = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={"name": "second", "codes": ["TENANT-UNIQUE-CODE"]},
            headers=headers,
        )

        assert first.status_code == 201
        assert duplicate.status_code == 409
        listed = await client.get("/api/v1/connectors/coupon-pools", headers=headers)
        assert listed.json()["total"] == 1

    @pytest.mark.anyio
    async def test_distribute_coupon(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        pool_resp = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={
                "name": "发放测试池",
                "codes": ["DIS001", "DIS002"],
            },
            headers=headers,
        )
        pool_id = pool_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/connectors/coupon-pools/{pool_id}/distribute",
            json={"consumer_id": "consumer-001"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "DIS001"
        assert data["consumer_id"] == "consumer-001"

    @pytest.mark.anyio
    async def test_pool_codes_and_distribution_do_not_cross_tenants(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        _tenant_a, headers_a = setup_tenant
        tenant_response = await client.post(
            "/api/v1/tenants",
            json={
                "name": "连接器隔离租户",
                "admin_email": "connector-isolation@test.com",
                "admin_name": "Admin",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        tenant_b = tenant_response.json()["id"]
        headers_b = {"Authorization": f"Bearer {create_access_token(tenant_b, str(uuid.uuid4()), 'admin')}"}
        pool_response = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={"name": "租户 B 券码池", "codes": ["TENANT-B-ONLY"]},
            headers=headers_b,
        )
        pool_id = pool_response.json()["id"]

        listed = await client.get(
            f"/api/v1/connectors/coupon-pools/{pool_id}/codes",
            headers=headers_a,
        )
        distributed = await client.post(
            f"/api/v1/connectors/coupon-pools/{pool_id}/distribute",
            json={"consumer_id": "tenant-a-consumer"},
            headers=headers_a,
        )

        assert listed.status_code == 404
        assert distributed.status_code == 404


class TestConnectorCRUD:
    """连接器 CRUD 操作"""

    @pytest.mark.anyio
    async def test_list_connector_types(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        resp = await client.get("/api/v1/connectors/connectors/types", headers=headers)
        assert resp.status_code == 200
        types = resp.json()["types"]
        assert "generic_http" in types
        assert "coupon_pool" in types

    @pytest.mark.anyio
    async def test_create_generic_http_connector(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "测试 HTTP 连接器",
                "connector_type": "generic_http",
                "config": {"api_url": "https://api.example.com"},
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "测试 HTTP 连接器"
        assert data["connector_type"] == "generic_http"
        assert data["enabled"] is True

    @pytest.mark.anyio
    async def test_create_coupon_pool_connector(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        # 先创建券码池
        pool_resp = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={"name": "关联测试池", "codes": ["C001"]},
            headers=headers,
        )
        pool_id = pool_resp.json()["id"]

        resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "券码池连接器",
                "connector_type": "coupon_pool",
                "config": {"pool_id": pool_id},
            },
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["connector_type"] == "coupon_pool"

    @pytest.mark.anyio
    async def test_create_connector_invalid_config(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "无效配置",
                "connector_type": "generic_http",
                "config": {},
            },
            headers=headers,
        )
        assert resp.status_code == 400

    @pytest.mark.anyio
    async def test_low_privilege_principal_cannot_read_or_mutate_connectors(self, client: AsyncClient, setup_tenant):
        tenant_id, _headers = setup_tenant
        viewer = create_access_token(tenant_id, str(uuid.uuid4()), "viewer")
        headers = {"Authorization": f"Bearer {viewer}"}

        read = await client.get("/api/v1/connectors/connectors", headers=headers)
        write = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "forbidden",
                "connector_type": "generic_http",
                "config": {"api_url": "https://api.example.com"},
            },
            headers=headers,
        )

        assert read.status_code == 403
        assert write.status_code == 403

    @pytest.mark.anyio
    async def test_plaintext_credentials_are_rejected_and_never_serialized(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_tenant,
    ):
        tenant_id, headers = setup_tenant
        rejected = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "plaintext-secret",
                "connector_type": "generic_http",
                "config": {"api_url": "https://api.example.com", "api_key": "must-not-persist"},
            },
            headers=headers,
        )
        assert rejected.status_code == 422

        legacy = Connector(
            tenant_id=uuid.UUID(tenant_id),
            name="legacy-plaintext",
            connector_type="generic_http",
            config={"api_url": "https://api.example.com", "api_key": "legacy-secret"},
            enabled=True,
        )
        db_session.add(legacy)
        await db_session.flush()
        response = await client.get(f"/api/v1/connectors/connectors/{legacy.id}", headers=headers)
        assert response.status_code == 200
        assert response.json()["config"] == {"api_url": "https://api.example.com"}
        assert "legacy-secret" not in response.text

    @pytest.mark.anyio
    async def test_list_connectors(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "列表测试",
                "connector_type": "generic_http",
                "config": {"api_url": "https://api.example.com"},
            },
            headers=headers,
        )

        resp = await client.get("/api/v1/connectors/connectors", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()["items"]) >= 1
        assert resp.json()["total"] >= 1

    @pytest.mark.anyio
    async def test_connector_json_and_pagination_are_bounded(self, client: AsyncClient, setup_tenant):
        _tenant_id, headers = setup_tenant
        nested: dict[str, object] = {"value": "leaf"}
        for _ in range(7):
            nested = {"nested": nested}
        rejected = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "nested-config",
                "connector_type": "generic_http",
                "config": nested,
            },
            headers=headers,
        )
        assert rejected.status_code == 422

        for index in range(3):
            created = await client.post(
                "/api/v1/connectors/connectors",
                json={
                    "name": f"connector-page-{index}",
                    "connector_type": "generic_http",
                    "config": {"api_url": "https://api.example.com"},
                },
                headers=headers,
            )
            assert created.status_code == 201

        first = await client.get(
            "/api/v1/connectors/connectors",
            params={"page": 1, "page_size": 2},
            headers=headers,
        )
        second = await client.get(
            "/api/v1/connectors/connectors",
            params={"page": 2, "page_size": 2},
            headers=headers,
        )
        assert first.json()["total"] == second.json()["total"] == 3
        assert len(first.json()["items"]) == 2
        assert len(second.json()["items"]) == 1
        assert {item["id"] for item in first.json()["items"]}.isdisjoint(
            {item["id"] for item in second.json()["items"]}
        )

    @pytest.mark.anyio
    async def test_update_connector(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "更新测试",
                "connector_type": "generic_http",
                "config": {"api_url": "https://api.example.com"},
            },
            headers=headers,
        )
        conn_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/connectors/connectors/{conn_id}",
            json={"name": "已更新"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "已更新"

    @pytest.mark.anyio
    async def test_test_connection(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "连接测试",
                "connector_type": "generic_http",
                "config": {"api_url": "https://api.example.com"},
            },
            headers=headers,
        )
        conn_id = create_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/connectors/connectors/{conn_id}/test",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestConnectorSecrets:
    """连接器凭证管理"""

    @pytest.mark.anyio
    async def test_create_with_secrets(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "带凭证连接器",
                "connector_type": "generic_http",
                "config": {"api_url": "https://api.example.com"},
                "secrets": {"api_key": "sk_live_12345678", "callback_secret": "cb-secret"},
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        # 凭证应脱敏显示
        assert "secrets" in data
        assert "sk_" in data["secrets"]["api_key"]
        assert "12345678" not in str(data["secrets"]["api_key"])

    @pytest.mark.anyio
    async def test_update_secrets(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "更新凭证",
                "connector_type": "generic_http",
                "config": {"api_url": "https://api.example.com"},
            },
            headers=headers,
        )
        conn_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/connectors/connectors/{conn_id}",
            json={"secrets": {"api_key": "new-key"}},
            headers=headers,
        )
        assert resp.status_code == 200


class TestBenefitDeliveries:
    """权益发放记录详情与重试"""

    @pytest.mark.anyio
    async def test_get_delivery_detail(self, client: AsyncClient, db_session: AsyncSession, setup_tenant):
        tenant_id, headers = setup_tenant
        delivery_id = uuid.uuid4()
        db_session.add(
            BenefitDelivery(
                id=delivery_id,
                tenant_id=uuid.UUID(tenant_id),
                connector_id=uuid.uuid4(),
                consumer_id="consumer-detail-001",
                benefit_type="coupon",
                benefit_config={"amount": 10},
                status="failed",
                retry_count=2,
                max_retries=5,
                external_data={"error": "invalid receiver"},
            )
        )
        await db_session.flush()

        resp = await client.get(f"/api/v1/connectors/deliveries/{delivery_id}", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(delivery_id)
        assert data["consumer_id"] == "consumer-detail-001"
        assert data["status"] == "failed"
        assert data["retry_count"] == 2
        assert data["max_retries"] == 5
        assert data["external_data"] == {"error": "invalid receiver"}
        assert data["created_at"]
        assert data["updated_at"]

    @pytest.mark.anyio
    async def test_pending_retries_are_stably_paginated(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_tenant,
    ):
        from datetime import UTC, datetime, timedelta

        tenant_id, headers = setup_tenant
        for index in range(3):
            db_session.add(
                BenefitDelivery(
                    tenant_id=uuid.UUID(tenant_id),
                    connector_id=uuid.uuid4(),
                    consumer_id=f"pending-{index}",
                    benefit_type="coupon",
                    benefit_config={},
                    status="pending",
                    retry_count=0,
                    max_retries=5,
                    next_retry_at=datetime.now(UTC) - timedelta(minutes=1),
                )
            )
        await db_session.flush()

        first = await client.get(
            "/api/v1/connectors/deliveries/pending-retries",
            params={"page": 1, "page_size": 2},
            headers=headers,
        )
        second = await client.get(
            "/api/v1/connectors/deliveries/pending-retries",
            params={"page": 2, "page_size": 2},
            headers=headers,
        )
        assert first.json()["total"] == second.json()["total"] == 3
        assert len(first.json()["items"]) == 2
        assert len(second.json()["items"]) == 1
        assert {item["id"] for item in first.json()["items"]}.isdisjoint(
            {item["id"] for item in second.json()["items"]}
        )

    @pytest.mark.anyio
    async def test_retry_failed_delivery_runs_again(self, client: AsyncClient, db_session: AsyncSession, setup_tenant):
        tenant_id, headers = setup_tenant
        pool_resp = await client.post(
            "/api/v1/connectors/coupon-pools",
            json={"name": "重试券码池", "codes": ["RETRY001"]},
            headers=headers,
        )
        connector_resp = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "重试券码池连接器",
                "connector_type": "coupon_pool",
                "config": {"pool_id": pool_resp.json()["id"]},
            },
            headers=headers,
        )
        connector_id = connector_resp.json()["id"]
        delivery_id = uuid.uuid4()
        db_session.add(
            BenefitDelivery(
                id=delivery_id,
                tenant_id=uuid.UUID(tenant_id),
                connector_id=uuid.UUID(connector_id),
                consumer_id="consumer-retry-001",
                benefit_type="coupon",
                benefit_config={"benefit_type": "platform_coupon"},
                status="failed",
                retry_count=1,
                max_retries=5,
                external_data={"error": "temporary failure"},
            )
        )
        await db_session.flush()

        resp = await client.post(f"/api/v1/connectors/deliveries/{delivery_id}/retry", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] != str(delivery_id)
        assert data["consumer_id"] == "consumer-retry-001"
        assert data["status"] == "success"
        assert data["external_data"]["code"] == "RETRY001"

    @pytest.mark.anyio
    async def test_signed_callback_matches_exact_external_claim_identity(
        self, client: AsyncClient, db_session: AsyncSession, setup_tenant
    ):
        tenant_id, headers = setup_tenant
        callback_secret = "callback-secret-for-test"
        connector_response = await client.post(
            "/api/v1/connectors/connectors",
            json={
                "name": "回调测试连接器",
                "connector_type": "generic_http",
                "config": {"api_url": "https://api.example.com"},
                "secrets": {"callback_secret": callback_secret},
            },
            headers=headers,
        )
        connector_id = uuid.UUID(connector_response.json()["id"])
        claim_id = uuid.uuid4()
        delivery = BenefitDelivery(
            tenant_id=uuid.UUID(tenant_id),
            connector_id=connector_id,
            claim_id=claim_id,
            consumer_id="consumer-callback-001",
            benefit_type="platform_coupon",
            benefit_config={"out_bill_no": str(claim_id)},
            external_id=str(claim_id),
            status="pending",
        )
        db_session.add(delivery)
        await db_session.flush()
        body = json.dumps({"id": str(claim_id), "status": "success"}, separators=(",", ":")).encode()
        signature = hmac.new(callback_secret.encode(), body, hashlib.sha256).hexdigest()

        response = await client.post(
            f"/api/v1/connectors/connectors/{connector_id}/callback",
            content=body,
            headers={"Content-Type": "application/json", "X-Callback-Sig": signature},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ok"}
        await db_session.refresh(delivery)
        assert delivery.status == "success"
        assert delivery.external_data["_callback_external_id"] == str(claim_id)
