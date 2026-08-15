"""yimatong-zgb1.8 验收测试 — 让有权限的运营人员冻结、解冻和作废码（真实 PostgreSQL）。

证明以下 AC：
1. 只有具备相应权限的账号可以执行状态操作。
2. 冻结与解冻遵守状态机，作废被视为受保护的不可逆动作。
3. 操作人、原因、前后状态和时间均可审计。
4. 并发或重复请求不会产生非法中间状态。
5. 状态改变后 H5、API 和权益资格立即表现一致。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.database import get_db, get_db_with_bypass, set_session_tenant_context
from app.main import app

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


@pytest.fixture
async def client(migrated_pg_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncClient, None]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import database
    from app.middleware.rate_limit import rate_limiter

    rate_limiter._cache._mem_store.clear()  # type: ignore[attr-defined]

    engine = create_async_engine(migrated_pg_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", session_factory)
    monkeypatch.setattr(database, "control_session_factory", session_factory)
    async with database.async_session_factory() as runtime_probe, database.control_session_factory() as control_probe:
        runtime_database = await runtime_probe.scalar(text("SELECT current_database()"))
        control_database = await control_probe.scalar(text("SELECT current_database()"))
        assert runtime_database == control_database == engine.url.database

    async def override_get_db(request: Request):
        async with session_factory() as session:
            request_tenant_id = getattr(request.state, "tenant_id", None)
            if request_tenant_id:
                request_tenant_id = str(request_tenant_id)
                await set_session_tenant_context(session, request_tenant_id)
                bound_tenant_id = await session.scalar(text("SELECT public.current_tenant_id()::text"))
                assert bound_tenant_id == request_tenant_id
            yield session
            await session.commit()

    async def override_get_bypass():
        async with session_factory() as session:
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


async def _reset_baseline_items(bypass_session, tenant_id: str, batch_id: str) -> None:
    """重置 baseline 批所有码为 activated（撤销 void/freeze 副作用）。"""
    await bypass_session.rollback()
    await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
    await bypass_session.execute(
        text("UPDATE code_items SET status='activated', revoked_at=NULL WHERE tenant_id=:t AND code_batch_id=:b"),
        {"t": tenant_id, "b": batch_id},
    )
    await bypass_session.commit()


async def _login_baseline_admin(client, summary) -> str:
    """登录 baseline admin，返回 access_token。"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": summary["baseline_tenant"]["admin_email"],
            "password": summary["baseline_tenant"]["admin_password"],
            "tenant_slug": summary["baseline_tenant"]["slug"],
        },
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


async def _enable_risk_module(bypass_session, tenant_id: str) -> None:
    """Grant the baseline tenant the paid risk feature required by freeze routes."""
    await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
    await bypass_session.execute(
        text(
            """
            UPDATE tenants
            SET enabled_features = (
                COALESCE(enabled_features::jsonb, '{}'::jsonb)
                || '{"risk_module": true}'::jsonb
            )::json
            WHERE id = :tenant_id
            """
        ),
        {"tenant_id": tenant_id},
    )
    await bypass_session.commit()


# ── AC1：权限检查 ─────────────────────────────────────────────────────────


