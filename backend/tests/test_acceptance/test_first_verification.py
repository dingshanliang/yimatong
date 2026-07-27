"""yimatong-zgb1.4 验收测试 — 首次查验与轻防伪结果（真实 PostgreSQL）。

证明以下 AC 在真实 PG + 真实 API 路径下成立：
1. 首次有效查验只生成一条权威扫码事实并标记为首次查验。
2. 页面（JSON 契约）明确说明码状态、查验时间、产品批次及可信依据。
3. 刷新、预取或前端重试不会把一次访问重复统计为两次首查。
4. 异常请求和跨租户请求不能改变该码的首次查验事实。
5. H5/API/DB 证据一致。

与 test_baseline_rebuild.TestScanRecordingEvidence 的关系：
- 后者验证 `record_scan_event` 直调的原子性（service 层），不经过 HTTP。
- 本文件验证完整 HTTP 路径：GET /c/{id} 的 scan_info 契约 + telemetry 不再双计 +
  跨租户防御 + 与 DB first_scanned_at 一致性。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.database import get_db, get_db_with_bypass
from app.main import app

# 验收测试：需要真实 infra PG；默认不在普通 pytest 运行中执行
pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


@pytest.fixture
async def client(migrated_pg_url: str) -> AsyncGenerator[AsyncClient, None]:
    """ASGI in-process client，依赖指向 acceptance PG。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # 清空 rate_limiter 的内存缓存，避免跨测试/跨文件累积触发限流（code_limit=10/60s）。
    # rate_limiter 用 AsyncRedisCache 内存降级，state 在 module 生命周期内持久。
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


async def _reset_first_scan(bypass_session, tenant_id: str, public_id: str) -> None:
    """清空指定码的 first_scanned_at、状态副作用，并删除已有 scan_events，隔离每个测试。

    acceptance DB 在同会话内被多个测试文件共享且 seed_baseline 幂等（同一批 UUID），
    因此：
    - 其他文件（test_code_lifecycle）可能 void/freeze 了 baseline 码 → status 变成非 activated；
    - 其他文件可能扫过该码 → first_scanned_at 非空、scan_events 累积。
    本函数把 baseline 该码重置为 activated + first_scanned_at=NULL + 清空所有 scan_events。
    """
    await bypass_session.rollback()
    await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
    # 重置状态为 activated（撤销 void/freeze 副作用），清空首查时间
    await bypass_session.execute(
        text(
            "UPDATE code_items SET status='activated', revoked_at=NULL, first_scanned_at=NULL "
            "WHERE tenant_id=:t AND public_id=:p"
        ),
        {"t": tenant_id, "p": public_id},
    )
    # 删除所有租户对该 public_id 的 scan_events（彻底隔离，含跨租户测试写入的行）
    await bypass_session.execute(
        text("DELETE FROM scan_events WHERE public_id=:p"),
        {"p": public_id},
    )
    await bypass_session.commit()


# ── AC1：首次查验只生成一条权威事实 ───────────────────────────────────────


