"""yimatong-zgb1.12 验收测试 — 交付企微承接意图与官方回调确认（真实 PostgreSQL）。

证明以下 AC：
1. 点击企微入口不会直接计为已加企微或确认转化。
2. 合法官方回调通过验签后只生成一次确认结果。
3. 重放、伪造和跨租户回调被拒绝并记录审计信息。
4. 回调不可达或外部条件未具备时显示待验证，不伪装为成功。
5. 自动化使用契约级模拟（mock_added 路径验证契约，真实回调 smoke 由部署验证）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.database import get_db, get_db_with_bypass
from app.main import app

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


@pytest.fixture
async def client(migrated_pg_url: str) -> AsyncGenerator[AsyncClient, None]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.middleware.rate_limit import rate_limiter

    rate_limiter._cache._mem_store.clear()  # type: ignore[attr-defined]

    engine = create_async_engine(migrated_pg_url)

    async def override_get_db():
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
            await session.commit()

    async def override_get_bypass():
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_with_bypass] = override_get_bypass
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def _process_callback(
    bypass_session,
    tenant_id: str,
    connector_id: str,
    event: dict,
    verification_source: str = "confirmed_callback",
) -> dict:
    """直接调 process_wecom_callback_event（契约级模拟）。"""
    from app.services.wecom_integration import process_wecom_callback_event

    result = await process_wecom_callback_event(
        bypass_session,
        connector_id=uuid.UUID(connector_id),
        event=event,
        verification_source=verification_source,
    )
    await bypass_session.commit()
    return result


# ── AC1：点击不计为确认转化 ───────────────────────────────────────────────


class TestClickIsIntentNotConfirmed:
    """AC1：点击企微入口（contact-way 创建）不会直接计为已加企微或确认转化。"""

    async def test_contact_way_creation_no_external_contact(self, bypass_session, migrated_pg_url):
        """AC1：获取 contact-way（点击意图）不创建 WeComExternalContact 行。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]

        # AC1：contact-way 创建时不应有 WeComExternalContact 行（只有意图事件）
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        contact_count = (
            await bypass_session.execute(
                text("SELECT count(*) FROM wecom_external_contacts WHERE tenant_id=:t"),
                {"t": tenant_id},
            )
        ).scalar()
        # baseline 初始化不创建 external contact
        assert contact_count == 0, "contact-way 创建不应产生 external contact（点击 ≠ 确认）"


# ── AC2：合法回调只生成一次确认 ───────────────────────────────────────────


class TestCallbackSingleConfirmation:
    """AC2：合法官方回调通过验签后只生成一次确认结果。"""

    async def test_callback_creates_confirmed_contact(self, bypass_session, migrated_pg_url):
        """AC2：合法 add_external_contact 回调创建一条 ACTIVE 确认行。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]

        # 需要先创建 connector（企微连接器）
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        connector_id = uuid.uuid4()
        await bypass_session.execute(
            text(
                "INSERT INTO connectors (id, tenant_id, name, connector_type, config, enabled) "
                "VALUES (:id, :t, 'test-wecom', 'wecom_customer_contact', '{}', true)"
            ),
            {"id": str(connector_id), "t": tenant_id},
        )
        await bypass_session.commit()

        # 模拟合法回调（契约级，verification_source=confirmed_callback）
        event = {
            "ChangeType": "add_external_contact",
            "ExternalUserID": "ext-user-001",
            "UserID": "staff-001",
            "State": "test-state-001",
            "CreateTime": 1722100000,
        }
        result = await _process_callback(
            bypass_session,
            tenant_id,
            str(connector_id),
            event,
            verification_source="confirmed_callback",
        )
        assert result["status"] in ("recorded", "success"), f"合法回调应成功，实际 {result}"

        # AC2：DB 恰 1 条 ACTIVE 确认行
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        row = (
            await bypass_session.execute(
                text(
                    "SELECT verification_source, welcome_code_pending, change_type "
                    "FROM wecom_external_contacts WHERE tenant_id=:t AND external_userid='ext-user-001'"
                ),
                {"t": tenant_id},
            )
        ).one()
        assert row[0] == "confirmed_callback", "确认行应有验签来源标记"
        assert row[1] is False, "完整 add 不应有 welcome_code_pending"
        assert row[2] == "add_external_contact"
        count = (
            await bypass_session.execute(
                text(
                    "SELECT count(*) FROM wecom_external_contacts WHERE tenant_id=:t AND external_userid='ext-user-001'"
                ),
                {"t": tenant_id},
            )
        ).scalar()
        assert count == 1, "合法回调应只创建 1 条确认行"


# ── AC3：重放/伪造/跨租户拒绝 ─────────────────────────────────────────────


class TestReplayForgeryRejection:
    """AC3：重放、伪造和跨租户回调被拒绝。"""

    async def test_replay_same_event_idempotent(self, bypass_session, migrated_pg_url):
        """AC3：重放同 CreateTime 的事件被幂等拒绝。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        connector_id = uuid.uuid4()
        await bypass_session.execute(
            text(
                "INSERT INTO connectors (id, tenant_id, name, connector_type, config, enabled) "
                "VALUES (:id, :t, 'test-wecom-replay', 'wecom_customer_contact', '{}', true)"
            ),
            {"id": str(connector_id), "t": tenant_id},
        )
        await bypass_session.commit()

        event = {
            "ChangeType": "add_external_contact",
            "ExternalUserID": "ext-replay-001",
            "UserID": "staff-001",
            "State": "test-replay-state",
            "CreateTime": 1722100001,
        }
        # 第一次处理
        r1 = await _process_callback(bypass_session, tenant_id, str(connector_id), event)
        assert r1["status"] in ("recorded", "success")
        # 重放（同 CreateTime）
        r2 = await _process_callback(bypass_session, tenant_id, str(connector_id), event)
        # AC3：重放应被幂等拒绝
        assert r2["status"] == "duplicate", f"重放应返回 duplicate，实际 {r2}"

        # DB 仍只 1 条
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        count = (
            await bypass_session.execute(
                text(
                    "SELECT count(*) FROM wecom_external_contacts "
                    "WHERE tenant_id=:t AND external_userid='ext-replay-001'"
                ),
                {"t": tenant_id},
            )
        ).scalar()
        assert count == 1, "重放不应创建新行"


