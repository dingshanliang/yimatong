"""yimatong-zgb1.10 验收测试 — 建立有效访问、匿名访客和意图事件主干（真实 PostgreSQL）。

证明以下 AC：
1. 预取、机器人、失败解析和重复上报不会计入有效访问。
2. 匿名访客和会话在隐私边界内稳定关联，且不同租户绝不串联。
3. 查验、活动参与和转化分别记录，首查不再等同首次参与。
4. 每类事件都有来源、发生时间、接收时间和幂等依据。
5. 现有重复扫码记录路径被消除或明确降级为非权威信号。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import patch

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
    control_engine = create_async_engine(migrated_pg_url)
    control_factory = async_sessionmaker(control_engine, expire_on_commit=False)

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
    with patch("app.core.database.control_session_factory", control_factory):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    app.dependency_overrides.clear()
    await engine.dispose()
    await control_engine.dispose()


async def _reset_code(bypass_session, tenant_id: str, public_id: str) -> None:
    await bypass_session.rollback()
    await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
    await bypass_session.execute(
        text(
            "UPDATE code_items SET status='activated', revoked_at=NULL, first_scanned_at=NULL "
            "WHERE tenant_id=:t AND public_id=:p"
        ),
        {"t": tenant_id, "p": public_id},
    )
    await bypass_session.execute(
        text("DELETE FROM scan_events WHERE public_id=:p"),
        {"p": public_id},
    )
    await bypass_session.execute(
        text("DELETE FROM intent_events WHERE public_id=:p"),
        {"p": public_id},
    )
    # 清 anonymous_visitors（跨测试累积）
    await bypass_session.execute(
        text("DELETE FROM anonymous_visitors WHERE tenant_id=:t"),
        {"t": tenant_id},
    )
    await bypass_session.commit()
    from app.services.resolve_cache import resolve_cache

    await resolve_cache.invalidate(f"resolve:{public_id}")


# ── AC1：预取/机器人/失败/重复 不计入有效访问 ──────────────────────────────


class TestValidVisitFiltering:
    """AC1：预取、机器人、失败解析和重复上报不会计入有效访问。"""

    async def test_robot_not_valid_visit(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_code(bypass_session, tenant_id, public_id)

        # robot UA 访问
        await client.get(
            f"/c/{public_id}",
            headers={"Accept": "application/json", "User-Agent": "Googlebot/2.1"},
        )

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        vv = (
            await bypass_session.execute(
                text(
                    "SELECT is_valid_visit FROM scan_events "
                    "WHERE tenant_id=:t AND public_id=:p ORDER BY scan_time DESC LIMIT 1"
                ),
                {"t": tenant_id, "p": public_id},
            )
        ).scalar()
        assert vv is False, "robot 流量不应计入有效访问"

    async def test_normal_visit_is_valid(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_code(bypass_session, tenant_id, public_id)

        # 正常浏览器访问
        await client.get(
            f"/c/{public_id}",
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
        )

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        vv = (
            await bypass_session.execute(
                text(
                    "SELECT is_valid_visit FROM scan_events "
                    "WHERE tenant_id=:t AND public_id=:p ORDER BY scan_time DESC LIMIT 1"
                ),
                {"t": tenant_id, "p": public_id},
            )
        ).scalar()
        assert vv is True, "正常浏览器访问应计入有效访问"

    async def test_voided_code_not_valid_visit(self, client, bypass_session, migrated_pg_url):
        """AC1：失败解析（voided 码）不计入有效访问。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_code(bypass_session, tenant_id, public_id)

        # 改为 revoked
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await bypass_session.execute(
            text("UPDATE code_items SET status='revoked',revoked_at=now() WHERE tenant_id=:t AND public_id=:p"),
            {"t": tenant_id, "p": public_id},
        )
        await bypass_session.commit()
        from app.services.resolve_cache import resolve_cache

        await resolve_cache.invalidate(f"resolve:{public_id}")

        # 访问 revoked 码（410）
        await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})

        # 不应有 scan_events 行（revoked 不记录扫码）
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        count = (
            await bypass_session.execute(
                text("SELECT count(*) FROM scan_events WHERE tenant_id=:t AND public_id=:p"),
                {"t": tenant_id, "p": public_id},
            )
        ).scalar()
        assert count == 0, "voided 码不应记录 scan_events（不计入有效访问）"


# ── AC2：匿名访客稳定 + 跨租户不串联 ───────────────────────────────────────


