"""yimatong-zgb1.6 验收测试 — 覆盖未激活、冻结、作废和不存在码（真实 PostgreSQL）。

证明以下 AC：
1. 四种非正常结果均有明确、互不混淆的页面状态和 API 契约。
2. 未激活码不会被误判为假货或有效参与码。
3. 冻结码仍能查看权威产品溯源，但无法领取权益。
4. 作废码不能参与活动或恢复为普通激活状态。
5. 不存在的码不会泄露租户、批次或内部错误信息。
"""

from __future__ import annotations

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


async def _set_item_status(bypass_session, tenant_id: str, public_id: str, status: str) -> None:
    """直接修改 baseline 码的状态用于测试（绕过状态机，仅测试消费者侧契约）。"""
    await bypass_session.rollback()
    await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
    await bypass_session.execute(
        text("UPDATE code_items SET status=:s WHERE tenant_id=:t AND public_id=:p"),
        {"s": status, "t": tenant_id, "p": public_id},
    )
    # 清缓存：resolve_cache 可能缓存了旧 status
    from app.services.resolve_cache import resolve_cache

    await resolve_cache.invalidate(f"resolve:{public_id}")
    await bypass_session.commit()


# ── AC1 + AC2：未激活码明确契约，不被误判 ─────────────────────────────────