class TestPermissionRequired:
    """AC1：只有具备相应权限的账号可以执行状态操作。"""

    async def test_freeze_requires_permission(self, client, bypass_session, migrated_pg_url):
        """AC1：无认证（无 token）调用 freeze 应 401。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        # 查一个 code_item_id
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        item_id = (
            await bypass_session.execute(
                text("SELECT id FROM code_items WHERE tenant_id=:t LIMIT 1"),
                {"t": summary["baseline_tenant"]["id"]},
            )
        ).scalar()
        await bypass_session.rollback()

        # 无 token 调用 freeze
        resp = await client.post(f"/api/v1/risk-alerts/code-items/{item_id}/freeze")
        # middleware 拦截无 token 请求
        assert resp.status_code in (401, 403), f"无权限调用应被拒，实际 {resp.status_code}"

    async def test_freeze_with_admin_permission_succeeds(self, client, bypass_session, migrated_pg_url):
        """AC1：有 code:manage 权限的 admin 可以冻结。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]
        batch_id = summary["code_batch"]["id"]
        await _reset_baseline_items(bypass_session, tenant_id, batch_id)
        await _enable_risk_module(bypass_session, tenant_id)
        token = await _login_baseline_admin(client, summary)

        # 查一个 activated code_item_id
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        item_id = (
            await bypass_session.execute(
                text("SELECT id FROM code_items WHERE tenant_id=:t AND status='activated' LIMIT 1"),
                {"t": tenant_id},
            )
        ).scalar()
        await bypass_session.rollback()

        # admin 调用 freeze
        resp = await client.post(
            f"/api/v1/risk-alerts/code-items/{item_id}/freeze",
            json={"reason": "operator acceptance freeze", "confirm": "freeze"},
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            assert resp.status_code == 200, f"admin freeze 应成功，实际 {resp.status_code}: {resp.text}"
            assert resp.json()["status"] == "frozen"
        finally:
            if resp.status_code == 200:
                recovered = await client.post(
                    f"/api/v1/risk-alerts/code-items/{item_id}/unfreeze",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert recovered.status_code == 200


# ── AC2：作废受保护（reason + 二次确认）────────────────────────────────────


class TestVoidProtected:
    """AC2：作废是受保护的不可逆动作，必须 reason + 二次确认。"""

    async def test_void_requires_reason(self, client, bypass_session, migrated_pg_url):
        """AC2：作废必须提供 reason。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]
        batch_id = summary["code_batch"]["id"]
        await _reset_baseline_items(bypass_session, tenant_id, batch_id)
        token = await _login_baseline_admin(client, summary)

        # 缺 reason
        resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/void?confirm=void",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, f"缺 reason 应 422，实际 {resp.status_code}"

    async def test_void_requires_confirm(self, client, bypass_session, migrated_pg_url):
        """AC2：作废必须 confirm=void 二次确认。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]
        batch_id = summary["code_batch"]["id"]
        await _reset_baseline_items(bypass_session, tenant_id, batch_id)
        token = await _login_baseline_admin(client, summary)

        # 有 reason 但缺 confirm
        resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/void?reason=作废测试",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, f"缺 confirm 应 422，实际 {resp.status_code}"

    async def test_void_with_reason_and_confirm_succeeds(self, client, bypass_session, migrated_pg_url):
        """AC2：reason + confirm=void 的作废成功，且不可逆。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]
        batch_id = summary["code_batch"]["id"]
        await _reset_baseline_items(bypass_session, tenant_id, batch_id)
        token = await _login_baseline_admin(client, summary)

        # 正确作废
        resp = await client.post(
            f"/api/v1/code-batches/{batch_id}/void?reason=batch-recall&confirm=void",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, f"作废应成功，实际 {resp.status_code}: {resp.text}"
        assert resp.json()["voided"] > 0

        # AC2：不可逆——再次作废幂等（已作废的跳过，voided=0）
        resp2 = await client.post(
            f"/api/v1/code-batches/{batch_id}/void?reason=second-attempt&confirm=void",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["voided"] == 0, "已作废的批再次作废应幂等（voided=0）"


# ── AC3：审计留痕（操作人、原因、前后状态、时间）─────────────────────────


class TestAuditTrail:
    """AC3：操作人、原因、前后状态和时间均可审计。"""

    async def test_void_writes_audit_with_reason(self, client, bypass_session, migrated_pg_url):
        """AC3：作废后 platform_audit_log 有对应行，含 reason。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]
        batch_id = summary["code_batch"]["id"]
        await _reset_baseline_items(bypass_session, tenant_id, batch_id)
        token = await _login_baseline_admin(client, summary)

        # 作废前 audit count（基线）
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await bypass_session.execute(
            text("SELECT count(*) FROM platform_audit_log WHERE target_tenant_id=:t AND action='code_void'"),
            {"t": tenant_id},
        )
        await bypass_session.rollback()

        # 作废
        await client.post(
            f"/api/v1/code-batches/{batch_id}/void?reason=audit-test-reason&confirm=void",
            headers={"Authorization": f"Bearer {token}"},
        )

        # 作废后 audit
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        after_rows = (
            await bypass_session.execute(
                text(
                    "SELECT operator_id, action, resource FROM platform_audit_log "
                    "WHERE target_tenant_id=:t AND action='code_void' "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"t": tenant_id},
            )
        ).one()
        assert after_rows[1] == "code_void"
        # resource 包含 batch_id + reason
        assert f"code_batch:{batch_id}" in after_rows[2]
        assert "audit-test-reason" in after_rows[2], f"审计应含 reason，实际 {after_rows[2]}"
        # operator_id 非空（操作人记录）
        assert after_rows[0], "审计必须有 operator_id（操作人）"


# ── AC4：并发/重复请求不产生非法中间状态 ───────────────────────────────────


