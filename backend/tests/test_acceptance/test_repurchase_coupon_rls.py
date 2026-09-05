"""Real PostgreSQL proof for the authoritative repurchase coupon lifecycle."""

import asyncio
import os
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.repurchase_coupon import (
    commit_member_coupon,
    create_coupon_rule,
    issue_member_coupon,
    redeem_member_coupon_at_store,
    release_member_coupon,
    reserve_member_coupon,
    reverse_member_coupon,
    transition_coupon_rule,
)
from tests.test_acceptance.conftest import seed_baseline
from tests.test_acceptance.test_brand_membership_rls import _seed_join_facts

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

TABLES = (
    "repurchase_coupon_rule_versions",
    "member_coupons",
    "repurchase_coupon_events",
    "member_notifications",
    "member_notification_deliveries",
)
BACKEND_DIR = Path(__file__).resolve().parents[2]


async def test_coupon_wallet_is_tenant_isolated_audited_and_store_redemption_is_not_gmv(
    migrated_pg_url: str,
) -> None:
    summary = await seed_baseline(migrated_pg_url)
    tenant_a = uuid.UUID(str(summary["baseline_tenant"]["id"]))
    tenant_b = uuid.UUID(str(summary["control_tenant"]["id"]))
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    try:
        facts = await _seed_join_facts(owner, tenant_a, f"COUPON-{uuid.uuid4().hex[:8]}")
        store_id = await owner.fetchval("SELECT id FROM stores WHERE tenant_id=$1 ORDER BY id LIMIT 1", tenant_a)
        if store_id is None:
            store_id = uuid.uuid4()
            await owner.execute(
                "INSERT INTO stores(id,tenant_id,name,code,status,version) VALUES($1,$2,$3,$4,'active',1)",
                store_id,
                tenant_a,
                "复购券验收门店",
                f"ACCEPT-{store_id.hex[:8]}",
            )
        security = await owner.fetch(
            "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relnamespace='public'::regnamespace AND relname=ANY($1::text[]) ORDER BY relname",
            list(TABLES),
        )
        assert len(security) == len(TABLES)
        assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in security)
        for table in TABLES:
            assert await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,'SELECT')", table)
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,$2)", table, privilege)
        for function in (
            "mutate_repurchase_coupon_rule_authority(uuid,text,jsonb)",
            "mutate_member_coupon_authority(uuid,text,jsonb)",
            "record_coupon_member_notification_authority(uuid,uuid)",
        ):
            assert await owner.fetchval("SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", function)
    finally:
        await owner.close()

    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    engine = create_async_engine(runtime_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_a)})
            membership_id = uuid.uuid4()
            await db.execute(
                text(
                    "SELECT * FROM create_brand_membership_authority("
                    ":tenant_id,:consent_id,:scan_event_id,:scan_time,:public_id,:visitor_id,:consumer_id,true,"
                    ":membership_id,:membership_number,:link_id,:event_id,:idempotency_key,:payload_hash)"
                ),
                {
                    "tenant_id": tenant_a,
                    "consent_id": facts["consent_id"],
                    "scan_event_id": facts["scan_event_id"],
                    "scan_time": facts["scan_time"],
                    "public_id": facts["public_id"],
                    "visitor_id": facts["visitor_id"],
                    "consumer_id": facts["consumer_id"],
                    "membership_id": membership_id,
                    "membership_number": f"MBR-{membership_id.hex[:12].upper()}",
                    "link_id": uuid.uuid4(),
                    "event_id": uuid.uuid4(),
                    "idempotency_key": "coupon-membership-join",
                    "payload_hash": "a" * 64,
                },
            )
            rule = await create_coupon_rule(
                db,
                tenant_id=tenant_a,
                rule_key="ACCEPTANCE-REPURCHASE",
                version=1,
                name="复购立减 10 元",
                amount_minor=1000,
                minimum_spend_minor=5000,
                product_scope="all",
                eligible_product_refs=[],
                channel_scope="both",
                validity_mode="relative",
                valid_days=30,
                fixed_valid_from=None,
                fixed_valid_until=None,
                issuance_limit=10,
                idempotency_key="acceptance-rule-create",
            )
            await transition_coupon_rule(
                db,
                tenant_id=tenant_a,
                rule_version_id=rule.id,
                action="publish",
                idempotency_key="acceptance-rule-publish",
                actor_id=None,
            )
            coupon = await issue_member_coupon(
                db,
                tenant_id=tenant_a,
                membership_id=membership_id,
                rule_version_id=rule.id,
                idempotency_key="acceptance-coupon-issue",
            )
            same_coupon = await issue_member_coupon(
                db,
                tenant_id=tenant_a,
                membership_id=membership_id,
                rule_version_id=rule.id,
                idempotency_key="acceptance-coupon-existing-active",
            )
            assert same_coupon.id == coupon.id
            coupon_state = (
                await db.execute(
                    text(
                        "SELECT status,valid_from,valid_until,statement_timestamp() AS now_at FROM member_coupons WHERE id=:id"
                    ),
                    {"id": coupon.id},
                )
            ).one()
            assert coupon_state.status == "available"
            assert coupon_state.valid_from <= coupon_state.now_at < coupon_state.valid_until
            await reserve_member_coupon(
                db,
                tenant_id=tenant_a,
                coupon_id=coupon.id,
                membership_id=membership_id,
                order_ref="ORDER-IDEMPOTENCY",
                goods_subtotal_minor=6000,
                eligible_subtotal_minor=6000,
                idempotency_key="acceptance-reserve-first",
            )
            same_reservation = await reserve_member_coupon(
                db,
                tenant_id=tenant_a,
                coupon_id=coupon.id,
                membership_id=membership_id,
                order_ref="ORDER-IDEMPOTENCY",
                goods_subtotal_minor=6000,
                eligible_subtotal_minor=6000,
                idempotency_key="acceptance-reserve-existing-order",
            )
            assert same_reservation.status == "reserved"
            await release_member_coupon(
                db,
                tenant_id=tenant_a,
                coupon_id=coupon.id,
                order_ref="ORDER-IDEMPOTENCY",
                idempotency_key="acceptance-release-reservation",
            )
            replayed_reservation = await reserve_member_coupon(
                db,
                tenant_id=tenant_a,
                coupon_id=coupon.id,
                membership_id=membership_id,
                order_ref="ORDER-IDEMPOTENCY",
                goods_subtotal_minor=6000,
                eligible_subtotal_minor=6000,
                idempotency_key="acceptance-reserve-existing-order",
            )
            assert replayed_reservation.status == "available"
            before_orders = await db.scalar(text("SELECT count(*) FROM external_orders"))
            before_gmv = await db.scalar(text("SELECT count(*) FROM gmv_attributions"))
            redeemed = await redeem_member_coupon_at_store(
                db,
                tenant_id=tenant_a,
                coupon_id=coupon.id,
                membership_id=membership_id,
                store_id=store_id,
                idempotency_key="acceptance-store-redemption",
                actor_id=uuid.uuid4(),
            )
            assert redeemed.status == "used"
            assert redeemed.used_order_ref is None
            replayed_issue = await issue_member_coupon(
                db,
                tenant_id=tenant_a,
                membership_id=membership_id,
                rule_version_id=rule.id,
                idempotency_key="acceptance-coupon-existing-active",
            )
            assert replayed_issue.id == coupon.id
            assert replayed_issue.status == "used"
            assert (
                await db.scalar(
                    text("SELECT issued_count FROM repurchase_coupon_rule_versions WHERE id=:rule_id"),
                    {"rule_id": rule.id},
                )
                == 1
            )
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM member_coupons WHERE membership_id=:membership_id"),
                    {"membership_id": membership_id},
                )
                == 1
            )
            assert await db.scalar(text("SELECT count(*) FROM external_orders")) == before_orders
            assert await db.scalar(text("SELECT count(*) FROM gmv_attributions")) == before_gmv
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM repurchase_coupon_events WHERE coupon_id=:coupon_id"),
                    {"coupon_id": coupon.id},
                )
                == 6
            )
            notification_types = set(
                await db.scalars(
                    text(
                        "SELECT notification_type FROM member_notifications "
                        "WHERE membership_id=:membership_id AND source_product='repurchase_coupon'"
                    ),
                    {"membership_id": membership_id},
                )
            )
            assert notification_types == {"coupon_issued", "coupon_used"}
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM member_notifications WHERE membership_id=:membership_id "
                        "AND source_product='repurchase_coupon'"
                    ),
                    {"membership_id": membership_id},
                )
                == 2
            )
            used_delivery = (
                await db.execute(
                    text(
                        "SELECT delivery.status,delivery.suppression_reason FROM member_notification_deliveries delivery "
                        "JOIN member_notifications notification ON notification.tenant_id=delivery.tenant_id "
                        "AND notification.id=delivery.notification_id WHERE notification.membership_id=:membership_id "
                        "AND notification.notification_type='coupon_used'"
                    ),
                    {"membership_id": membership_id},
                )
            ).one()
            assert used_delivery.status == "suppressed"
            assert used_delivery.suppression_reason == "channel_not_applicable"

            refunded_coupon = await issue_member_coupon(
                db,
                tenant_id=tenant_a,
                membership_id=membership_id,
                rule_version_id=rule.id,
                idempotency_key="acceptance-refund-coupon-issue",
            )
            await reserve_member_coupon(
                db,
                tenant_id=tenant_a,
                coupon_id=refunded_coupon.id,
                membership_id=membership_id,
                order_ref="ORDER-PARTIAL-REFUND",
                goods_subtotal_minor=6000,
                eligible_subtotal_minor=6000,
                idempotency_key="acceptance-refund-reserve",
            )
            await commit_member_coupon(
                db,
                tenant_id=tenant_a,
                coupon_id=refunded_coupon.id,
                order_ref="ORDER-PARTIAL-REFUND",
                idempotency_key="acceptance-refund-commit",
            )
            partial = await reverse_member_coupon(
                db,
                tenant_id=tenant_a,
                coupon_id=refunded_coupon.id,
                order_ref="ORDER-PARTIAL-REFUND",
                full_refund=False,
                idempotency_key="acceptance-partial-refund",
            )
            replayed_partial = await reverse_member_coupon(
                db,
                tenant_id=tenant_a,
                coupon_id=refunded_coupon.id,
                order_ref="ORDER-PARTIAL-REFUND",
                full_refund=False,
                idempotency_key="acceptance-partial-refund",
            )
            assert partial.status == replayed_partial.status == "used"
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM repurchase_coupon_events "
                        "WHERE idempotency_key='acceptance-partial-refund' AND event_type='refund_recorded'"
                    )
                )
                == 1
            )
            with pytest.raises(HTTPException, match="coupon_idempotency_conflict"):
                async with db.begin_nested():
                    await reverse_member_coupon(
                        db,
                        tenant_id=tenant_a,
                        coupon_id=refunded_coupon.id,
                        order_ref="ORDER-PARTIAL-REFUND",
                        full_refund=True,
                        idempotency_key="acceptance-partial-refund",
                    )

        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_b)})
            for table in TABLES:
                assert await db.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    finally:
        await engine.dispose()

    runtime = await asyncpg.connect(owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    try:
        direct_writes = (
            "INSERT INTO repurchase_coupon_rule_versions DEFAULT VALUES",
            "UPDATE repurchase_coupon_rule_versions SET status='ended'",
            "DELETE FROM repurchase_coupon_rule_versions",
            "INSERT INTO member_coupons DEFAULT VALUES",
            "UPDATE member_coupons SET status='used'",
            "DELETE FROM member_coupons",
            "INSERT INTO repurchase_coupon_events DEFAULT VALUES",
            "UPDATE repurchase_coupon_events SET reason='forged'",
            "DELETE FROM repurchase_coupon_events",
        )
        for statement in direct_writes:
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await runtime.execute(statement)
    finally:
        await runtime.close()

    env = os.environ.copy()
    env["database_url"] = migrated_pg_url
    env["migration_database_url"] = migrated_pg_url
    downgrade = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "downgrade",
        "c8a8c3346d7c",
        cwd=BACKEND_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await downgrade.communicate()
    assert downgrade.returncode != 0
    assert b"partial refund receipts exist; archive before downgrade" in stdout + stderr
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "0b91acfedc87"
    finally:
        await owner.close()
    upgrade = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        "head",
        cwd=BACKEND_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await upgrade.communicate()
    assert upgrade.returncode == 0, (stdout + stderr).decode()
