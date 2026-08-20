from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.database import _session_uses_postgresql, set_session_tenant_context
from app.models.commerce_integration import CommerceConnection
from app.models.commerce_order import CommerceOrderFact, CommerceRefundFact, CommerceRepurchaseAttribution
from app.models.consent import ConsentRecord
from app.models.member import BrandMembership
from app.models.member_notification import MemberNotificationDelivery
from app.models.repurchase_coupon import MemberCoupon
from app.models.repurchase_workbench import RepurchaseWorkItem, RepurchaseWorkItemEvent
from app.models.tenant import Account
from app.utils import utcnow

MATURE_DAYS = 30


def _availability(coverage: str) -> str:
    return {"complete": "complete", "partial": "partial", "unavailable": "unavailable"}[coverage]


async def build_repurchase_workbench(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    start_at: datetime,
    end_at: datetime,
    time_basis: str,
) -> dict[str, Any]:
    await set_session_tenant_context(db, tenant_id)
    maturity_cutoff = end_at - timedelta(days=MATURE_DAYS)
    cohort_time = ConsentRecord.scan_event_time
    new_members = int(
        await db.scalar(
            select(func.count())
            .select_from(BrandMembership)
            .where(
                BrandMembership.tenant_id == tenant_id,
                BrandMembership.status == "active",
                BrandMembership.joined_at >= start_at,
                BrandMembership.joined_at < end_at,
            )
        )
        or 0
    )
    mature_members = int(
        await db.scalar(
            select(func.count(func.distinct(BrandMembership.id)))
            .select_from(BrandMembership)
            .join(
                ConsentRecord,
                (ConsentRecord.tenant_id == BrandMembership.tenant_id)
                & (ConsentRecord.id == BrandMembership.join_consent_id),
            )
            .where(
                BrandMembership.tenant_id == tenant_id,
                BrandMembership.status == "active",
                cohort_time >= start_at,
                cohort_time < end_at,
                cohort_time <= maturity_cutoff,
            )
        )
        or 0
    )

    active_connections = int(
        await db.scalar(
            select(func.count())
            .select_from(CommerceConnection)
            .where(CommerceConnection.tenant_id == tenant_id, CommerceConnection.status == "active")
        )
        or 0
    )
    incomplete_orders = int(
        await db.scalar(
            select(func.count())
            .select_from(CommerceOrderFact)
            .where(CommerceOrderFact.tenant_id == tenant_id, CommerceOrderFact.coverage_status != "complete")
        )
        or 0
    )
    coverage = "unavailable" if active_connections == 0 else ("partial" if incomplete_orders else "complete")
    basis_column = (
        CommerceRepurchaseAttribution.occurrence_at
        if time_basis == "occurrence"
        else CommerceRepurchaseAttribution.member_cohort_at
    )
    filters = [
        CommerceRepurchaseAttribution.tenant_id == tenant_id,
        basis_column >= start_at,
        basis_column < end_at,
        CommerceRepurchaseAttribution.trust_level == "authoritative",
        CommerceRepurchaseAttribution.coverage_status == "complete",
    ]
    if time_basis == "cohort":
        filters.append(CommerceRepurchaseAttribution.member_cohort_at <= maturity_cutoff)
    metric_row = (
        await db.execute(
            select(
                func.count(func.distinct(CommerceRepurchaseAttribution.membership_id))
                .filter(CommerceRepurchaseAttribution.is_packaging_repurchase)
                .label("repurchase_members"),
                func.count()
                .filter(CommerceRepurchaseAttribution.is_packaging_repurchase)
                .label("packaging_repurchase_orders"),
                func.coalesce(
                    func.sum(CommerceRepurchaseAttribution.net_product_sales_fen).filter(
                        CommerceRepurchaseAttribution.is_packaging_repurchase
                    ),
                    0,
                ).label("attributed_net_sales_fen"),
            ).where(*filters)
        )
    ).one()
    repurchase_members = int(metric_row.repurchase_members or 0)
    transaction_values: dict[str, int | float | None] = {
        "repurchase_members": repurchase_members,
        "packaging_repurchase_orders": int(metric_row.packaging_repurchase_orders or 0),
        "attributed_net_sales_fen": int(metric_row.attributed_net_sales_fen or 0),
        "member_scan_repurchase_rate_percent": (
            round(repurchase_members * 100 / mature_members, 2) if mature_members else None
        ),
    }
    if coverage == "unavailable":
        transaction_values = {key: None for key in transaction_values}

    cutoff = await db.scalar(
        select(func.max(CommerceOrderFact.updated_at)).where(CommerceOrderFact.tenant_id == tenant_id)
    )
    operations = (
        await db.execute(
            select(
                func.count().filter(MemberCoupon.status == "available").label("available_coupons"),
                func.count().filter(MemberCoupon.sync_status == "error").label("coupon_sync_failures"),
            ).where(MemberCoupon.tenant_id == tenant_id)
        )
    ).one()
    delivery_failures = int(
        await db.scalar(
            select(func.count())
            .select_from(MemberNotificationDelivery)
            .where(
                MemberNotificationDelivery.tenant_id == tenant_id,
                MemberNotificationDelivery.status.in_(["failed", "exhausted"]),
            )
        )
        or 0
    )
    refund_count = int(
        await db.scalar(
            select(func.count())
            .select_from(CommerceRefundFact)
            .where(
                CommerceRefundFact.tenant_id == tenant_id,
                CommerceRefundFact.occurred_at >= start_at,
                CommerceRefundFact.occurred_at < end_at,
            )
        )
        or 0
    )
    unattributed = int(
        await db.scalar(
            select(func.count())
            .select_from(CommerceRepurchaseAttribution)
            .where(
                CommerceRepurchaseAttribution.tenant_id == tenant_id,
                CommerceRepurchaseAttribution.trust_level == "unattributed",
            )
        )
        or 0
    )
    recent_orders = (
        await db.scalars(
            select(CommerceOrderFact)
            .where(CommerceOrderFact.tenant_id == tenant_id)
            .order_by(CommerceOrderFact.last_event_occurred_at.desc())
            .limit(20)
        )
    ).all()
    work_items = await list_work_items(db, tenant_id)
    common = {"availability": _availability(coverage), "coverage_status": coverage, "data_cutoff_at": cutoff}
    metrics = [
        {"key": "new_members", "label": "新增正式会员", "value": new_members, **{**common, "availability": "complete"}},
        {
            "key": "mature_members",
            "label": "成熟观察会员",
            "value": mature_members,
            **{**common, "availability": "complete"},
        },
        {
            "key": "repurchase_members",
            "label": "产生复购的会员",
            "value": transaction_values["repurchase_members"],
            **common,
        },
        {
            "key": "member_scan_repurchase_rate_percent",
            "label": "会员整体扫码复购率",
            "value": transaction_values["member_scan_repurchase_rate_percent"],
            **common,
        },
        {
            "key": "packaging_repurchase_orders",
            "label": "包装扫码复购订单数",
            "value": transaction_values["packaging_repurchase_orders"],
            **common,
        },
        {
            "key": "attributed_net_sales_fen",
            "label": "归因净销售额",
            "value": transaction_values["attributed_net_sales_fen"],
            **common,
        },
    ]
    return {
        "time_basis": time_basis,
        "start_at": start_at,
        "end_at": end_at,
        "maturity_days": MATURE_DAYS,
        "metrics": metrics,
        "operations": {
            "available_coupons": int(operations.available_coupons or 0),
            "coupon_sync_failures": int(operations.coupon_sync_failures or 0),
            "notification_failures": delivery_failures,
            "refunds": refund_count,
            "unattributed_orders": unattributed,
            "coverage_issues": incomplete_orders,
        },
        "source_coverage": {
            "status": coverage,
            "active_connections": active_connections,
            "incomplete_orders": incomplete_orders,
            "data_cutoff_at": cutoff,
        },
        "trust_rubric": [
            {"key": "platform_authoritative", "label": "平台权威事实", "included": True},
            {"key": "trusted_transaction", "label": "可信交易事实", "included": True},
            {"key": "validated_import", "label": "已校验导入", "included": True},
            {"key": "reference", "label": "参考数据", "included": False},
        ],
        "recent_orders": [
            {
                "order_ref": order.external_order_ref,
                "source_system": order.source_system,
                "status": order.status,
                "net_product_sales_fen": order.product_net_amount_fen,
                "coverage_status": order.coverage_status,
                "occurred_at": order.last_event_occurred_at,
            }
            for order in recent_orders
        ],
        "work_items": work_items,
    }


