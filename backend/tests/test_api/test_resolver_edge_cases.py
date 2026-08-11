"""码解析模块边缘场景测试：expired 码、scan_token tenant_id、IP 提取"""

import uuid
from collections.abc import AsyncGenerator
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update as db_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
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


async def _create_code_chain(client: AsyncClient, prefix: str):
    """创建完整的 租户→品牌→产品→SKU→生产批次→码批次→激活 链路，返回 (public_id, item_id, tid)"""

    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": f"{prefix}测试",
            "admin_email": f"{prefix.lower()}@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": f"{prefix}品牌"}, headers=headers)
    prod = await client.post(
        "/api/v1/products",
        json={"brand_id": brand.json()["id"], "name": f"{prefix}产品"},
        headers=headers,
    )
    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": prod.json()["id"], "code": f"{prefix}-SKU", "name": f"{prefix} SKU"},
        headers=headers,
    )
    # 创建生产批次（code-batches 的 production_batch_id 是必填）
    prod_batch = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": prod.json()["id"],
            "sku_id": sku.json()["id"],
            "batch_code": f"{prefix}-PB01",
            "production_date": str(date.today()),
            "expiry_date": str(date.today() + timedelta(days=365)),
        },
        headers=headers,
    )
    batch = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": prod.json()["id"],
            "sku_id": sku.json()["id"],
            "production_batch_id": prod_batch.json()["id"],
            "batch_code": f"{prefix}-001",
            "quantity": 1,
        },
        headers={
            **headers,
            "Idempotency-Key": str(uuid.uuid5(uuid.NAMESPACE_URL, f"resolver-edge:{prefix}")),
        },
    )
    batch_id = batch.json()["id"]
    await client.post(f"/api/v1/code-batches/{batch_id}/export", headers=headers)
    await client.post(f"/api/v1/code-batches/{batch_id}/mark-printing", headers=headers)
    await client.post(
        f"/api/v1/code-batches/{batch_id}/mark-delivered",
        json={"reason": "resolver edge test", "recipient": "test recipient", "confirm": "deliver"},
        headers=headers,
    )
    await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)

    items = await client.get(
        f"/api/v1/code-items?code_batch_id={batch_id}",
        headers=headers,
    )
    public_id = items.json()["items"][0]["public_id"]
    item_id = items.json()["items"][0]["id"]

    return public_id, item_id, tid


@pytest.fixture
async def expired_code(client: AsyncClient, db_session: AsyncSession):
    """创建一个 expired 状态的码"""
    import uuid

    from app.models.code import CodeItem, CodeItemStatus

    public_id, item_id, _ = await _create_code_chain(client, "EXP")

    # 设置为 expired
    await db_session.execute(
        db_update(CodeItem).where(CodeItem.id == uuid.UUID(item_id)).values(status=CodeItemStatus.expired)
    )
    await db_session.commit()

    return public_id


class TestExpiredCode:
    """expired 状态码不应颁发 scan_token"""

    @pytest.mark.anyio
    async def test_expired_code_json_returns_410(self, client: AsyncClient, expired_code: str):
        resp = await client.get(
            f"/c/{expired_code}",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 410, f"过期码 JSON 应返回 410，实际 {resp.status_code}"
        data = resp.json()
        assert data["code_data"]["status"] == "expired"

    @pytest.mark.anyio
    async def test_expired_code_html_returns_410(self, client: AsyncClient, expired_code: str):
        resp = await client.get(f"/c/{expired_code}")
        assert resp.status_code == 410, f"过期码 HTML 应返回 410，实际 {resp.status_code}"
        assert "过期" in resp.text

    @pytest.mark.anyio
    async def test_expired_code_no_scan_token(self, client: AsyncClient, expired_code: str):
        """过期码响应不应包含 scan_token"""
        resp = await client.get(
            f"/c/{expired_code}",
            headers={"Accept": "application/json"},
        )
        data = resp.json()
        assert "scan_token" not in data, "过期码不应颁发 scan_token"


class TestScanTokenTenantId:
    """验证 scan_token 包含 tenant_id"""

    @pytest.mark.anyio
    async def test_scan_token_contains_tenant_id(self, client: AsyncClient):
        """通过完整链路验证 scan_token 包含 tenant_id"""
        from app.services.scan_token import verify_scan_token

        public_id, _, tid = await _create_code_chain(client, "TID")

        # 扫码获取 scan_token
        scan_resp = await client.get(
            f"/c/{public_id}",
            headers={"Accept": "application/json"},
        )
        assert scan_resp.status_code == 200
        scan_token = scan_resp.json()["scan_token"]

        # 验证 token 包含 tenant_id
        payload = verify_scan_token(scan_token, public_id)
        assert payload is not None
        assert payload["tenant_id"] == tid, f"tenant_id 应为 {tid}，实际 {payload.get('tenant_id')}"


class TestVerificationAvailability:
    @pytest.mark.anyio
    async def test_scan_authority_failure_never_returns_a_repeat_scan_result(self, client: AsyncClient):
        public_id, _, _ = await _create_code_chain(client, "UNAVAILABLE")

        with patch("app.api.v1.resolver.record_scan_event", side_effect=RuntimeError("database unavailable")):
            response = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})

        assert response.status_code == 503
        assert response.json() == {
            "detail": "verification_unavailable",
            "code_data": {"result": "unavailable"},
        }
        assert "scan_token" not in response.json()
        assert "is_first_scan" not in response.text
        assert "verification_count" not in response.text


class TestClientIpExtraction:
    """验证 IP 提取逻辑"""

    def test_trusted_proxy_uses_first_untrusted_xff_hop(self):
        """可信代理链从右向左剥离，忽略 X-Real-IP。"""
        from app.utils.client_ip import get_client_ip

        request = MagicMock()
        request.headers = {"X-Real-IP": "1.2.3.4", "X-Forwarded-For": "5.6.7.8, 9.10.11.12"}
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        assert get_client_ip(request) == "9.10.11.12"

    def test_xff_fallback(self):
        """只移除可信代理，返回最靠近服务端的首个不可信 hop。"""
        from app.utils.client_ip import get_client_ip

        request = MagicMock()
        request.headers = {"X-Forwarded-For": "5.6.7.8, 9.10.11.12"}
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        assert get_client_ip(request) == "9.10.11.12"

    def test_untrusted_peer_cannot_spoof_forwarding_headers(self):
        from app.utils.client_ip import get_client_ip

        request = MagicMock()
        request.headers = {"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "5.6.7.8"}
        request.client = MagicMock()
        request.client.host = "198.51.100.8"

        assert get_client_ip(request) == "198.51.100.8"

    def test_invalid_forwarded_chain_fails_closed_to_peer(self):
        from app.utils.client_ip import get_client_ip

        request = MagicMock()
        request.headers = {"X-Forwarded-For": "not-an-ip"}
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        assert get_client_ip(request) == "127.0.0.1"

    def test_direct_connection_fallback(self):
        """无代理头时回退到直连 IP"""
        from app.utils.client_ip import get_client_ip

        request = MagicMock()
        request.headers = {}
        request.client = MagicMock()
        request.client.host = "192.168.1.1"

        assert get_client_ip(request) == "192.168.1.1"

    def test_no_client_returns_unknown(self):
        """无 client 对象时返回 unknown"""
        from app.utils.client_ip import get_client_ip

        request = MagicMock()
        request.headers = {}
        request.client = None

        assert get_client_ip(request) == "unknown"