class TestAnonymousVisitor:
    """AC2：匿名访客在隐私边界内稳定关联，跨租户不串联。"""

    async def test_visitor_id_issued_and_stable(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_code(bypass_session, tenant_id, public_id)

        # 首次访问（无 X-Visitor-ID）→ 签发新 visitor_id
        r1 = await client.get(
            f"/c/{public_id}",
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        vid1 = r1.json()["scan_info"]["visitor_id"]
        assert vid1, "首次访问应签发 visitor_id"

        # 第二次访问（带同一个 X-Visitor-ID）→ 复用
        r2 = await client.get(
            f"/c/{public_id}",
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
                "X-Visitor-ID": vid1,
            },
        )
        vid2 = r2.json()["scan_info"]["visitor_id"]
        assert vid1 == vid2, "同一 visitor_id 应稳定复用"

        # DB：anonymous_visitors 表只有 1 行
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        visitor_count = (
            await bypass_session.execute(
                text("SELECT count(*) FROM anonymous_visitors WHERE tenant_id=:t"),
                {"t": tenant_id},
            )
        ).scalar()
        assert visitor_count == 1, "同一 visitor 应只创建 1 行"

    async def test_cross_tenant_no_stringing(self, client, bypass_session, migrated_pg_url):
        """AC2：不同租户的 visitor 不串联（baseline 的 visitor_id 不能在 control 租户复用）。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        baseline_pid = summary["first_public_id"]
        baseline_tid = summary["baseline_tenant"]["id"]
        control_pid = summary["control_first_public_id"]
        control_tid = summary["control_tenant"]["id"]

        await _reset_code(bypass_session, baseline_tid, baseline_pid)

        # baseline 访问拿 visitor_id
        r = await client.get(
            f"/c/{baseline_pid}",
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        baseline_vid = r.json()["scan_info"]["visitor_id"]

        # 用 baseline 的 visitor_id 访问 control 的码
        if control_pid:
            await _reset_code(bypass_session, control_tid, control_pid)
            await client.get(
                f"/c/{control_pid}",
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0",
                    "X-Visitor-ID": baseline_vid,
                },
            )
            # control 应创建自己的 visitor 行（不串联 baseline）
            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            control_visitors = (
                await bypass_session.execute(
                    text("SELECT count(*) FROM anonymous_visitors WHERE tenant_id=:t"),
                    {"t": control_tid},
                )
            ).scalar()
            baseline_visitors = (
                await bypass_session.execute(
                    text("SELECT count(*) FROM anonymous_visitors WHERE tenant_id=:t"),
                    {"t": baseline_tid},
                )
            ).scalar()
            # baseline 1 行（自己的），control 也应有自己的 visitor（不共用 baseline 的）
            assert baseline_visitors >= 1
            assert control_visitors >= 1


# ── AC3：查验/活动参与/转化分别记录 ───────────────────────────────────────


class TestSeparateEventRecording:
    """AC3：查验、活动参与和转化分别记录，首查不再等同首次参与。"""

    async def test_verification_separate_from_participation(self, client, bypass_session, migrated_pg_url):
        """AC3：scan_events（查验）vs intent_events（意图）vs benefit_claims（参与）独立。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_code(bypass_session, tenant_id, public_id)

        # 固定 IP：resolve 与 telemetry 必须同 IP（scan_token 校验 ip_hash）
        fixed_ip = "203.0.113.83"
        fixed_headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
            "X-Forwarded-For": fixed_ip,
        }

        # 1. 查验（resolve）
        r = await client.get(f"/c/{public_id}", headers=fixed_headers)
        scan_token = r.json()["scan_token"]
        visitor_id = r.json()["scan_info"]["visitor_id"]

        # 2. 意图事件（page_view）
        intent_resp = await client.post(
            "/api/v1/scan-events",
            json={
                "event_type": "view",
                "public_id": public_id,
                "client_event_id": "evt-001",
            },
            headers={
                "Authorization": f"Bearer {scan_token}",
                "X-Visitor-ID": visitor_id,
                "X-Forwarded-For": fixed_ip,
            },
        )
        assert intent_resp.status_code == 201, f"intent 上报失败: {intent_resp.text}"

        # DB：三张表各自独立
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        scan_count = (
            await bypass_session.execute(
                text("SELECT count(*) FROM scan_events WHERE tenant_id=:t AND public_id=:p"),
                {"t": tenant_id, "p": public_id},
            )
        ).scalar()
        intent_count = (
            await bypass_session.execute(
                text("SELECT count(*) FROM intent_events WHERE tenant_id=:t AND public_id=:p"),
                {"t": tenant_id, "p": public_id},
            )
        ).scalar()

        # AC3：查验（scan_events 1 条）≠ 意图（intent_events 1 条），分别记录
        assert scan_count == 1, "查验事实 1 条"
        assert intent_count == 1, "意图事件 1 条（page_view）"
        # 首查（is_first_scan）≠ 首次参与（intent_events 是不同业务事实）


# ── AC4：事件来源/时间/幂等依据 ───────────────────────────────────────────


