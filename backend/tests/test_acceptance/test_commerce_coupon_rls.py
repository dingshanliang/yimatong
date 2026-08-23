"""Real PostgreSQL proof for the signed commerce coupon adapter."""

import json
import uuid
from types import SimpleNamespace

import asyncpg
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.commerce_coupon import list_eligible_commerce_coupons, transition_commerce_coupon
from tests.test_acceptance.conftest import seed_baseline
from tests.test_acceptance.test_brand_membership_rls import _seed_join_facts

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def test_commerce_coupon_adapter_is_tenant_scoped_idempotent_and_refund_safe(
    migrated_pg_url: str,
) -> None:
    summary = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(str(summary["baseline_tenant"]["id"]))
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    try:
        facts = await _seed_join_facts(owner, tenant_id, f"COUPON-{uuid.uuid4().hex[:8]}")
        membership_id = uuid.uuid4()
        connection_id = uuid.uuid4()
        member_reference_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        coupon_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO brand_memberships(id,tenant_id,membership_number,status,join_consent_id) "
            "VALUES($1,$2,$3,'active',$4)",
            membership_id,
            tenant_id,
            f"MBR-{membership_id.hex[:12].upper()}",
            facts["consent_id"],
        )
        await owner.execute(
            "INSERT INTO brand_membership_profile_links(id,tenant_id,membership_id,consumer_profile_id,is_primary,"
            "link_reason,verification_receipt_hash) VALUES($1,$2,$3,$4,true,'explicit_join',$5)",
            uuid.uuid4(),
            tenant_id,
            membership_id,
            facts["consumer_id"],
            "8" * 64,
        )
        await owner.execute(
            "INSERT INTO commerce_connections(id,tenant_id,external_tenant_ref,external_shop_ref,base_url,"
            "capabilities,status,version) VALUES($1,$2,'commerce-a','shop-a','https://commerce.example.test',"
            "$3::jsonb,'active',1)",
            connection_id,
            tenant_id,
            json.dumps(["coupon_lifecycle"]),
        )
        await owner.execute(
            "INSERT INTO commerce_member_references(id,tenant_id,connection_id,membership_id,member_ref) "
            "VALUES($1,$2,$3,$4,'cmr_acceptance_coupon')",
            member_reference_id,
            tenant_id,
            connection_id,
            membership_id,
        )
        await owner.execute(
            "INSERT INTO repurchase_coupon_rule_versions(id,tenant_id,rule_key,version,name,amount_minor,"
            "minimum_spend_minor,currency,product_scope,eligible_product_refs,channel_scope,validity_mode,"
            "valid_days,issuance_limit,issued_count,status,published_at) "
            "VALUES($1,$2,'commerce-adapter',1,'商城复购券',100,500,'CNY','all','[]','online',"
            "'relative',30,10,1,'published',statement_timestamp())",
            rule_id,
            tenant_id,
        )
        await owner.execute(
            "INSERT INTO member_coupons(id,tenant_id,membership_id,rule_version_id,coupon_number,status,"
            "valid_from,valid_until,authority_type,sync_status,version) "
            "VALUES($1,$2,$3,$4,$5,'available',statement_timestamp()-interval '1 day',"
            "statement_timestamp()+interval '29 days','yimatong','not_required',1)",
            coupon_id,
            tenant_id,
            membership_id,
            rule_id,
            f"RCP-{coupon_id.hex[:16].upper()}",
        )
    finally:
        await owner.close()

    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    engine = create_async_engine(runtime_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    credential = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        connection_id=connection_id,
        direction="commerce_to_yimatong",
    )
    connection = SimpleNamespace(
        id=connection_id,
        tenant_id=tenant_id,
        status="active",
        capabilities=["coupon_lifecycle"],
    )
    lines = [
        {
            "product_ref": "MEDUSA-PRODUCT-A",
            "sku_ref": "MEDUSA-SKU-A",
            "quantity": 1,
            "amount_fen": 1000,
        }
    ]
    try:
        async with factory() as db, db.begin():
            eligible = await list_eligible_commerce_coupons(
                db,
                credential=credential,
                connection=connection,
                member_ref="cmr_acceptance_coupon",
                goods_subtotal_fen=1000,
                line_items=lines,
            )
            assert [item["coupon_ref"] for item in eligible] == [str(coupon_id)]
            with pytest.raises(HTTPException) as wrong_member:
                await list_eligible_commerce_coupons(
                    db,
                    credential=credential,
                    connection=connection,
                    member_ref="cmr_other_member",
                    goods_subtotal_fen=1000,
                    line_items=lines,
                )
            assert wrong_member.value.status_code == 403
            with pytest.raises(HTTPException) as missing_capability:
                await list_eligible_commerce_coupons(
                    db,
                    credential=credential,
                    connection=SimpleNamespace(
                        id=connection_id,
                        tenant_id=tenant_id,
                        status="active",
                        capabilities=["order_events"],
                    ),
                    member_ref="cmr_acceptance_coupon",
                    goods_subtotal_fen=1000,
                    line_items=lines,
                )
            assert missing_capability.value.detail == "commerce_coupon_capability_required"
            with pytest.raises(HTTPException) as wrong_connection:
                await list_eligible_commerce_coupons(
                    db,
                    credential=credential,
                    connection=SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), status="active"),
                    member_ref="cmr_acceptance_coupon",
                    goods_subtotal_fen=1000,
                    line_items=lines,
                )
            assert wrong_connection.value.status_code == 401

            common = {
                "db": db,
                "credential": credential,
                "connection": connection,
                "member_ref": "cmr_acceptance_coupon",
                "coupon_ref": coupon_id,
                "order_id": "ORDER-COUPON-ACCEPTANCE",
            }
            reserved = await transition_commerce_coupon(
                **common,
                action="reserve",
                amount_fen=100,
                idempotency_key="commerce-acceptance-reserve",
                goods_subtotal_fen=1000,
                line_items=lines,
                full_refund=None,
            )
            assert reserved.status == "reserved"
            replayed = await transition_commerce_coupon(
                **common,
                action="reserve",
                amount_fen=100,
                idempotency_key="commerce-acceptance-reserve",
                goods_subtotal_fen=1000,
                line_items=lines,
                full_refund=None,
            )
            assert replayed.id == coupon_id
            with pytest.raises(HTTPException) as changed_payload:
                async with db.begin_nested():
                    await transition_commerce_coupon(
                        **common,
                        action="reserve",
                        amount_fen=100,
                        idempotency_key="commerce-acceptance-reserve",
                        goods_subtotal_fen=1000,
                        line_items=[{**lines[0], "product_ref": "MEDUSA-PRODUCT-B"}],
                        full_refund=None,
                    )
            assert changed_payload.value.detail == "coupon_idempotency_conflict"
            used = await transition_commerce_coupon(
                **common,
                action="commit",
                amount_fen=100,
                idempotency_key="commerce-acceptance-commit",
                goods_subtotal_fen=None,
                line_items=[],
                full_refund=None,
            )
            assert used.status == "used"
            partial = await transition_commerce_coupon(
                **common,
                action="reverse",
                amount_fen=100,
                idempotency_key="commerce-acceptance-partial",
                goods_subtotal_fen=None,
                line_items=[],
                full_refund=False,
            )
            assert partial.status == "used"
            restored = await transition_commerce_coupon(
                **common,
                action="reverse",
                amount_fen=100,
                idempotency_key="commerce-acceptance-full",
                goods_subtotal_fen=None,
                line_items=[],
                full_refund=True,
            )
            assert restored.status == "available"
    finally:
        await engine.dispose()

    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval("SELECT status FROM member_coupons WHERE id=$1", coupon_id) == "available"
        assert await owner.fetchval(
            "SELECT count(*) FROM repurchase_coupon_events WHERE coupon_id=$1 AND event_type='refund_recorded'",
            coupon_id,
        ) == 1
        assert await owner.fetchval(
            "SELECT count(*) FROM repurchase_coupon_events WHERE coupon_id=$1 AND event_type='reversed'",
            coupon_id,
        ) == 1
    finally:
        await owner.close()