async def list_work_items(db: AsyncSession, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(RepurchaseWorkItem, Account.name, Account.email)
            .join(
                Account,
                (Account.tenant_id == RepurchaseWorkItem.tenant_id)
                & (Account.id == RepurchaseWorkItem.owner_account_id),
            )
            .where(RepurchaseWorkItem.tenant_id == tenant_id)
            .order_by(RepurchaseWorkItem.resolved_at.is_not(None), RepurchaseWorkItem.due_at)
        )
    ).all()
    return [
        {
            "id": item.id,
            "category": item.category,
            "business_ref": item.business_ref,
            "title": item.title,
            "impact_summary": item.impact_summary,
            "priority": item.priority,
            "status": item.status,
            "owner": {"name": name, "email": email},
            "owner_account_id": item.owner_account_id,
            "due_at": item.due_at,
            "conclusion": item.conclusion,
            "evidence": item.evidence,
            "resolved_at": item.resolved_at,
            "overdue": item.resolved_at is None and item.due_at < utcnow(),
        }
        for item, name, email in rows
    ]


async def mutate_work_item(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_account_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    payload: dict[str, Any],
) -> uuid.UUID:
    payload = {
        **payload,
        "actor_account_id": str(actor_account_id),
        "auth_session_id": str(auth_session_id),
        "event_id": str(uuid7()),
    }
    payload.setdefault("work_item_id", str(uuid7()))
    if _session_uses_postgresql(db):
        try:
            return uuid.UUID(
                str(
                    await db.scalar(
                        text("SELECT public.mutate_repurchase_work_item_authority(:tenant_id,CAST(:payload AS jsonb))"),
                        {"tenant_id": tenant_id, "payload": __import__("json").dumps(payload, default=str)},
                    )
                )
            )
        except DBAPIError as exc:
            code = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if code == "42501":
                raise HTTPException(status_code=403, detail="repurchase_workbench_authority_denied") from exc
            raise HTTPException(status_code=409, detail="repurchase_work_item_conflict") from exc

    action = payload["action"]
    item = None
    if action == "create":
        item = await db.scalar(
            select(RepurchaseWorkItem).where(
                RepurchaseWorkItem.tenant_id == tenant_id,
                RepurchaseWorkItem.category == payload["category"],
                RepurchaseWorkItem.business_ref == payload["business_ref"],
            )
        )
        if item is None:
            item = RepurchaseWorkItem(
                id=uuid.UUID(payload["work_item_id"]),
                tenant_id=tenant_id,
                category=payload["category"],
                business_ref=payload["business_ref"],
                title=payload["title"],
                impact_summary=payload["impact_summary"],
                priority=payload["priority"],
                owner_account_id=uuid.UUID(str(payload["owner_account_id"])),
                due_at=datetime.fromisoformat(str(payload["due_at"])),
                evidence=payload.get("evidence", {}),
            )
            db.add(item)
            event_action = "created"
        else:
            return item.id
    else:
        item = await db.scalar(
            select(RepurchaseWorkItem).where(
                RepurchaseWorkItem.tenant_id == tenant_id,
                RepurchaseWorkItem.id == uuid.UUID(str(payload["work_item_id"])),
            )
        )
        if item is None:
            raise HTTPException(status_code=404, detail="repurchase_work_item_not_found")
        previous_status = item.status
        event_action = {
            "transition": "transitioned",
            "reassign": "reassigned",
            "resync": "resync_requested",
            "correction": "correction_submitted",
        }[action]
        if action == "transition":
            item.status = payload["status"]
            item.conclusion = payload.get("conclusion")
            item.evidence = {**item.evidence, **payload.get("evidence", {})}
            item.resolved_at = utcnow() if item.status in {"resolved", "no_action"} else None
        elif action == "reassign":
            item.owner_account_id = uuid.UUID(str(payload["owner_account_id"]))
            item.due_at = datetime.fromisoformat(str(payload["due_at"]))
    db.add(
        RepurchaseWorkItemEvent(
            id=uuid.UUID(payload["event_id"]),
            tenant_id=tenant_id,
            work_item_id=item.id,
            action=event_action,
            from_status=item.status if action == "create" else previous_status,
            to_status=payload.get("status", item.status),
            before_snapshot=payload.get("before", {}),
            after_snapshot=payload.get("after", {}),
            reason=payload["reason"],
            evidence=payload.get("evidence", {}),
            actor_account_id=actor_account_id,
        )
    )
    await db.flush()
    return item.id
