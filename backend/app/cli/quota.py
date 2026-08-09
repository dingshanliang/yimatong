"""Operator-gated quota counter reconciliation commands."""

from __future__ import annotations

import asyncio

import typer
from sqlalchemy import select, text

from app.core.database import control_session_factory
from app.models.tenant import Tenant
from app.services.quota import (
    QUOTA_RECONCILIATION_SOURCE_REVISION,
    QuotaEnforcementReadiness,
    activate_current_quota_rollout_epoch,
    begin_current_quota_rollout_epoch,
    get_quota_enforcement_readiness,
    reconcile_quota_usage_from_authoritative_rows,
)

app = typer.Typer(help="配额计数桥接、对账与执行门禁")
control_session = control_session_factory


async def _readiness():
    async with control_session() as db, db.begin():
        await db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
        return await get_quota_enforcement_readiness(db)


@app.command("readiness")
def readiness() -> None:
    """检查所有租户是否已在当前代码 revision 完成权威对账。"""

    state = asyncio.run(_readiness())
    status = "READY" if state.ready else "NOT_READY"
    typer.echo(
        f"quota_enforcement={status} ready={state.ready_tenants}/{state.total_tenants} "
        f"source_revision={state.expected_source_revision} "
        f"rollout_phase={state.global_phase.value if state.global_phase else 'missing'} "
        f"rollout_source_revision={state.global_source_revision or 'missing'}"
    )
    if not state.ready:
        raise typer.Exit(code=2)


async def _reconcile_all(*, operator: str) -> tuple[int, QuotaEnforcementReadiness]:
    # Commit the deployment gate before touching any tenant. If the process
    # fails later, the database remains durably drained/non-enforcing and a
    # rerun can safely resume the per-tenant work.
    async with control_session() as db, db.begin():
        await db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
        await begin_current_quota_rollout_epoch(
            db,
            old_writers_drained=True,
            operator=operator,
        )

    async with control_session() as db, db.begin():
        await db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
        tenant_ids = tuple(await db.scalars(select(Tenant.id).order_by(Tenant.id)))

    reconciled = 0
    for tenant_id in tenant_ids:
        # A transaction per tenant bounds lock duration and makes the operation
        # safely resumable. Canonical writers serialize on the same tenant row.
        async with control_session() as db, db.begin():
            await db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
            await reconcile_quota_usage_from_authoritative_rows(
                db,
                tenant_id,
                old_writers_drained=True,
                source_revision=QUOTA_RECONCILIATION_SOURCE_REVISION,
            )
        reconciled += 1

    async with control_session() as db, db.begin():
        await db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
        state = await activate_current_quota_rollout_epoch(db, operator=operator)
    return reconciled, state


@app.command("reconcile")
def reconcile(
    old_writers_drained: bool = typer.Option(
        False,
        "--old-writers-drained",
        help="确认旧版本写进程已全部停止；缺少该确认时拒绝执行",
    ),
    operator: str = typer.Option(
        "quota-cli",
        "--operator",
        help="写入数据库的操作来源（1-128 字符）",
    ),
) -> None:
    """逐租户权威重计并开启配额拒绝逻辑。"""

    if not old_writers_drained:
        raise typer.BadParameter("必须先停止旧写进程并显式传入 --old-writers-drained")
    reconciled, state = asyncio.run(_reconcile_all(operator=operator))
    typer.echo(
        f"reconciled={reconciled} quota_enforcement={'READY' if state.ready else 'NOT_READY'} "
        f"ready={state.ready_tenants}/{state.total_tenants} "
        f"source_revision={state.expected_source_revision} "
        f"rollout_phase={state.global_phase.value if state.global_phase else 'missing'}"
    )
    if not state.ready:
        raise typer.Exit(code=2)
