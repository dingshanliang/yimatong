"""SSE ticket 认证测试"""

import time
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.risk_dashboard import _sse_tickets
from app.core.database import get_db, get_db_with_bypass
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal

TENANT_ID = "019e887f-2d5d-70e2-a8ff-874a1d24791e"
ACCOUNT_ID = "019e887f-2d5d-70e2-a8ff-874a1d24791f"


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_with_bypass] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _auth_headers() -> dict:
    token = create_access_token(TENANT_ID, ACCOUNT_ID, "admin")
    return {"Authorization": f"Bearer {token}"}


# ── API 端点测试 ──


@pytest.mark.asyncio
async def test_create_sse_ticket_requires_auth(client: AsyncClient):
    """未认证请求应返回 401"""
    resp = await client.post("/api/v1/risk-dashboard/alerts/ticket")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_sse_ticket_returns_ticket(client: AsyncClient):
    """认证请求应返回有效 ticket"""
    resp = await client.post(
        "/api/v1/risk-dashboard/alerts/ticket",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "ticket" in data
    assert len(data["ticket"]) > 10


@pytest.mark.asyncio
async def test_sse_stream_rejects_missing_ticket(client: AsyncClient):
    """缺少 ticket 参数应返回 422"""
    resp = await client.get("/api/v1/risk-dashboard/alerts/stream")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sse_stream_rejects_invalid_ticket(client: AsyncClient):
    """无效 ticket 应返回 401"""
    resp = await client.get(
        "/api/v1/risk-dashboard/alerts/stream",
        params={"ticket": "invalid_ticket_value"},
    )
    assert resp.status_code == 401


# ── Ticket 逻辑单元测试 ──


@pytest.mark.asyncio
async def test_ticket_store_and_validate():
    """ticket 存储和验证逻辑"""
    import secrets

    ticket = secrets.token_urlsafe(32)
    _sse_tickets[ticket] = {
        "tenant_id": TENANT_ID,
        "exp": time.time() + 300,
    }

    # 验证 ticket 存在且未过期
    data = _sse_tickets.get(ticket)
    assert data is not None
    assert data["exp"] > time.time()
    assert data["tenant_id"] == TENANT_ID

    # pop 模拟一次性使用
    popped = _sse_tickets.pop(ticket, None)
    assert popped is not None
    assert _sse_tickets.pop(ticket, None) is None


@pytest.mark.asyncio
async def test_ticket_one_time_use_via_api(client: AsyncClient):
    """通过 API 验证 ticket 只能使用一次"""
    # 创建 ticket
    ticket_resp = await client.post(
        "/api/v1/risk-dashboard/alerts/ticket",
        headers=_auth_headers(),
    )
    ticket = ticket_resp.json()["ticket"]

    # 验证 ticket 存在于 store 中
    assert ticket in _sse_tickets

    # 第一次 pop（模拟 SSE 端点使用）
    data = _sse_tickets.pop(ticket, None)
    assert data is not None
    assert data["tenant_id"] == TENANT_ID

    # 第二次 pop 应返回 None
    assert _sse_tickets.pop(ticket, None) is None


@pytest.mark.asyncio
async def test_expired_ticket_rejected_by_stream(client: AsyncClient):
    """过期 ticket 应被 SSE 端点拒绝"""
    import secrets

    ticket = secrets.token_urlsafe(32)
    _sse_tickets[ticket] = {
        "tenant_id": TENANT_ID,
        "exp": time.time() - 1,  # 已过期
    }

    resp = await client.get(
        "/api/v1/risk-dashboard/alerts/stream",
        params={"ticket": ticket},
    )
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower() or "invalid" in resp.json()["detail"].lower()
