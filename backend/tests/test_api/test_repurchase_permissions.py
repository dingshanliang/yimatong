"""回归测试：复购/会员通知管理端点必须使用真实存在的 campaign:manage 权限码。

历史缺陷：repurchase_coupons / repurchase_workbench / member_notifications 的管理
端点曾使用不存在的权限码 campaign:write（任何角色都没有）→ 线上全部 403。
admin/operator 均持有 campaign:manage（与 campaigns/benefits 管理端点同款）。
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api_client:
        yield api_client
    app.dependency_overrides.clear()


def _auth_headers(tenant_id: uuid.UUID | str, account_id: uuid.UUID | str, role: str = "admin") -> dict:
    token = create_access_token(str(tenant_id), str(account_id), role)
    return {"Authorization": f"Bearer {token}"}


def _durable_auth_headers(tenant_id: uuid.UUID | str, account_id: uuid.UUID | str, role: str = "admin") -> dict:
    """带 durable session sid 的 admin token（workbench 写端点要求会话绑定）。"""
    token = create_access_token(str(tenant_id), str(account_id), role, extra={"sid": str(uuid.uuid4())})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.anyio
async def test_admin_can_create_repurchase_coupon_rule(client: AsyncClient):
    """POST /repurchase-coupon-rules 曾因 campaign:write 全员 403；admin 现在必须能进到业务层。"""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    rule = SimpleNamespace(
        id=uuid.uuid4(),
        rule_key="repurchase-10-off",
        version=1,
        benefit_id=None,
        name="复购立减",
        amount_minor=1000,
        minimum_spend_minor=5000,
        currency="CNY",
        product_scope="all",
        eligible_product_refs=[],
        channel_scope="both",
        validity_mode="relative",
        valid_days=30,
        fixed_valid_from=None,
        fixed_valid_until=None,
        issuance_limit=100,
        issued_count=0,
        status="draft",
        published_at=None,
        ended_at=None,
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "app.api.v1.repurchase_coupons.create_coupon_rule",
            _async_return(rule),
        )
        response = await client.post(
            "/api/v1/repurchase-coupon-rules",
            json={
                "rule_key": "repurchase-10-off",
                "version": 1,
                "name": "复购立减",
                "amount_minor": 1000,
                "minimum_spend_minor": 5000,
                "channel_scope": "both",
                "validity_mode": "relative",
                "valid_days": 30,
                "issuance_limit": 100,
                "idempotency_key": "rule-create-001",
            },
            headers=_auth_headers(tenant_id, account_id, "admin"),
        )

    assert response.status_code == 201
    assert response.json()["id"] == str(rule.id)


@pytest.mark.anyio
async def test_admin_can_create_member_marketing_notification(client: AsyncClient):
    """POST /member-notifications/marketing-events 曾因 campaign:write 全员 403。"""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    notification_id = uuid.uuid4()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "app.api.v1.member_notifications.create_marketing_notification",
            _async_return(SimpleNamespace(id=notification_id)),
        )
        response = await client.post(
            "/api/v1/member-notifications/marketing-events",
            json={
                "membership_id": str(uuid.uuid4()),
                "notification_type": "coupon_expiry",
                "source_product": "大米",
                "source_event_id": "evt-001",
                "source_event_version": 1,
                "object_ref": "obj-001",
                "title": "优惠券即将过期",
                "body": "您的优惠券还有 3 天过期",
                "occurred_at": datetime.now(UTC).isoformat(),
                "template_code": "coupon_expiry_v1",
                "template_version": "1",
            },
            headers=_auth_headers(tenant_id, account_id, "admin"),
        )

    assert response.status_code == 200
    assert response.json()["created"] is True
    assert response.json()["notification_id"] == str(notification_id)


@pytest.mark.anyio
async def test_admin_can_create_repurchase_work_item(client: AsyncClient):
    """POST /members/repurchase-workbench/work-items 曾因 campaign:write 全员 403。"""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    work_item_id = uuid.uuid4()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "app.api.v1.repurchase_workbench.mutate_work_item",
            _async_return(work_item_id),
        )
        response = await client.post(
            "/api/v1/members/repurchase-workbench/work-items",
            json={
                "category": "coupon_issue_failure",
                "business_ref": "order-001",
                "title": "券发放失败",
                "impact_summary": "会员未收到券，需要人工补发",
                "priority": "high",
                "owner_account_id": str(account_id),
                "due_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "reason": "发放通道返回失败",
            },
            headers=_durable_auth_headers(tenant_id, account_id, "admin"),
        )

    assert response.status_code == 201
    assert response.json()["id"] == str(work_item_id)


@pytest.mark.anyio
async def test_viewer_still_rejected_for_missing_campaign_manage(client: AsyncClient):
    """负向对照：无 campaign:manage 的 viewer 必须仍然 403，证明权限依赖真实生效。"""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()

    response = await client.post(
        "/api/v1/member-notifications/marketing-events",
        json={
            "membership_id": str(uuid.uuid4()),
            "notification_type": "coupon_expiry",
            "source_product": "大米",
            "source_event_id": "evt-001",
            "source_event_version": 1,
            "object_ref": "obj-001",
            "title": "优惠券即将过期",
            "body": "您的优惠券还有 3 天过期",
            "occurred_at": datetime.now(UTC).isoformat(),
            "template_code": "coupon_expiry_v1",
            "template_version": "1",
        },
        headers=_auth_headers(tenant_id, account_id, "viewer"),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing permission: campaign:manage"


def _async_return(value):
    async def _factory(*_args, **_kwargs):
        return value

    return _factory