class TestFirstVerificationSingleAuthoritativeFact:
    """AC1：首次有效查验只生成一条权威扫码事实并标记为首次查验。"""

    async def test_first_resolve_writes_exactly_one_fact(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_first_scan(bypass_session, tenant_id, public_id)

        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        assert resp.status_code == 200
        body = resp.json()

        # scan_info 契约（AC1 + AC2）
        assert body["scan_info"]["is_first_scan"] is True

        # DB 侧：恰好 1 条 scan_events（resolver 唯一写入，无 telemetry 双计）
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        rows = await bypass_session.execute(
            text("SELECT count(*) FROM scan_events WHERE tenant_id=:t AND public_id=:p"),
            {"t": tenant_id, "p": public_id},
        )
        assert rows.scalar() == 1, "首次 resolve 应只产生 1 条 scan_events 事实"

        # DB 侧：code_items.first_scanned_at 已被原子写入（非空）
        fsa = await bypass_session.execute(
            text("SELECT first_scanned_at FROM code_items WHERE tenant_id=:t AND public_id=:p"),
            {"t": tenant_id, "p": public_id},
        )
        assert fsa.scalar() is not None, "首次查验必须写入 code_items.first_scanned_at"


# ── AC1 + AC2：首查时间持久化且响应一致 ───────────────────────────────────


class TestFirstVerificationTimePersisted:
    """AC1/AC2：scan_info.first_scan_time 与 DB first_scanned_at 一致。"""

    async def test_response_first_scan_time_matches_db(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_first_scan(bypass_session, tenant_id, public_id)

        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        body = resp.json()
        first_scan_time = body["scan_info"]["first_scan_time"]
        assert first_scan_time, "首次查验响应必须包含 first_scan_time"

        # 解析为 datetime 用于比较（ISO8601）
        resp_time = datetime.fromisoformat(first_scan_time)

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        db_fsa = await bypass_session.execute(
            text("SELECT first_scanned_at FROM code_items WHERE tenant_id=:t AND public_id=:p"),
            {"t": tenant_id, "p": public_id},
        )
        db_time = db_fsa.scalar()
        assert db_time is not None
        # 响应时间来自 DB 同一列，秒级容差
        delta = abs((resp_time - db_time).total_seconds())
        assert delta < 1.0, f"响应首查时间 {resp_time} 与 DB {db_time} 差 {delta}s"


# ── AC2：verification_count 契约（post-insert，首次=1）─────────────────────


class TestVerificationCountPostInsert:
    """AC2：verification_count = 本码累计被查验次数（含本次），首次=1。"""

    async def test_count_increments_on_each_resolve(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_first_scan(bypass_session, tenant_id, public_id)

        headers = {"Accept": "application/json"}

        r1 = await client.get(f"/c/{public_id}", headers=headers)
        assert r1.json()["scan_info"]["verification_count"] == 1, "首次查验 = 1"
        assert r1.json()["scan_info"]["scan_count"] == 1, "scan_count 兼容别名 = 1"
        assert r1.json()["scan_info"]["is_first_scan"] is True

        r2 = await client.get(f"/c/{public_id}", headers=headers)
        assert r2.json()["scan_info"]["verification_count"] == 2
        assert r2.json()["scan_info"]["scan_count"] == 2
        assert r2.json()["scan_info"]["is_first_scan"] is False, "第二次不再为首查"

        r3 = await client.get(f"/c/{public_id}", headers=headers)
        assert r3.json()["scan_info"]["verification_count"] == 3


# ── AC3：刷新不双计首查 ────────────────────────────────────────────────────


class TestRefreshDoesNotDoubleCountFirst:
    """AC3：刷新、预取或前端重试不会把一次访问重复统计为两次首查。"""

    async def test_three_refreshes_one_first_scan(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_first_scan(bypass_session, tenant_id, public_id)

        headers = {"Accept": "application/json"}
        for _ in range(3):
            await client.get(f"/c/{public_id}", headers=headers)

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        first_count = await bypass_session.execute(
            text("SELECT count(*) FROM scan_events WHERE tenant_id=:t AND public_id=:p AND is_first_scan=true"),
            {"t": tenant_id, "p": public_id},
        )
        assert first_count.scalar() == 1, "3 次刷新后，首查事实仍唯一（=1）"

        total = await bypass_session.execute(
            text("SELECT count(*) FROM scan_events WHERE tenant_id=:t AND public_id=:p"),
            {"t": tenant_id, "p": public_id},
        )
        assert total.scalar() == 3, "3 次 resolve 产生 3 条事实（每次一条，无叠加）"

    async def test_telemetry_does_not_create_scan_event(self, client, bypass_session, migrated_pg_url):
        """AC3：POST /scan-events 不再插 ScanEvent（消除 resolver+telemetry 双重记录）。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_first_scan(bypass_session, tenant_id, public_id)

        # 显式固定 IP（X-Real-IP），确保 resolve 与 telemetry 的 ip_hash 一致，
        # 避免 ASGITransport 下 client.host 不稳导致 scan_token 校验失败。
        # 生产 Nginx 同样通过 X-Real-IP 提供稳定 IP。
        fixed_headers = {"X-Real-IP": "203.0.113.7", "Accept": "application/json"}

        resolve_resp = await client.get(f"/c/{public_id}", headers=fixed_headers)
        token = resolve_resp.json()["scan_token"]

        # 模拟 H5 触发 telemetry（此前会插第 2 条）
        tele = await client.post(
            "/scan-events",
            json={"event_type": "view", "public_id": public_id},
            headers={"Authorization": f"Bearer {token}", "X-Real-IP": "203.0.113.7"},
        )
        assert tele.status_code == 201, f"telemetry 应 201，实际 {tele.status_code}: {tele.text}"
        assert tele.json()["status"] == "ok"

        # DB 仍只有 1 条 scan_events（telemetry 不落库）
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        total = await bypass_session.execute(
            text("SELECT count(*) FROM scan_events WHERE tenant_id=:t AND public_id=:p"),
            {"t": tenant_id, "p": public_id},
        )
        assert total.scalar() == 1, "telemetry 端点不应创建额外的 scan_events 行"


# ── AC4：跨租户请求不能改变首查事实 ───────────────────────────────────────


class TestCrossTenantCannotReassignFirstScan:
    """AC4：control tenant 用自己的 tenant_id 调用不能污染 baseline 的首查事实。"""

    async def test_cross_tenant_does_not_reassign(self, bypass_session, migrated_pg_url):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.services.scan_event import record_scan_event
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        baseline_public_id = summary["first_public_id"]
        baseline_tenant = summary["baseline_tenant"]["id"]
        control_tenant = summary["control_tenant"]["id"]
        await _reset_first_scan(bypass_session, baseline_tenant, baseline_public_id)

        engine = create_async_engine(migrated_pg_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        # baseline 自己首查一次（正常）
        async with factory() as db:
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            baseline_event = await record_scan_event(db, uuid.UUID(baseline_tenant), public_id=baseline_public_id)
            await db.commit()
        assert baseline_event.is_first_scan is True

        # 记录 baseline 的首查时间（DB 单一源）
        async with factory() as db:
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            baseline_fsa = (
                await db.execute(
                    text("SELECT first_scanned_at FROM code_items WHERE tenant_id=:t AND public_id=:p"),
                    {"t": baseline_tenant, "p": baseline_public_id},
                )
            ).scalar()
        assert baseline_fsa is not None

        # control tenant 试图用 baseline 的 public_id + 自己的 tenant_id 调用
        # （模拟异常/恶意请求：public_id 在 baseline 租户下，但调用方声明属于 control）
        async with factory() as db:
            control_event = await record_scan_event(db, uuid.UUID(control_tenant), public_id=baseline_public_id)
            await db.commit()
        # control 视角：没有匹配的 CodeItem（public_id 全局唯一但属于 baseline），
        # is_first_scan 走 fallback 路径；关键是 baseline 的 first_scanned_at 不变。
        assert control_event.tenant_id == uuid.UUID(control_tenant)

        # 验证 baseline 的 first_scanned_at 未被 control 调用污染
        async with factory() as db:
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            after_fsa = (
                await db.execute(
                    text("SELECT first_scanned_at FROM code_items WHERE tenant_id=:t AND public_id=:p"),
                    {"t": baseline_tenant, "p": baseline_public_id},
                )
            ).scalar()
        assert after_fsa == baseline_fsa, "control tenant 的跨租户调用不得改写 baseline 的 first_scanned_at"

        await engine.dispose()


# ── AC1：并发首查单赢家（tenant 路径）────────────────────────────────────


class TestConcurrentFirstVerificationSingleWinner:
    """AC1：8 并发 record_scan_event 恰 1 条 is_first_scan=True。"""

    async def test_concurrent_only_one_first(self, bypass_session, migrated_pg_url):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.services.scan_event import record_scan_event
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_first_scan(bypass_session, tenant_id, public_id)

        engine = create_async_engine(migrated_pg_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def _one_scan():
            async with factory() as db:
                await record_scan_event(
                    db, uuid.UUID(tenant_id), public_id=public_id, ip_hash=f"ip-{uuid.uuid4().hex[:8]}"
                )
                await db.commit()

        await asyncio.gather(*[_one_scan() for _ in range(8)])

        async with factory() as db:
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            first_count = (
                await db.execute(
                    text("SELECT count(*) FROM scan_events WHERE tenant_id=:t AND public_id=:p AND is_first_scan=true"),
                    {"t": tenant_id, "p": public_id},
                )
            ).scalar()
            total = (
                await db.execute(
                    text("SELECT count(*) FROM scan_events WHERE tenant_id=:t AND public_id=:p"),
                    {"t": tenant_id, "p": public_id},
                )
            ).scalar()
        await engine.dispose()

        assert first_count == 1, f"8 并发后 is_first_scan=true 应恰为 1，实际 {first_count}"
        assert total == 8, f"8 并发应产生 8 条事实，实际 {total}"


# ── AC2 + AC5：响应契约完整（消费者可见字段 + DB 一致性）─────────────────


class TestResponseContractComplete:
    """AC2/AC5：响应 scan_info 含全部字段；code_data.lifecycle == active；
    DB first_scanned_at 与响应一致。"""

    async def test_full_contract(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _reset_first_scan(bypass_session, tenant_id, public_id)

        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        body = resp.json()

        # scan_info 全字段契约
        si = body["scan_info"]
        assert "is_first_scan" in si
        assert "verification_count" in si
        assert "scan_count" in si  # 兼容别名
        assert "first_scan_time" in si
        assert "verification_time" in si
        assert si["is_first_scan"] is True
        assert si["verification_count"] == 1
        assert si["scan_count"] == 1
        assert si["first_scan_time"] is not None
        assert si["verification_time"] is not None

        # code_data：权威溯源资料（来自 zgb1.2）+ lifecycle（来自 zgb1.3）
        cd = body["code_data"]
        assert cd["lifecycle"] == "active", "激活码生命周期 = active（zgb1.3 映射）"
        assert cd["status"] == "activated", "旧 status 兼容保留"
        assert "product" in cd, "权威产品资料必须呈现"
        assert "batch" in cd, "权威批次资料必须呈现"
        assert "test_reports" in cd
        assert "certificates" in cd
        assert cd["product"]["name"] == summary["product"]["name"]
        assert cd["batch"]["batch_code"] == summary["production_batch"]["batch_code"]

        # DB 一致性：first_scanned_at 与响应 first_scan_time 来自同一列
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        db_fsa = (
            await bypass_session.execute(
                text("SELECT first_scanned_at FROM code_items WHERE tenant_id=:t AND public_id=:p"),
                {"t": tenant_id, "p": public_id},
            )
        ).scalar()
        resp_time = datetime.fromisoformat(si["first_scan_time"])
        delta = abs((resp_time - db_fsa).total_seconds())
        assert delta < 1.0, f"API 响应与 DB first_scanned_at 应秒级一致，差 {delta}s"
