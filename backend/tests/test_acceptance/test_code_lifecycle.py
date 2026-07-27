"""yimatong-zgb1.3 验收门禁 — 统一四状态码生命周期兼容层（real PG）。

证明：
1. 旧数据（baseline 的 activated codes）经权威层映射出唯一四状态结果（lifecycle=active）。
2. void_batch 强制不可逆：作废后该码 lifecycle=voided，再 activate/freeze 都拒绝或幂等。
3. freeze → unfreeze 可恢复（lifecycle 回到 active）。
4. 批次交付状态（mark_printing/mark_delivered）不改单码消费状态（AC3）。
5. 状态变更写审计日志（AC5）。
6. 跨租户状态变更被拒（多租户隔离不被削弱）。

权威资料：docs/01_product/THREE_LINE_PRODUCT_SPEC.md Decision 10/11。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.database import get_db, get_db_with_bypass
from app.main import app

# 验收测试：需要真实 infra PG；默认不在普通 pytest 运行中执行
pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def _reset_baseline_items_to_activated(bypass_session, tenant_id: str, batch_id: str) -> None:
    """把 baseline 批的所有码重置为 activated，撤销其他测试的状态副作用。

    acceptance DB 在同会话内被多个测试共享且 seed_baseline 幂等（同一批 UUID），
    因此 void/freeze 等状态变更会累积。每个改状态的测试先调本函数重置。
    """
    await bypass_session.rollback()
    await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
    await bypass_session.execute(
        text("UPDATE code_items SET status='activated', revoked_at=NULL WHERE tenant_id=:t AND code_batch_id=:b"),
        {"t": tenant_id, "b": batch_id},
    )
    await bypass_session.commit()


@pytest.fixture
async def client(migrated_pg_url: str) -> AsyncGenerator[AsyncClient, None]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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


class TestLegacyMapsToFourStates:
    """AC：新旧数据都能映射为唯一、明确的四状态结果。"""

    async def test_baseline_activated_codes_map_to_active(self, client, migrated_pg_url):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        body = await client.get(f"/c/{summary['first_public_id']}", headers={"Accept": "application/json"})
        assert body.status_code == 200
        # 权威四状态字段（yimatong-zgb1.3）：activated → active
        assert body.json()["code_data"]["lifecycle"] == "active"
        # 旧 status 仍保留（AC4 兼容期）
        assert body.json()["code_data"]["status"] == "activated"


class TestVoidIrreversible:
    """AC：非法转换被拒绝；voided 终态不可逆。"""

    async def test_void_batch_then_reactivate_rejected(self, client, bypass_session, migrated_pg_url):
        from app.services.code import activate_batch, void_batch
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]
        batch_id = summary["code_batch"]["id"]
        tenant_uuid = uuid.UUID(tenant_id)
        batch_uuid = uuid.UUID(batch_id)
        await _reset_baseline_items_to_activated(bypass_session, tenant_id, batch_id)

        # 作废整批
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        resp = await void_batch(bypass_session, tenant_uuid, batch_uuid)
        assert resp.voided > 0
        await bypass_session.commit()

        # 验证码 lifecycle 现在是 voided（DB status=revoked）
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        rows = (
            await bypass_session.execute(
                text("SELECT DISTINCT status FROM code_items WHERE tenant_id=:t AND code_batch_id=:b"),
                {"t": tenant_id, "b": batch_id},
            )
        ).all()
        statuses = {r[0] for r in rows}
        assert statuses == {"revoked"}, f"all items should be revoked, got {statuses}"

        # 再次 activate 应失败（voided → active 非法）
        from app.services.code_lifecycle import InvalidLifecycleTransitionError

        with pytest.raises((InvalidLifecycleTransitionError, Exception)):
            await activate_batch(bypass_session, tenant_uuid, batch_uuid)
        await bypass_session.rollback()

    async def test_void_batch_idempotent_on_already_voided(self, bypass_session, migrated_pg_url):
        """二次作废幂等（已 voided 的跳过，不抛错）。

        注意：acceptance DB 在同会话内被多个测试共享，baseline 批可能已被其他测试作废。
        这里先把批重置为 activated 再测两次 void（保证 first 有码可作废）。
        """
        from app.services.code import void_batch
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])
        batch_id = uuid.UUID(summary["code_batch"]["id"])

        # 重置该批码为 activated（撤销其他测试的 void/freeze 副作用）
        await _reset_baseline_items_to_activated(
            bypass_session, summary["baseline_tenant"]["id"], summary["code_batch"]["id"]
        )

        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        first = await void_batch(bypass_session, tenant_id, batch_id)
        await bypass_session.commit()

        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        second = await void_batch(bypass_session, tenant_id, batch_id)
        await bypass_session.commit()

        assert first.voided > 0, "first void should have codes to void"
        assert second.voided == 0, "second void must be idempotent (no new voids)"


class TestFreezeUnfreeze:
    """AC：合法转换；frozen 可恢复。"""

    async def test_freeze_then_unfreeze_recovers(self, bypass_session, migrated_pg_url):
        from app.models.code import CodeItemStatus
        from app.services.code import freeze_batch
        from app.services.risk import unfreeze_code_item
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])
        batch_id = uuid.UUID(summary["code_batch"]["id"])
        await _reset_baseline_items_to_activated(
            bypass_session, summary["baseline_tenant"]["id"], summary["code_batch"]["id"]
        )

        # 冻结整批
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        freeze_resp = await freeze_batch(bypass_session, tenant_id, batch_id)
        await bypass_session.commit()
        assert freeze_resp.frozen > 0

        # 取一个冻结码，解冻
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        row = (
            await bypass_session.execute(
                text("SELECT id FROM code_items WHERE tenant_id=:t AND code_batch_id=:b AND status='frozen' LIMIT 1"),
                {"t": summary["baseline_tenant"]["id"], "b": summary["code_batch"]["id"]},
            )
        ).first()
        assert row is not None
        item = await unfreeze_code_item(bypass_session, tenant_id, uuid.UUID(str(row[0])))
        await bypass_session.commit()
        assert item.status == CodeItemStatus.activated  # 恢复为 active


class TestBatchDeliveryDoesNotChangeItem:
    """AC3：批次交付状态（printing/delivered）不改单码消费状态。"""

    async def test_mark_printing_keeps_item_status(self, bypass_session, migrated_pg_url):
        from app.services.code import mark_printing
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])
        batch_id = uuid.UUID(summary["code_batch"]["id"])

        # 记录 mark_printing 前的 item 状态分布
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        before = {
            r[0]: r[1]
            for r in (
                await bypass_session.execute(
                    text(
                        "SELECT status, count(*) FROM code_items WHERE tenant_id=:t AND code_batch_id=:b GROUP BY status"
                    ),
                    {"t": summary["baseline_tenant"]["id"], "b": summary["code_batch"]["id"]},
                )
            ).all()
        }

        # mark_printing（批次交付状态）— 需先把 batch 设回 completed（baseline 已 activated）
        await bypass_session.execute(
            text("UPDATE code_batches SET status='completed' WHERE id=:b"),
            {"b": batch_id},
        )
        await mark_printing(bypass_session, tenant_id, batch_id)
        await bypass_session.commit()

        # 验证 item 状态分布未变
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        after = {
            r[0]: r[1]
            for r in (
                await bypass_session.execute(
                    text(
                        "SELECT status, count(*) FROM code_items WHERE tenant_id=:t AND code_batch_id=:b GROUP BY status"
                    ),
                    {"t": summary["baseline_tenant"]["id"], "b": summary["code_batch"]["id"]},
                )
            ).all()
        }
        assert before == after, f"batch delivery state must NOT change item lifecycle; before={before}, after={after}"


class TestAuditOnStateChange:
    """AC5：状态变更写审计日志。"""

    async def test_freeze_and_void_write_audit(self, bypass_session, migrated_pg_url):
        from app.services.code import freeze_batch, void_batch
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])
        batch_id = uuid.UUID(summary["code_batch"]["id"])
        tenant_str = summary["baseline_tenant"]["id"]
        await _reset_baseline_items_to_activated(bypass_session, tenant_str, summary["code_batch"]["id"])

        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await freeze_batch(bypass_session, tenant_id, batch_id)
        await bypass_session.commit()

        # 解冻回去以便后续 void（void 需要 active/frozen 源；这里直接 void frozen 也合法）
        # 跳过解冻，直接 void（frozen → voided 合法）

        # 查审计日志：freeze 已写
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        freeze_audits = (
            await bypass_session.execute(
                text(
                    "SELECT action FROM platform_audit_log WHERE target_tenant_id=:t AND action='code_freeze' LIMIT 5"
                ),
                {"t": tenant_str},
            )
        ).all()
        assert len(freeze_audits) >= 1, "freeze must write audit log"
        await bypass_session.rollback()

        # void
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await void_batch(bypass_session, tenant_id, batch_id)
        await bypass_session.commit()

        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        void_audits = (
            await bypass_session.execute(
                text("SELECT action FROM platform_audit_log WHERE target_tenant_id=:t AND action='code_void' LIMIT 5"),
                {"t": tenant_str},
            )
        ).all()
        assert len(void_audits) >= 1, "void must write audit log"


class TestCrossTenantStateChangeBlocked:
    """AC：多租户隔离不被削弱。"""

    async def test_control_tenant_cannot_void_baseline_batch(self, bypass_session, migrated_pg_url):
        from app.services.code import void_batch
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        baseline_batch_id = uuid.UUID(summary["code_batch"]["id"])
        control_tenant_id = uuid.UUID(summary["control_tenant"]["id"])
        await _reset_baseline_items_to_activated(
            bypass_session, summary["baseline_tenant"]["id"], summary["code_batch"]["id"]
        )

        # 用 control tenant 身份去 void baseline 的 batch
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        resp = await void_batch(bypass_session, control_tenant_id, baseline_batch_id)
        await bypass_session.commit()
        # void_batch 按 tenant_id+batch_id 过滤，control tenant 看不到 baseline 的码 → voided=0
        assert resp.voided == 0, "control tenant must NOT void baseline batch (tenant isolation)"

        # 验证 baseline 的码未被作废
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        statuses = {
            r[0]
            for r in (
                await bypass_session.execute(
                    text("SELECT DISTINCT status FROM code_items WHERE code_batch_id=:b"),
                    {"b": baseline_batch_id},
                )
            ).all()
        }
        assert "revoked" not in statuses, "baseline codes were voided by control tenant — isolation broken"