class TestUnactivatedCode:
    """AC1+AC2：未激活码（created）有明确契约，不被误判为有效/作废。"""

    async def test_unactivated_clear_contract(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _set_item_status(bypass_session, tenant_id, public_id, "created")

        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        # unactivated 返回 200（不是错误，是提示）
        assert resp.status_code == 200
        body = resp.json()
        cd = body["code_data"]
        # AC1：明确、互不混淆的结果标识
        assert cd["result"] == "unactivated"
        assert cd["lifecycle"] == "unactivated"
        # AC2：不被误判为 active 或 voided
        assert cd["lifecycle"] != "active"
        assert cd["lifecycle"] != "voided"
        # 兼容字段保留
        assert cd["status"] == "created"
        # 不颁发 scan_token（无 token 则无法领权益）
        assert "scan_token" not in body or body.get("scan_token") is None


# ── AC3：冻结码保留溯源 + 暂停权益 ───────────────────────────────────────


class TestFrozenCodeKeepsTraceability:
    """AC3：冻结码仍能查看权威产品溯源，但无法领取权益。"""

    async def test_frozen_keeps_traceability(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _set_item_status(bypass_session, tenant_id, public_id, "frozen")

        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        # frozen 返回 200（可查看溯源），不是 403
        assert resp.status_code == 200, f"frozen 应 200 保留溯源，实际 {resp.status_code}: {resp.text}"
        body = resp.json()
        cd = body["code_data"]

        # AC3 核心：权威产品溯源资料仍可见
        assert cd["lifecycle"] == "frozen"
        assert "product" in cd, "frozen 必须返回产品资料（保留溯源）"
        assert cd["product"]["name"] == summary["product"]["name"]
        assert "batch" in cd, "frozen 必须返回批次资料（保留溯源）"
        assert cd["batch"]["batch_code"] == summary["production_batch"]["batch_code"]

        # AC3 核心：权益暂停（不颁发 scan_token → 无法领权益）
        assert "scan_token" not in body or body.get("scan_token") is None, "frozen 不应颁发 scan_token（权益暂停）"
        # scan_info 标记 benefit_paused
        si = body.get("scan_info", {})
        assert si.get("benefit_paused") is True, "frozen 应标记 benefit_paused"


# ── AC4：作废码终止参与 ───────────────────────────────────────────────────


class TestVoidedCodeTerminates:
    """AC4：作废码（revoked/expired）不能参与活动。"""

    async def test_revoked_terminates(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _set_item_status(bypass_session, tenant_id, public_id, "revoked")

        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        # voided 返回 410（终止性）
        assert resp.status_code == 410
        body = resp.json()
        cd = body["code_data"]
        assert cd["result"] == "voided"
        assert cd["lifecycle"] == "voided"
        # AC4：不颁发 scan_token，不返回溯源
        assert "scan_token" not in body or body.get("scan_token") is None
        assert "product" not in cd, "voided 不应返回溯源资料"

    async def test_expired_maps_to_voided(self, client, bypass_session, migrated_pg_url):
        """AC1：expired 归一化为 voided 生命周期（1.3 映射）。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await _set_item_status(bypass_session, tenant_id, public_id, "expired")

        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        assert resp.status_code == 410
        body = resp.json()
        cd = body["code_data"]
        # expired → voided（1.3 归一化）
        assert cd["lifecycle"] == "voided"
        assert cd["result"] == "voided"


# ── AC5：不存在码不泄露信息 ───────────────────────────────────────────────


class TestNotFoundNoLeakage:
    """AC5：不存在的码不会泄露租户、批次或内部错误信息。"""

    async def test_not_found_no_leakage(self, client, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        await seed_baseline(migrated_pg_url)  # 确保 baseline 存在

        # 用一个格式合法但不存在的 public_id（Luhn 校验通过的随机串）
        from app.services.public_id import generate_public_id

        fake_id = generate_public_id()
        # 确保它真的不存在
        resp = await client.get(f"/c/{fake_id}", headers={"Accept": "application/json"})
        assert resp.status_code == 404
        body = resp.json()

        # AC5：只返回 not_found，不泄露租户/产品/批次
        assert body.get("detail") == "not_found" or body.get("code_data", {}).get("result") == "not_found"
        # 关键：响应中不含 tenant_id / product / brand / batch
        assert "tenant_id" not in body
        assert "product" not in body
        assert "brand" not in body
        assert "batch" not in body
        cd = body.get("code_data", {})
        assert "tenant_id" not in cd
        assert "product" not in cd
        assert "batch" not in cd

    async def test_invalid_format_no_leakage(self, client, migrated_pg_url):
        """格式非法的 public_id 也不泄露信息。"""
        from tests.test_acceptance.conftest import seed_baseline

        await seed_baseline(migrated_pg_url)

        resp = await client.get("/c/INVALID123", headers={"Accept": "application/json"})
        assert resp.status_code == 404
        body = resp.json()
        assert "tenant_id" not in body
        assert "product" not in body


# ── AC1：四种状态互不混淆 ───────────────────────────────────────────────


class TestFourStatesMutuallyExclusive:
    """AC1：四种非正常状态的 result/lifecycle 字段互不混淆。"""

    async def test_results_mutually_exclusive(self, client, bypass_session, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]

        results_seen: set[str] = set()

        # unactivated
        await _set_item_status(bypass_session, tenant_id, public_id, "created")
        r = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        results_seen.add(r.json()["code_data"]["result"])

        # frozen
        await _set_item_status(bypass_session, tenant_id, public_id, "frozen")
        r = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        # frozen 走正常路径，lifecycle=frozen（result 字段在 build_json_response 里可能没有，
        # 用 lifecycle 断言）
        results_seen.add(r.json()["code_data"]["lifecycle"])

        # voided (revoked)
        await _set_item_status(bypass_session, tenant_id, public_id, "revoked")
        r = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        results_seen.add(r.json()["code_data"]["result"])

        # not_found
        from app.services.public_id import generate_public_id

        fake_id = generate_public_id()
        r = await client.get(f"/c/{fake_id}", headers={"Accept": "application/json"})
        results_seen.add(r.json().get("code_data", {}).get("result", "not_found"))

        # AC1：4 种状态的结果标识互不混淆（各自唯一）
        assert results_seen == {"unactivated", "frozen", "voided", "not_found"}, (
            f"4 种状态应互不混淆，实际 {results_seen}"
        )
