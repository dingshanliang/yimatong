"""GET /benefit-claims/{claim_id}/status：scan_token 鉴权、防枚举、独立限流、中间件放行。"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitResult
from app.services.benefit_claim_status import ConsumerClaimStatus
from app.services.scan_token import create_scan_token


class _FakeDialect:
    name = "sqlite"


class _FakeBind:
    dialect = _FakeDialect()


class _FakeSession:
    """最小形状：让 set_session_tenant_context 走真实 SQLite 分支（跳过 SET 语句）。"""

    def get_bind(self) -> _FakeBind:
        return _FakeBind()


@pytest.fixture
async def status_client(monkeypatch):
    async def override_get_db():
        yield _FakeSession()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        "app.api.v1.benefit_claim_status.rate_limiter.check",
        AsyncMock(return_value=RateLimitResult(allowed=True)),
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, monkeypatch
    finally:
        app.dependency_overrides.clear()


def _token(tenant_id: uuid.UUID, visitor_id: str = "visitor-1") -> str:
    return create_scan_token(
        public_id="pk-status-1",
        ip_hash=None,
        tenant_id=str(tenant_id),
        visitor_id=visitor_id,
    )


def _stub_service(monkeypatch, result):
    stub = AsyncMock(return_value=result)
    monkeypatch.setattr("app.api.v1.benefit_claim_status.get_consumer_claim_status", stub)
    return stub


@pytest.mark.anyio
async def test_status_returns_derived_payload_and_passes_middleware_without_admin_jwt(status_client):
    """白名单放行 + Bearer scan_token + 服务派生结果原样返回。"""

    client, monkeypatch = status_client
    tenant_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    completed = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    stub = _stub_service(
        monkeypatch,
        ConsumerClaimStatus(status="success", amount_minor=88, completed_at=completed),
    )

    response = await client.get(
        f"/api/v1/benefit-claims/{claim_id}/status",
        headers={"Authorization": f"Bearer {_token(tenant_id)}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["amount_minor"] == 88
    assert body["completed_at"] == completed.isoformat()
    assert body["failure_reason"] is None
    assert stub.await_count == 1
    call = stub.await_args
    assert call.args[1] == tenant_id
    assert call.args[2] == claim_id
    assert call.args[3].startswith("anon:v1:")  # 主体身份由 token 派生


@pytest.mark.anyio
async def test_status_missing_token_is_401_before_service(status_client):
    client, monkeypatch = status_client
    stub = _stub_service(monkeypatch, None)

    response = await client.get(f"/api/v1/benefit-claims/{uuid.uuid4()}/status")

    assert response.status_code == 401
    stub.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("scenario", ["invalid_token", "service_none"])
async def test_status_uses_uniform_unavailable_semantics(status_client, scenario):
    """防枚举：token 无效与记录不存在/主体不匹配返回完全相同的响应。"""

    client, monkeypatch = status_client
    _stub_service(monkeypatch, None if scenario == "service_none" else ConsumerClaimStatus(status="processing"))

    if scenario == "invalid_token":
        response = await client.get(
            f"/api/v1/benefit-claims/{uuid.uuid4()}/status",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
    else:
        response = await client.get(
            f"/api/v1/benefit-claims/{uuid.uuid4()}/status",
            headers={"Authorization": f"Bearer {_token(uuid.uuid4())}"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "claim_status_unavailable",
        "message": "该领取记录当前不可查询",
    }


@pytest.mark.anyio
async def test_status_independent_rate_limit_returns_429(status_client, monkeypatch):
    client = status_client[0]
    monkeypatch.setattr(
        "app.api.v1.benefit_claim_status.rate_limiter.check",
        AsyncMock(return_value=RateLimitResult(allowed=False, retry_after=60)),
    )

    response = await client.get(
        f"/api/v1/benefit-claims/{uuid.uuid4()}/status",
        headers={"Authorization": f"Bearer {_token(uuid.uuid4())}"},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"


@pytest.mark.anyio
async def test_status_rejects_non_uuid_claim_id(status_client):
    client, _ = status_client

    response = await client.get(
        "/api/v1/benefit-claims/not-a-uuid/status",
        headers={"Authorization": "Bearer anything"},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_status_token_without_tenant_subject_is_unavailable(status_client):
    """token 缺 tenant_id 或无法派生主体 → 统一不可查询，不泄露原因。"""

    client, monkeypatch = status_client
    stub = _stub_service(monkeypatch, ConsumerClaimStatus(status="processing"))
    token = create_scan_token(public_id="pk", ip_hash=None)  # 无 tenant/visitor

    response = await client.get(
        f"/api/v1/benefit-claims/{uuid.uuid4()}/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    stub.assert_not_awaited()
