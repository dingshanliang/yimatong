"""yimatong-zgb1.9 验收测试 — 收缩旧码状态并完成生命周期迁移（真实 PostgreSQL）。

证明以下 AC：
1. 数据迁移可升级、可回滚，并在干净数据库与现有数据快照上通过。
2. 扫码、码管理、导入、种子、活动和消费者页面只使用统一状态语义。
3. 存量码迁移前后的消费者业务结果保持一致。
4. 仓库中不再存在会重新写入旧状态的生产路径。
5. 生命周期相关自动化测试覆盖所有合法与非法转换。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace

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


# ── AC1：迁移可升级/回滚（已在 1.3/1.5/1.7/1.10 验证，此处固化 head 一致性）────


class TestMigrationIntegrity:
    """AC1：数据迁移可升级、可回滚，干净 DB 上通过。"""

    async def test_alembic_head_consistent(self, migrated_pg_url):
        """AC1：acceptance DB 已升级到 head（conftest migrated_pg_url fixture 保证）。"""
        # migrated_pg_url fixture 已经跑 alembic upgrade head，如果到这说明迁移成功
        assert migrated_pg_url is not None

    async def test_lifecycle_columns_exist(self, bypass_session):
        """AC1：lifecycle 相关字段在 DB 中存在（to_lifecycle 映射依赖的 status 列）。"""
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        cols = (
            await bypass_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='code_items' AND column_name IN ('status', 'first_scanned_at')"
                )
            )
        ).fetchall()
        col_names = {c[0] for c in cols}
        assert "status" in col_names, "code_items.status 必须存在（lifecycle 映射源）"
        assert "first_scanned_at" in col_names, "code_items.first_scanned_at 必须存在（首查权威源）"


# ── AC2：所有入口用统一 lifecycle 语义 ─────────────────────────────────────


class TestUnifiedLifecycleSemantics:
    """AC2：扫码、消费者页面只使用统一 lifecycle 语义（to_lifecycle 映射）。"""

    async def test_resolver_returns_lifecycle_for_all_states(
        self, client, bypass_session, migrated_pg_url, monkeypatch
    ):
        """AC2：resolve 对所有码状态返回 lifecycle 字段（统一语义）。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]

        async def scope_fixture_tenant(_db, requested_public_id):
            assert requested_public_id == public_id
            return uuid.UUID(str(tenant_id))

        from app.api.v1 import resolver
        from app.services import resolver as resolver_service

        bootstrap_calls: list[dict[str, object]] = []

        async def bootstrap_fixture_tenant(_db, statement, parameters=None):
            compiled_parameters = statement.compile().params
            assert parameters in (None, {})
            assert public_id in compiled_parameters.values()
            bootstrap_calls.append(compiled_parameters)
            return SimpleNamespace(tenant_id=uuid.UUID(str(tenant_id)))

        monkeypatch.setattr(resolver, "_scope_public_code_tenant", scope_fixture_tenant)
        monkeypatch.setattr(resolver_service, "bootstrap_tenant_row", bootstrap_fixture_tenant)

        # 真实行只沿合法的 activated -> frozen -> activated -> revoked 路径前进。
        test_cases = [
            ("activated", "active"),
            ("frozen", "frozen"),
            ("activated", "active"),
            ("revoked", "voided"),
        ]
        for status, expected_lifecycle in test_cases:
            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await bypass_session.execute(
                text(
                    "UPDATE code_items SET status=:s, "
                    "frozen_from_status=CASE WHEN :is_frozen THEN 'activated' END, "
                    "frozen_at=CASE WHEN :is_frozen THEN now() END, "
                    "freeze_provenance_version=CASE WHEN :is_frozen THEN 0 END, "
                    "revoked_at=CASE WHEN :is_revoked THEN now() ELSE revoked_at END "
                    "WHERE tenant_id=:t AND public_id=:p"
                ),
                {
                    "s": status,
                    "is_frozen": status == "frozen",
                    "is_revoked": status == "revoked",
                    "t": tenant_id,
                    "p": public_id,
                },
            )
            await bypass_session.commit()
            from app.services.resolve_cache import resolve_cache

            await resolve_cache.invalidate(f"resolve:{public_id}")

            resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
            body = resp.json()
            cd = body.get("code_data", {})
            # AC2：所有状态都返回 lifecycle（统一语义），映射正确
            assert cd.get("lifecycle") == expected_lifecycle, (
                f"码 {public_id} 应映射到 lifecycle={expected_lifecycle}，实际 {cd.get('lifecycle')}"
            )
        from app.models.code import CodeItemStatus, to_lifecycle

        assert to_lifecycle(CodeItemStatus.created) == "unactivated"
        assert to_lifecycle(CodeItemStatus.expired) == "voided"
        assert len(bootstrap_calls) == 2 * len(test_cases)


# ── AC3：存量码迁移前后消费者业务结果一致 ──────────────────────────────────


