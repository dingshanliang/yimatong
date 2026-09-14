"""Real PostgreSQL proof for the external coupon wallet authority (youzan path)."""

import uuid

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.external_coupon_wallet import (
    confirm_external_coupon_sync,
    consume_external_coupon,
    issue_external_member_coupon,
)
from app.services.repurchase_coupon import create_coupon_rule, transition_coupon_rule
from tests.test_acceptance.conftest import seed_baseline
from tests.test_acceptance.test_brand_membership_rls import _seed_join_facts

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def _seed_runtime_facts(owner: asyncpg.Connection, tenant_id) -> dict:
    facts = await _seed_join_facts(owner, tenant_id, f"YZW-{uuid.uuid4().hex[:8]}")
    connector_id = uuid.uuid4()
    await owner.execute(
        "INSERT INTO connectors(id,tenant_id,name,connector_type,config,enabled) "
        "VALUES($1,$2,'有赞验收连接器','youzan','{\"client_id\":\"cid\"}'::jsonb,true)",
        connector_id,
        tenant_id,
    )
    benefit_id = uuid.uuid4()
    await owner.execute(
        "INSERT INTO benefits(id,tenant_id,name,benefit_type,config_json,stock_total,stock_used,per_person_limit,status) "
        "VALUES($1,$2,'有赞券权益','platform_coupon',"
        '\'{"coupon_id":"YZ-TPL","rule_version_id":null}\'::jsonb,10,0,1,\'active\')',
        benefit_id,
        tenant_id,
    )
    claim_id = uuid.uuid4()
    await owner.execute(
        "INSERT INTO benefit_claims(id,tenant_id,benefit_id,consumer_id,idempotency_key,claim_type,status,delivery_status) "
        "VALUES($1,$2,$3,$4,$5,'claim','success','processing')",
        claim_id,
        tenant_id,
        benefit_id,
        str(facts["consumer_id"]),
        f"claim-{uuid.uuid4().hex[:12]}",
    )
    return {**facts, "connector_id": connector_id, "claim_id": claim_id}


async def test_external_coupon_wallet_authority_on_postgres(migrated_pg_url: str) -> None:
    summary = await seed_baseline(migrated_pg_url)
    tenant_a = uuid.UUID(str(summary["baseline_tenant"]["id"]))
    tenant_b = uuid.UUID(str(summary["control_tenant"]["id"]))
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    try:
        facts = await _seed_runtime_facts(owner, tenant_a)

        # 新增约束/索引/权限就位
        assert await owner.fetchval("SELECT 1 FROM pg_indexes WHERE indexname='uq_member_coupons_external_ref'")
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app','mutate_member_coupon_authority(uuid,text,jsonb)','EXECUTE')"
        )
        assert await owner.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname='ck_connectors_config_has_no_plaintext_secrets' AND conrelid='connectors'::regclass"
        ) and "client_secret" in str(
            await owner.fetchval(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname='ck_connectors_config_has_no_plaintext_secrets'"
            )
        )
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
                    "idempotency_key": "yz-wallet-membership-join",
                    "payload_hash": "b" * 64,
                },
            )
            rule = await create_coupon_rule(
                db,
                tenant_id=tenant_a,
                rule_key=f"YZ-ACCEPT-{uuid.uuid4().hex[:6]}",
                version=1,
                name="有赞外部券规则",
                amount_minor=500,
                minimum_spend_minor=2000,
                product_scope="all",
                eligible_product_refs=[],
                channel_scope="both",
                validity_mode="relative",
                valid_days=30,
                fixed_valid_from=None,
                fixed_valid_until=None,
                issuance_limit=10,
                idempotency_key="yz-rule-create",
            )
            await transition_coupon_rule(
                db,
                tenant_id=tenant_a,
                rule_version_id=rule.id,
                action="publish",
                idempotency_key="yz-rule-publish",
                actor_id=None,
            )
            coupon = await issue_external_member_coupon(
                db,
                tenant_id=tenant_a,
                membership_id=membership_id,
                rule_version_id=rule.id,
                claim_id=facts["claim_id"],
                connector_id=facts["connector_id"],
            )
            assert coupon.sync_status == "pending"
            assert coupon.authority_type == "external"

            await confirm_external_coupon_sync(
                db, tenant_id=tenant_a, claim_id=facts["claim_id"], external_id="YZ-ACCEPT-GRANT-1"
            )
            assert (
                await consume_external_coupon(
                    db, tenant_a, facts["connector_id"], "YZ-ACCEPT-GRANT-1", {"event": "coupon_consume"}
                )
                is False
            )  # 尚无发放记录映射

        # 补齐发放记录后走完整回流；同时验证跨租户不可见
        owner = await asyncpg.connect(owner_dsn)
        try:
            await owner.execute(
                "INSERT INTO benefit_deliveries(id,tenant_id,connector_id,claim_id,consumer_id,benefit_type,"
                "benefit_config,external_id,status,retry_count,max_retries) "
                "VALUES($1,$2,$3,$4,$5,'platform_coupon','{}'::jsonb,'YZ-ACCEPT-GRANT-1','success',0,5)",
                uuid.uuid4(),
                tenant_a,
                facts["connector_id"],
                facts["claim_id"],
                str(facts["consumer_id"]),
            )
        finally:
            await owner.close()

        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_b)})
            assert (
                await consume_external_coupon(
                    db, tenant_b, facts["connector_id"], "YZ-ACCEPT-GRANT-1", {"event": "coupon_consume"}
                )
                is False
            )

        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_a)})
            assert (
                await consume_external_coupon(
                    db, tenant_a, facts["connector_id"], "YZ-ACCEPT-GRANT-1", {"event": "coupon_consume"}
                )
                is True
            )
            row = (
                (
                    await db.execute(
                        text(
                            "SELECT status, sync_status, used_at IS NOT NULL AS used, "
                            "used_order_ref IS NULL AS no_local_ref "
                            "FROM member_coupons WHERE tenant_id=:t AND source_claim_id=:c"
                        ),
                        {"t": str(tenant_a), "c": str(facts["claim_id"])},
                    )
                )
                .mappings()
                .one()
            )
            assert row["status"] == "used"
            assert row["sync_status"] == "synchronized"
            assert row["used"] and row["no_local_ref"]

            events = [
                event["event_type"]
                for event in (
                    await db.execute(
                        text(
                            "SELECT event_type FROM repurchase_coupon_events "
                            "WHERE tenant_id=:t AND coupon_id=:cid ORDER BY occurred_at"
                        ),
                        {"t": str(tenant_a), "cid": str(coupon.id)},
                    )
                ).mappings()
            ]
            assert events == ["external_sync_pending", "external_sync_confirmed", "external_sync_confirmed"]
    finally:
        await engine.dispose()

    # 共享 acceptance 库礼仪：把已核销的外部券复位，避免本测试的消费状态
    # 触发后续测试（如降级演练）撞上 f619a09be4c0 的 downgrade 守卫。
    owner = await asyncpg.connect(
        migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://").replace(
            "yimatong:yimatong@", "yimatong:yimatong@"
        )
    )
    try:
        await owner.execute(
            "UPDATE member_coupons SET status='available', used_at=NULL, version=version+1 "
            "WHERE tenant_id=$1 AND authority_type='external' AND status='used' "
            "AND used_order_ref IS NULL AND used_store_id IS NULL",
            tenant_a,
        )
    finally:
        await owner.close()