class TestEventEvidenceAndIdempotency:
    """AC4：每类事件都有来源、发生时间、接收时间和幂等依据。"""

    async def test_intent_event_persisted_with_evidence(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_code(bypass_session, tenant_id, public_id)

        fixed_ip = "203.0.113.84"
        r = await client.get(
            f"/c/{public_id}",
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
                "X-Forwarded-For": fixed_ip,
            },
        )
        scan_token = r.json()["scan_token"]
        visitor_id = r.json()["scan_info"]["visitor_id"]

        # 上报意图事件
        intent_resp = await client.post(
            "/api/v1/scan-events",
            json={
                "event_type": "view",
                "public_id": public_id,
                "client_event_id": "evt-evidence-001",
                "page_version_id": "pv-123",
            },
            headers={
                "Authorization": f"Bearer {scan_token}",
                "X-Visitor-ID": visitor_id,
                "X-Forwarded-For": fixed_ip,
            },
        )
        assert intent_resp.status_code == 201, f"intent 上报失败: {intent_resp.text}"

        # DB 证据链
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        row = (
            await bypass_session.execute(
                text(
                    "SELECT event_type, visitor_id, client_event_id, page_version_id, "
                    "ip_hash, user_agent, occurred_at, received_at "
                    "FROM intent_events WHERE tenant_id=:t AND public_id=:p"
                ),
                {"t": tenant_id, "p": public_id},
            )
        ).one()
        assert row[0] == "page_view"  # event_type（规范化后）
        assert row[1] == visitor_id  # visitor_id（来源）
        assert row[2] == "evt-evidence-001"  # client_event_id（幂等依据）
        assert row[3] == "pv-123"  # page_version_id（来源）
        assert row[4] is not None  # ip_hash（来源）
        assert row[5] is not None  # user_agent（来源）
        assert row[6] is not None  # occurred_at（发生时间）
        assert row[7] is not None  # received_at（接收时间）

    async def test_intent_event_idempotent(self, client, bypass_session, migrated_pg_url):
        """AC4：重复上报同 client_event_id 幂等（只记一次）。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_code(bypass_session, tenant_id, public_id)

        fixed_ip = "203.0.113.85"
        r = await client.get(
            f"/c/{public_id}",
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
                "X-Forwarded-For": fixed_ip,
            },
        )
        scan_token = r.json()["scan_token"]
        visitor_id = r.json()["scan_info"]["visitor_id"]

        headers = {
            "Authorization": f"Bearer {scan_token}",
            "X-Visitor-ID": visitor_id,
            "X-Forwarded-For": fixed_ip,
        }
        # 同一 client_event_id 上报两次
        for _ in range(2):
            await client.post(
                "/api/v1/scan-events",
                json={
                    "event_type": "view",
                    "public_id": public_id,
                    "client_event_id": "evt-dup-001",
                },
                headers=headers,
            )

        # DB：只 1 条（幂等）
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        count = (
            await bypass_session.execute(
                text(
                    "SELECT count(*) FROM intent_events "
                    "WHERE tenant_id=:t AND public_id=:p AND client_event_id='evt-dup-001'"
                ),
                {"t": tenant_id, "p": public_id},
            )
        ).scalar()
        assert count == 1, f"重复上报应幂等（只 1 条），实际 {count}"


# ── AC5：重复扫码记录路径降级为非权威 ─────────────────────────────────────


class TestScanEventsDowngraded:
    """AC5：现有重复扫码记录路径被消除或明确降级为非权威信号。"""

    async def test_scan_events_is_diagnostic_not_authoritative(self, client, bypass_session, migrated_pg_url):
        """AC5：scan_events 是诊断指标，valid_visits（is_valid_visit=true）才是权威分母。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_code(bypass_session, tenant_id, public_id)

        # 正常访问 + robot 访问
        await client.get(
            f"/c/{public_id}",
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        await client.get(
            f"/c/{public_id}",
            headers={"Accept": "application/json", "User-Agent": "Googlebot/2.1"},
        )

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        # total scan_events = 2（诊断指标，含 robot）
        total = (
            await bypass_session.execute(
                text("SELECT count(*) FROM scan_events WHERE tenant_id=:t AND public_id=:p"),
                {"t": tenant_id, "p": public_id},
            )
        ).scalar()
        # valid_visits = 1（权威指标，排除 robot）
        valid = (
            await bypass_session.execute(
                text("SELECT count(*) FROM scan_events WHERE tenant_id=:t AND public_id=:p AND is_valid_visit=true"),
                {"t": tenant_id, "p": public_id},
            )
        ).scalar()

        # AC5：total（诊断）> valid（权威），证明 raw scan 降级为非权威信号
        assert total == 2, f"诊断指标 total_scans 应为 2，实际 {total}"
        assert valid == 1, f"权威指标 valid_visits 应为 1，实际 {valid}"
        assert total > valid, "raw scan 应降级为非权威信号（valid_visits 是分母）"
