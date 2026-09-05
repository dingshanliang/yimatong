import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.repurchase_workbench import RepurchaseWorkItem, RepurchaseWorkItemEvent
from app.models.tenant import Account, Organization, Tenant
from app.schemas.repurchase_workbench import RepurchaseWorkItemAction, RepurchaseWorkItemTransition
from app.services.repurchase_workbench import build_repurchase_workbench, mutate_work_item


async def _operator(db, suffix: str = "workbench") -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    account_id = uuid.uuid4()
    db.add(Tenant(id=tenant_id, name="复购运营品牌", slug=f"{suffix}-{tenant_id.hex[:8]}"))
    db.add(Organization(id=organization_id, tenant_id=tenant_id, name="品牌总部"))
    db.add(
        Account(
            id=account_id,
            tenant_id=tenant_id,
            organization_id=organization_id,
            email=f"operator-{tenant_id.hex[:8]}@example.test",
            hashed_password="test",
            name="复购运营负责人",
        )
    )
    await db.flush()
    return tenant_id, account_id


@pytest.mark.anyio
async def test_workbench_keeps_missing_transaction_coverage_distinct_from_zero(db):
    tenant_id, _ = await _operator(db, "coverage")
    end_at = datetime.now(UTC)

    result = await build_repurchase_workbench(
        db,
        tenant_id,
        start_at=end_at - timedelta(days=30),
        end_at=end_at,
        time_basis="occurrence",
    )

    assert len(result["metrics"]) == 6
    assert result["source_coverage"]["status"] == "unavailable"
    assert result["metrics"][0]["value"] == 0
    assert result["metrics"][0]["availability"] == "complete"
    assert result["metrics"][2]["value"] is None
    assert result["metrics"][2]["availability"] == "unavailable"
    assert "points" not in str(result).lower()


@pytest.mark.anyio
async def test_work_item_lifecycle_records_assignment_transition_resync_and_correction(db):
    tenant_id, account_id = await _operator(db, "lifecycle")
    session_id = uuid.uuid4()
    due_at = datetime.now(UTC) + timedelta(days=1)
    item_id = await mutate_work_item(
        db,
        tenant_id=tenant_id,
        actor_account_id=account_id,
        auth_session_id=session_id,
        payload={
            "action": "create",
            "category": "refund_unsynced",
            "business_ref": "ORDER-20260820-001",
            "title": "退款未同步",
            "impact_summary": "正式净销售额仍包含已退款金额",
            "priority": "high",
            "owner_account_id": account_id,
            "due_at": due_at,
            "reason": "退款来源事实与订单状态不一致",
            "evidence": {"refund_ref": "REFUND-001"},
        },
    )
    next_due_at = due_at + timedelta(days=1)
    await mutate_work_item(
        db,
        tenant_id=tenant_id,
        actor_account_id=account_id,
        auth_session_id=session_id,
        payload={
            "action": "reassign",
            "work_item_id": item_id,
            "owner_account_id": account_id,
            "due_at": next_due_at,
            "reason": "统一由退款负责人闭环",
        },
    )
    await mutate_work_item(
        db,
        tenant_id=tenant_id,
        actor_account_id=account_id,
        auth_session_id=session_id,
        payload={
            "action": "transition",
            "work_item_id": item_id,
            "status": "in_progress",
            "reason": "开始核对来源退款单",
            "evidence": {},
        },
    )
    for action in ("resync", "correction"):
        await mutate_work_item(
            db,
            tenant_id=tenant_id,
            actor_account_id=account_id,
            auth_session_id=session_id,
            payload={
                "action": action,
                "work_item_id": item_id,
                "reason": "核实来源后执行受控操作",
                "before": {"refund_status": "missing"},
                "after": {"refund_status": "confirmed"},
                "evidence": {"refund_ref": "REFUND-001"},
            },
        )

    item = await db.get(RepurchaseWorkItem, item_id)
    events = (
        await db.scalars(
            select(RepurchaseWorkItemEvent)
            .where(RepurchaseWorkItemEvent.work_item_id == item_id)
            .order_by(RepurchaseWorkItemEvent.occurred_at, RepurchaseWorkItemEvent.id)
        )
    ).all()

    assert item is not None
    assert item.status == "in_progress"
    assert item.due_at.replace(tzinfo=UTC) == next_due_at
    assert [event.action for event in events] == [
        "created",
        "reassigned",
        "transitioned",
        "resync_requested",
        "correction_submitted",
    ]
    transition = events[2]
    assert transition.from_status == "pending"
    assert transition.to_status == "in_progress"
    assert events[-1].before_snapshot == {"refund_status": "missing"}
    assert events[-1].after_snapshot == {"refund_status": "confirmed"}


def test_terminal_transition_and_controlled_correction_require_auditable_details():
    with pytest.raises(ValueError):
        RepurchaseWorkItemTransition(status="resolved", reason="完成核实")
    with pytest.raises(ValueError):
        RepurchaseWorkItemAction(action="correction", reason="修正", before={}, after={})