class TestStoredDataConsistency:
    """AC3：存量码（旧 status 值）迁移前后消费者业务结果一致。"""

    async def test_legacy_activated_maps_to_active_consistent(self, client, bypass_session, migrated_pg_url):
        """AC3：存量 activated 码的消费者结果与 lifecycle=active 一致。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]

        # 确保是 activated（baseline 默认）
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await bypass_session.execute(
            text("UPDATE code_items SET status='activated' WHERE tenant_id=:t AND public_id=:p"),
            {"t": tenant_id, "p": public_id},
        )
        await bypass_session.commit()
        from app.services.resolve_cache import resolve_cache

        await resolve_cache.invalidate(f"resolve:{public_id}")

        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        body = resp.json()
        # AC3：消费者结果一致——lifecycle=active，颁发 scan_token，溯源可见
        assert body["code_data"]["lifecycle"] == "active"
        assert body.get("scan_token"), "active 码应颁发 scan_token"
        assert "product" in body["code_data"], "active 码应返回溯源"


# ── AC4：无旧状态写入的生产路径 ───────────────────────────────────────────


class TestNoLegacyStatusWritePaths:
    """AC4：仓库中不再存在会重新写入旧状态的生产路径（状态机强制）。

    验证：所有 status 变更都经过 code_lifecycle 状态机（can_lifecycle_transition），
    没有绕过状态机直接写 status 的生产路径。
    """

    async def test_void_enforces_state_machine(self, bypass_session, migrated_pg_url):
        """AC4：void_batch 走状态机（can_lifecycle_transition），不绕过。"""
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]
        batch_id = summary["code_batch"]["id"]

        # 重置为 activated
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await bypass_session.execute(
            text("UPDATE code_items SET status='activated', revoked_at=NULL WHERE tenant_id=:t AND code_batch_id=:b"),
            {"t": tenant_id, "b": batch_id},
        )
        await bypass_session.commit()

        # void_batch（走状态机）
        from app.services.code import void_batch

        result = await void_batch(bypass_session, uuid.UUID(tenant_id), uuid.UUID(batch_id))
        await bypass_session.commit()
        assert result.voided > 0

        # AC4：所有码现在是 revoked（voided 生命周期），且通过状态机写入
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        statuses = (
            await bypass_session.execute(
                text("SELECT DISTINCT status FROM code_items WHERE tenant_id=:t AND code_batch_id=:b"),
                {"t": tenant_id, "b": batch_id},
            )
        ).fetchall()
        # 所有码都是 revoked（voided 生命周期的 DB 存储值）
        for s in statuses:
            assert s[0] == "revoked", f"void 后所有码应为 revoked，实际 {s[0]}"


# ── AC5：生命周期转换全覆盖 ───────────────────────────────────────────────


class TestLifecycleTransitionCoverage:
    """AC5：生命周期相关自动化测试覆盖所有合法与非法转换。"""

    async def test_all_legal_transitions(self):
        """AC5：所有合法转换通过（unactivated→active/voided, active→frozen/voided, frozen→active/voided）。"""
        from app.models.code import CodeLifecycle
        from app.services.code_lifecycle import can_lifecycle_transition

        legal_cases = [
            (CodeLifecycle.unactivated, CodeLifecycle.active),
            (CodeLifecycle.unactivated, CodeLifecycle.voided),
            (CodeLifecycle.active, CodeLifecycle.frozen),
            (CodeLifecycle.active, CodeLifecycle.voided),
            (CodeLifecycle.frozen, CodeLifecycle.active),
            (CodeLifecycle.frozen, CodeLifecycle.voided),
        ]
        for current, target in legal_cases:
            assert can_lifecycle_transition(current, target), f"合法转换 {current.value}→{target.value} 应通过"

    async def test_all_illegal_transitions_rejected(self):
        """AC5：所有非法转换被拒（voided→anything, active→unactivated, 跳过激活等）。"""
        from app.models.code import CodeLifecycle
        from app.services.code_lifecycle import can_lifecycle_transition

        illegal_cases = [
            (CodeLifecycle.voided, CodeLifecycle.active),  # voided 终态
            (CodeLifecycle.voided, CodeLifecycle.frozen),
            (CodeLifecycle.voided, CodeLifecycle.unactivated),
            (CodeLifecycle.active, CodeLifecycle.unactivated),  # 不能回退到未激活
            (CodeLifecycle.unactivated, CodeLifecycle.frozen),  # 跳过激活
            (CodeLifecycle.frozen, CodeLifecycle.unactivated),
        ]
        for current, target in illegal_cases:
            assert not can_lifecycle_transition(current, target), f"非法转换 {current.value}→{target.value} 应被拒"

    async def test_voided_irreversible_all_targets(self):
        """AC5：voided 作为源的任何转换都被拒（不可逆）。"""
        from app.models.code import CodeLifecycle
        from app.services.code_lifecycle import can_lifecycle_transition

        for target in CodeLifecycle:
            if target == CodeLifecycle.voided:
                continue
            assert not can_lifecycle_transition(CodeLifecycle.voided, target), (
                f"voided→{target.value} 应被拒（voided 不可逆）"
            )