class TestConcurrentSafety:
    """AC4：并发或重复请求不会产生非法中间状态。"""

    async def test_repeated_freeze_idempotent(self, client, bypass_session, migrated_pg_url):
        """AC4：重复冻结同一码不产生非法中间状态（已 frozen 的冻结幂等或 409，但不崩）。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]
        batch_id = summary["code_batch"]["id"]
        await _reset_baseline_items(bypass_session, tenant_id, batch_id)
        await _enable_risk_module(bypass_session, tenant_id)
        token = await _login_baseline_admin(client, summary)

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        item_id = (
            await bypass_session.execute(
                text("SELECT id FROM code_items WHERE tenant_id=:t AND status='activated' LIMIT 1"),
                {"t": tenant_id},
            )
        ).scalar()
        await bypass_session.rollback()

        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": f"operator-freeze-{item_id}"}
        # 第一次冻结
        r1 = await client.post(
            f"/api/v1/risk-alerts/code-items/{item_id}/freeze",
            json={"reason": "repeat safety", "confirm": "freeze"},
            headers=headers,
        )
        try:
            assert r1.status_code == 200
            assert r1.json()["status"] == "frozen"

            # 稳定幂等键重试重放同一权威结果，不新增事实。
            r2 = await client.post(
                f"/api/v1/risk-alerts/code-items/{item_id}/freeze",
                json={"reason": "repeat safety", "confirm": "freeze"},
                headers=headers,
            )
            assert r2.status_code == 200
            assert r2.json() == r1.json()

            # 没有稳定幂等键是一次新命令，已冻结状态必须拒绝。
            r3 = await client.post(
                f"/api/v1/risk-alerts/code-items/{item_id}/freeze",
                json={"reason": "repeat safety", "confirm": "freeze"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r3.status_code == 409, f"无幂等键的重复冻结应 409，实际 {r3.status_code}"

            # DB 状态仍是 frozen（合法）
            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            final_status = (
                await bypass_session.execute(
                    text("SELECT status FROM code_items WHERE id=:i"),
                    {"i": item_id},
                )
            ).scalar()
            await bypass_session.rollback()
            assert final_status == "frozen", f"重复冻结后状态应仍为 frozen，实际 {final_status}"
        finally:
            if r1.status_code == 200:
                recovered = await client.post(
                    f"/api/v1/risk-alerts/code-items/{item_id}/unfreeze",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert recovered.status_code == 200


# ── AC5：状态改变后 H5/API/权益一致 ───────────────────────────────────────


class TestStateChangeConsistency:
    """AC5：状态改变后 H5、API 和权益资格立即表现一致。"""

    async def test_freeze_immediately_affects_resolve_and_claim(self, client, bypass_session, migrated_pg_url):
        """AC5：冻结后 resolve 立即表现 frozen（保留溯源 + 无 scan_token），权益暂停。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]
        batch_id = summary["code_batch"]["id"]
        public_id = summary["first_public_id"]
        await _reset_baseline_items(bypass_session, tenant_id, batch_id)
        await _enable_risk_module(bypass_session, tenant_id)
        token = await _login_baseline_admin(client, summary)

        # 冻结前 resolve：active + 溯源；无 live launch 时不颁发 scan_token。
        r_before = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        assert r_before.status_code == 200
        assert r_before.json()["code_data"]["lifecycle"] == "active"
        assert "product" in r_before.json()["code_data"]

        # 清缓存（冻结操作会清，但测试保险起见也清）
        from app.services.resolve_cache import resolve_cache

        await resolve_cache.invalidate(f"resolve:{public_id}")

        # 找到该 public_id 的 item_id 并冻结
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        item_id = (
            await bypass_session.execute(
                text("SELECT id FROM code_items WHERE tenant_id=:t AND public_id=:p"),
                {"t": tenant_id, "p": public_id},
            )
        ).scalar()
        await bypass_session.rollback()

        freeze_resp = await client.post(
            f"/api/v1/risk-alerts/code-items/{item_id}/freeze",
            json={"reason": "resolver consistency", "confirm": "freeze"},
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            assert freeze_resp.status_code == 200

            # 冻结后再 resolve（清缓存确保看到新状态）
            await resolve_cache.invalidate(f"resolve:{public_id}")
            r_after = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
            # AC5：立即表现 frozen
            assert r_after.json()["code_data"]["lifecycle"] == "frozen", "冻结后 lifecycle 应立即变 frozen"
            # AC5：权益暂停（无 scan_token）
            assert not r_after.json().get("scan_token"), "冻结后不应颁发 scan_token（权益暂停）"
            # AC5：溯源仍可见（1.6 AC3）
            assert "product" in r_after.json()["code_data"], "冻结后溯源应仍可见"
            assert r_after.json()["scan_info"]["benefit_paused"] is True
            assert r_after.json()["scan_info"]["paused_reason"] == "frozen"
        finally:
            if freeze_resp.status_code == 200:
                recovered = await client.post(
                    f"/api/v1/risk-alerts/code-items/{item_id}/unfreeze",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert recovered.status_code == 200