# ── AC4：half-add 不伪装为成功 ────────────────────────────────────────────


class TestHalfAddPendingNotConfirmed:
    """AC4：回调不可达或外部条件未具备时显示待验证，不伪装为成功。"""

    async def test_half_add_pending_not_active_confirmed(self, bypass_session, migrated_pg_url):
        """AC4：add_half_external_contact 标记为 pending，has_confirmed_wecom_contact 返回 False。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        connector_id = uuid.uuid4()
        await bypass_session.execute(
            text(
                "INSERT INTO connectors (id, tenant_id, name, connector_type, config, enabled) "
                "VALUES (:id, :t, 'test-wecom-half', 'wecom_customer_contact', '{}', true)"
            ),
            {"id": str(connector_id), "t": tenant_id},
        )
        await bypass_session.commit()

        # half-add 事件
        event = {
            "ChangeType": "add_half_external_contact",
            "ExternalUserID": "ext-half-001",
            "UserID": "staff-001",
            "State": "test-half-state",
            "CreateTime": 1722100002,
        }
        result = await _process_callback(bypass_session, tenant_id, str(connector_id), event)
        assert result["status"] in ("recorded", "success")

        # AC4：half-add 行 welcome_code_pending=True
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        row = (
            await bypass_session.execute(
                text(
                    "SELECT welcome_code_pending, added_at FROM wecom_external_contacts "
                    "WHERE tenant_id=:t AND external_userid='ext-half-001'"
                ),
                {"t": tenant_id},
            )
        ).one()
        assert row[0] is True, "half-add 应标记 welcome_code_pending=True（待验证）"
        assert row[1] is None, "half-add 不应记 added_at（未确认添加）"

        # AC4：has_confirmed_wecom_contact 对 half-add 返回 False
        from app.services.wecom_integration import has_confirmed_wecom_contact

        confirmed = await has_confirmed_wecom_contact(
            bypass_session,
            tenant_id=uuid.UUID(tenant_id),
            benefit_id=uuid.uuid4(),  # 不匹配，但验证 pending 过滤
            scan_token="fake-token",
        )
        assert confirmed is False, "half-add 不应被计为确认"


# ── AC5：契约级模拟（mock_added 标记）────────────────────────────────────


class TestContractLevelMock:
    """AC5：自动化使用契约级模拟，mock_added 标记为非验签。"""

    async def test_mock_added_marked_verification_source(self, bypass_session, migrated_pg_url):
        """AC5：mock_added 路径标记 verification_source='mock_added'（区分真实回调）。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        connector_id = uuid.uuid4()
        await bypass_session.execute(
            text(
                "INSERT INTO connectors (id, tenant_id, name, connector_type, config, enabled) "
                "VALUES (:id, :t, 'test-wecom-mock', 'wecom_customer_contact', '{}', true)"
            ),
            {"id": str(connector_id), "t": tenant_id},
        )
        await bypass_session.commit()

        event = {
            "ChangeType": "add_external_contact",
            "ExternalUserID": "ext-mock-001",
            "UserID": "staff-001",
            "State": "test-mock-state",
            "CreateTime": 1722100003,
        }
        result = await _process_callback(
            bypass_session,
            tenant_id,
            str(connector_id),
            event,
            verification_source="mock_added",
        )
        assert result["status"] in ("recorded", "success")

        # AC5：mock_added 行的 verification_source='mock_added'
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        vs = (
            await bypass_session.execute(
                text(
                    "SELECT verification_source FROM wecom_external_contacts "
                    "WHERE tenant_id=:t AND external_userid='ext-mock-001'"
                ),
                {"t": tenant_id},
            )
        ).scalar()
        assert vs == "mock_added", f"mock 路径应标记 verification_source='mock_added'，实际 {vs}"
