"""Seed platform demo data: plan definitions (if empty) and sample audit logs."""

import asyncio
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from app.core.database import async_session_factory, engine
from app.core.database import _is_pg


async def seed():
    if not _is_pg:
        print("⚠️  Skipping platform seed: not using PostgreSQL")
        return

    async with async_session_factory() as db:
        from app.models.plan import PlanDefinition
        from app.models.audit import PlatformAuditLog

        # Check if plans already exist
        result = await db.execute(select(PlanDefinition).limit(1))
        if result.scalar_one_or_none():
            print("✅ Plan definitions already exist, skipping")
        else:
            plans = [
                PlanDefinition(
                    id="plan-free", name="free", display_name="免费版",
                    description="基础功能，适合试用", price_yearly=0,
                    quota_defaults={"max_codes": 1000, "max_scans": 5000, "max_campaigns": 3, "max_accounts": 2},
                    feature_flags={"ai_assistant": False, "risk_module": False, "channel_portal": False},
                    sort_order=0,
                ),
                PlanDefinition(
                    id="plan-starter", name="starter", display_name="入门版",
                    description="适合小型品牌", price_yearly=29900,
                    quota_defaults={"max_codes": 10000, "max_scans": 50000, "max_campaigns": 10, "max_accounts": 5},
                    feature_flags={"ai_assistant": True, "risk_module": False, "channel_portal": False},
                    sort_order=1,
                ),
                PlanDefinition(
                    id="plan-pro", name="pro", display_name="专业版",
                    description="适合成长型品牌", price_yearly=99900,
                    quota_defaults={"max_codes": 100000, "max_scans": 500000, "max_campaigns": 50, "max_accounts": 20},
                    feature_flags={"ai_assistant": True, "risk_module": True, "channel_portal": True},
                    sort_order=2,
                ),
                PlanDefinition(
                    id="plan-enterprise", name="enterprise", display_name="企业版",
                    description="无限制，专属服务", price_yearly=299900,
                    quota_defaults={"max_codes": -1, "max_scans": -1, "max_campaigns": -1, "max_accounts": -1},
                    feature_flags={"ai_assistant": True, "risk_module": True, "channel_portal": True, "white_label": True},
                    sort_order=3,
                ),
            ]
            for p in plans:
                db.add(p)
            await db.flush()
            print(f"✅ Created {len(plans)} plan definitions")

        # Sample audit logs
        from datetime import UTC, datetime, timedelta

        result = await db.execute(select(PlatformAuditLog).limit(1))
        if result.scalar_one_or_none():
            print("✅ Audit logs already exist, skipping")
        else:
            from app.models.tenant import Tenant

            tenants_result = await db.execute(select(Tenant).limit(3))
            tenants = list(tenants_result.scalars().all())

            if tenants:
                now = datetime.now(UTC)
                sample_logs = [
                    PlatformAuditLog(
                        operator_id="platform-admin",
                        target_tenant_id=str(tenants[0].id),
                        action="create_tenant",
                        resource=f"tenant:{tenants[0].slug}",
                        timestamp=now - timedelta(days=30),
                    ),
                    PlatformAuditLog(
                        operator_id="platform-admin",
                        target_tenant_id=str(tenants[0].id),
                        action="assign_plan",
                        resource="plan:starter",
                        timestamp=now - timedelta(days=29),
                    ),
                ]
                if len(tenants) > 1:
                    sample_logs.append(
                        PlatformAuditLog(
                            operator_id="platform-admin",
                            target_tenant_id=str(tenants[1].id),
                            action="create_tenant",
                            resource=f"tenant:{tenants[1].slug}",
                            timestamp=now - timedelta(days=15),
                        )
                    )
                if len(tenants) > 2:
                    sample_logs.append(
                        PlatformAuditLog(
                            operator_id="platform-admin",
                            target_tenant_id=str(tenants[2].id),
                            action="status_change:active->suspended",
                            resource=f"tenant:{tenants[2].slug}",
                            timestamp=now - timedelta(days=2),
                        )
                    )

                for log in sample_logs:
                    db.add(log)
                await db.flush()
                print(f"✅ Created {len(sample_logs)} sample audit logs")
            else:
                print("ℹ️  No tenants found, skipping audit log seed")

        await db.commit()
        print("\n🎉 Platform seed complete!")


if __name__ == "__main__":
    asyncio.run(seed())
