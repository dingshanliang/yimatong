"""Tenant health scoring service.

Computes a 0–100 health score for each tenant based on:
- Recent scan activity (weight: 30)
- Active campaigns (weight: 20)
- Login recency (weight: 20)
- Days until plan expiry (weight: 30)
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant, TenantStatus
from app.models.tenant_health import TenantHealthMetrics


def _compute_health_status(score: int) -> str:
    if score >= 75:
        return "healthy"
    if score >= 50:
        return "warning"
    if score >= 25:
        return "critical"
    return "dormant"


async def refresh_tenant_health(db: AsyncSession, tenant_id: str) -> TenantHealthMetrics:
    """Recalculate health metrics for a single tenant."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")

    now = datetime.now(UTC)
    score = 0

    # 1. Scan recency (0-30 points)
    # For MVP: check if tenant has recent scan_events via daily_scan_stats
    # Simplified: use last_scan_at if available, otherwise estimate from daily_scan_stats
    from app.models.stats import DailyScanStats

    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    scans_7d = (
        await db.execute(
            select(func.coalesce(func.sum(DailyScanStats.total_scans), 0)).where(
                DailyScanStats.tenant_id == tenant_id,
                DailyScanStats.date >= seven_days_ago.date(),
            )
        )
    ).scalar() or 0

    scans_30d = (
        await db.execute(
            select(func.coalesce(func.sum(DailyScanStats.total_scans), 0)).where(
                DailyScanStats.tenant_id == tenant_id,
                DailyScanStats.date >= thirty_days_ago.date(),
            )
        )
    ).scalar() or 0

    if scans_7d > 0:
        score += min(30, int(30 * min(scans_7d / 100, 1)))
    elif scans_30d > 0:
        score += 10  # Some activity in last 30 days

    # 2. Active campaigns (0-20 points)
    from app.models.campaign import Campaign

    active_campaigns = (
        await db.execute(
            select(func.count(Campaign.id)).where(
                Campaign.tenant_id == tenant_id,
                Campaign.status == "active",
            )
        )
    ).scalar() or 0

    score += min(20, active_campaigns * 5)

    # 3. Login recency — use Account.last_login_at as proxy (0-20 points)
    from app.models.tenant import Account

    last_login = (
        await db.execute(select(func.max(Account.last_login_at)).where(Account.tenant_id == tenant_id))
    ).scalar()

    if last_login:
        days_since_login = (now - last_login).days
        if days_since_login <= 1:
            score += 20
        elif days_since_login <= 7:
            score += 15
        elif days_since_login <= 30:
            score += 8
        elif days_since_login <= 90:
            score += 3

    # 4. Days until expiry (0-30 points)
    days_until_expiry = None
    if tenant.plan_expires_at:
        delta = (tenant.plan_expires_at - now).days
        days_until_expiry = max(0, delta)
        if days_until_expiry > 90:
            score += 30
        elif days_until_expiry > 30:
            score += 20
        elif days_until_expiry > 0:
            score += 5
        # expired → 0 points

    # Find last scan time
    last_scan_date = (
        await db.execute(select(func.max(DailyScanStats.date)).where(DailyScanStats.tenant_id == tenant_id))
    ).scalar()
    last_scan_at = (
        datetime(last_scan_date.year, last_scan_date.month, last_scan_date.day, tzinfo=UTC) if last_scan_date else None
    )

    health_status = _compute_health_status(score)

    # Upsert metrics
    metrics = (
        await db.execute(select(TenantHealthMetrics).where(TenantHealthMetrics.tenant_id == tenant_id))
    ).scalar_one_or_none()

    if metrics:
        metrics.last_scan_at = last_scan_at
        metrics.scans_last_7d = scans_7d
        metrics.scans_last_30d = scans_30d
        metrics.active_campaigns = active_campaigns
        metrics.days_until_expiry = days_until_expiry
        metrics.health_score = score
        metrics.health_status = health_status
        metrics.last_login_at = last_login
    else:
        metrics = TenantHealthMetrics(
            tenant_id=tenant_id,
            last_scan_at=last_scan_at,
            last_login_at=last_login,
            scans_last_7d=scans_7d,
            scans_last_30d=scans_30d,
            active_campaigns=active_campaigns,
            days_until_expiry=days_until_expiry,
            health_score=score,
            health_status=health_status,
        )
        db.add(metrics)

    await db.flush()
    return metrics


async def refresh_all_health_metrics(db: AsyncSession) -> int:
    """Refresh health metrics for all active tenants. Returns count of tenants processed."""
    result = await db.execute(select(Tenant.id).where(Tenant.status == TenantStatus.active))
    tenant_ids = [str(row[0]) for row in result.all()]

    for tid in tenant_ids:
        try:
            await refresh_tenant_health(db, tid)
        except Exception:
            continue  # Skip tenants with missing data

    return len(tenant_ids)
