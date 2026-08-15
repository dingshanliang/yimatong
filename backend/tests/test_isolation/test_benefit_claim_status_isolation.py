"""消费者发放状态端点隔离：跨租户/跨主体统一不可查询 + 白名单精确形状。

yimatong-kc6d：公开路由 + 中间件绕过条件变更必须覆盖认证边界与跨租户场景
（AGENTS.md 验证要求）。SQLite 在此覆盖应用层 tenant_id 过滤与 token 主体
绑定；PostgreSQL RLS 策略的真实语义由
tests/test_acceptance/test_consumer_claim_status_authority.py 锁定。
"""

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitResult
from app.models.campaign import BenefitClaim, CampaignClaimOutbox
from app.services.benefit_claim_admission import build_claim_consumer_id
from app.services.claim_revisit_credential import issue_revisit_credential
from app.services.scan_token import create_scan_token, verify_scan_token
from tests.conftest import TestSessionLocal

_UNAVAILABLE_DETAIL = {"code": "claim_status_unavailable", "message": "该领取记录当前不可查询"}


def _token_for(tenant_id: uuid.UUID, public_id: str, visitor_id: str) -> tuple[str, str]:
    token = create_scan_token(public_id=public_id, ip_hash=None, tenant_id=str(tenant_id), visitor_id=visitor_id)
    return token, build_claim_consumer_id(verify_scan_token(token))


async def _seed_claim(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: str,
    *,
    outbox_status: str | None = "pending",
) -> uuid.UUID:
    claim_id = uuid.uuid4()
    session.add(
        BenefitClaim(
            id=claim_id,
            tenant_id=tenant_id,
            benefit_id=uuid.uuid4(),
            consumer_id=consumer_id,
            idempotency_key=f"claim:v1:{uuid.uuid4().hex}",
            claim_type="claim",
            status="success",
            delivery_status="pending",
            reserved_amount=100,
            reservation_status="reserved",
        )
    )
    if outbox_status is not None:
        session.add(
            CampaignClaimOutbox(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                claim_id=claim_id,
                event_type="claim_committed",
                payload={},
                status=outbox_status,
                attempt_count=0,
                max_attempts=8,
            )
        )
    await session.commit()
    return claim_id


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession, monkeypatch):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        "app.api.v1.benefit_claim_status.rate_limiter.check_shared",
        AsyncMock(return_value=RateLimitResult(allowed=True)),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class TestClaimStatusIsolation:
    @pytest.mark.anyio
    async def test_same_tenant_and_consumer_reads_real_status(self, client, db_session):
        """正控：同租户同主体走真实 DB 派生链，返回 processing。"""

        tenant_a = uuid.uuid4()
        token_a, consumer_a = _token_for(tenant_a, "pk-iso-a", "visitor-a")
        claim_id = await _seed_claim(db_session, tenant_a, consumer_a)

        response = await client.get(
            f"/api/v1/benefit-claims/{claim_id}/status",
            headers={"Authorization": f"Bearer {token_a}"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "processing"

    @pytest.mark.anyio
    async def test_cross_tenant_token_gets_uniform_unavailable(self, client, db_session):
        """租户 B 的 scan_token 查租户 A 的 claim → 统一不可查询，不泄露存在性。"""

        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
        token_a, consumer_a = _token_for(tenant_a, "pk-iso-a", "visitor-a")
        token_b, _consumer_b = _token_for(tenant_b, "pk-iso-b", "visitor-b")
        claim_id = await _seed_claim(db_session, tenant_a, consumer_a)

        response = await client.get(
            f"/api/v1/benefit-claims/{claim_id}/status",
            headers={"Authorization": f"Bearer {token_b}"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == _UNAVAILABLE_DETAIL

    @pytest.mark.anyio
    async def test_same_tenant_other_consumer_gets_uniform_unavailable(self, client, db_session):
        """同租户但主体不同（另一位扫码者）→ 与跨租户语义完全一致。"""

        tenant_a = uuid.uuid4()
        token_owner, consumer_owner = _token_for(tenant_a, "pk-iso-a", "visitor-owner")
        token_other, _ = _token_for(tenant_a, "pk-iso-a", "visitor-other")
        token_cross_tenant, _ = _token_for(uuid.uuid4(), "pk-iso-b", "visitor-b")
        claim_id = await _seed_claim(db_session, tenant_a, consumer_owner)

        responses = [
            await client.get(
                f"/api/v1/benefit-claims/{claim_id}/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            for token in (token_other, token_cross_tenant)
        ]

        bodies = [r.json()["detail"] for r in responses]
        assert all(r.status_code == 404 for r in responses)
        assert bodies[0] == bodies[1] == _UNAVAILABLE_DETAIL

    @pytest.mark.anyio
    async def test_revisit_credential_cannot_cross_claims_or_tenants(self, client, db_session):
        """回访凭证只绑定签发时的三元组：跨 claim 查询一律统一不可查询。"""

        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
        token_a, consumer_a = _token_for(tenant_a, "pk-iso-a", "visitor-a")
        own_claim = await _seed_claim(db_session, tenant_a, consumer_a)
        other_claim_tenant_a = await _seed_claim(db_session, tenant_a, "anon:v1:someone-else")
        other_claim_tenant_b = await _seed_claim(db_session, tenant_b, "anon:v1:someone-else")
        credential = issue_revisit_credential(tenant_a, own_claim, consumer_a)

        responses = [
            await client.get(
                f"/api/v1/benefit-claims/{claim_id}/status",
                headers={"Authorization": f"Bearer {credential}"},
            )
            for claim_id in (other_claim_tenant_a, other_claim_tenant_b)
        ]

        assert all(r.status_code == 404 for r in responses)
        assert all(r.json()["detail"] == _UNAVAILABLE_DETAIL for r in responses)


class TestClaimStatusWhitelistShape:
    @pytest.mark.anyio
    async def test_whitelisted_shape_has_no_registered_mutation(self, client):
        """白名单按路径匹配且方法无关：该形状下只允许注册过的 GET，POST 必须 405。"""

        response = await client.post(f"/api/v1/benefit-claims/{uuid.uuid4()}/status")

        assert response.status_code == 405

    @pytest.mark.anyio
    async def test_whitelist_does_not_extend_to_sibling_paths(self, client):
        """前缀下新增路由不继承放行：兄弟路径未带凭证必须被中间件拦截（401），而非路由 405。"""

        response = await client.post(f"/api/v1/benefit-claims/{uuid.uuid4()}/retry")

        assert response.status_code == 401
