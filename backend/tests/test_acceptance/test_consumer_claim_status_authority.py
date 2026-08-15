"""Consumer claim status closure on real PG: replay truth, RLS-scoped derivation, no double release.

yimatong-kc6d S3：失败终态预留释放与幂等契约必须在真实 PostgreSQL 上成立。
SQLite 覆盖应用层过滤；本文件锁定 RLS 会话下的三态派生、幂等重放响应的
真实状态（Story 22）以及旧链路释放函数对已由 PG 权威释放的 claim 不二次扣减。
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import set_session_tenant_context
from app.models.campaign import Benefit
from app.services.benefit_claim_status import build_claim_success_payload, get_consumer_claim_status
from app.services.benefit_delivery_handler import release_failed_delivery_reservation
from tests.test_acceptance.test_campaign_claim_outbox_authority import (
    _runtime_connection,
    _seed_catalog,
    _seed_claim_evidence,
    _seed_claim_launch,
)

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def _runtime_session_factory(migrated_pg_url: str) -> tuple[async_sessionmaker[AsyncSession], object]:
    runtime_dsn = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    engine = create_async_engine(runtime_dsn)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


async def _drive_outbox_to_dead_letter(
    runtime: asyncpg.Connection,
    tenant_id: uuid.UUID,
    outbox_id: uuid.UUID,
) -> None:
    for _attempt in range(1, 9):
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))
            lease = await runtime.fetchrow("SELECT * FROM lease_campaign_claim_outbox($1,'worker-a',1,30)", tenant_id)
            failed = await runtime.fetchrow(
                "SELECT * FROM fail_campaign_claim_outbox($1,$2,$3,'provider unavailable',0)",
                tenant_id,
                outbox_id,
                lease["lease_token"],
            )
            assert failed["current_status"] in {"pending", "dead_letter"}


async def test_replay_payload_and_status_derivation_follow_real_facts(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    runtime = await _runtime_connection(migrated_pg_url)
    ids = await _seed_catalog(owner, "claim-status-closure")
    other = await _seed_catalog(owner, "claim-status-other-tenant")
    connector_id = uuid.uuid4()
    benefit_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    idempotency_key = f"claim:v1:{uuid.uuid4().hex}"
    consumer_ref = f"anon:v1:{uuid.uuid4().hex}"
    scan_event_id, public_id, _item_id, batch_id = await _seed_claim_evidence(owner, ids)
    await owner.execute(
        "INSERT INTO connectors(id,tenant_id,name,connector_type,config,enabled,created_at,updated_at) "
        "VALUES($1,$2,'cash','wechat_pay','{}',true,now(),now())",
        connector_id,
        ids["tenant"],
    )
    await owner.execute(
        "INSERT INTO benefits(id,tenant_id,campaign_id,name,benefit_type,config_json,connector_id,"
        "stock_total,stock_used,per_person_limit,status,created_at,updated_at) "
        "VALUES($1,$2,NULL,'cash','cash_red_packet',"
        '\'{"validity_type":"after_claim_days","validity_days":1,"amount_type":"fixed",'
        '"fixed_amount":100,"budget":100,"claimed_budget":0}\'::jsonb,$3,2,0,1,\'active\',now(),now())',
        benefit_id,
        ids["tenant"],
        connector_id,
    )
    launch_release_id, launch_campaign_id, launch_digest = await _seed_claim_launch(owner, ids, batch_id, (benefit_id,))
    factory, engine = await _runtime_session_factory(migrated_pg_url)
    try:
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            claimed = await runtime.fetchrow(
                "SELECT * FROM claim_campaign_benefit($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                ids["tenant"],
                claim_id,
                benefit_id,
                scan_event_id,
                ids["product"],
                public_id,
                consumer_ref,
                idempotency_key,
                launch_release_id,
                launch_campaign_id,
                launch_digest,
            )
        assert claimed["created"] is True and claimed["reserved_amount"] == 100
        outbox_id = claimed["outbox_id"]

        await _drive_outbox_to_dead_letter(runtime, ids["tenant"], outbox_id)
        # PG 权威链已在 dead_letter 时释放预留并终态化 claim。
        assert (
            await owner.fetchval(
                "SELECT stock_used FROM benefits WHERE tenant_id=$1 AND id=$2", ids["tenant"], benefit_id
            )
            == 0
        )
        assert (
            await owner.fetchval(
                "SELECT reservation_status='refunded' AND status='failed' FROM benefit_claims "
                "WHERE tenant_id=$1 AND id=$2",
                ids["tenant"],
                claim_id,
            )
        )

        # 消费者读路径：受限 runtime 角色 + 租户上下文（RLS）下派生真实三态。
        async with factory() as session:
            await set_session_tenant_context(session, ids["tenant"])
            status = await get_consumer_claim_status(session, ids["tenant"], claim_id, consumer_ref)
            assert status is not None and status.status == "failed"
            assert status.failure_reason in {"channel_failure", "risk_paused", "recipient_missing", "system_error"}

            # 主体不匹配 → None（防枚举语义的读侧来源）。
            assert await get_consumer_claim_status(session, ids["tenant"], claim_id, "anon:v1:not-me") is None

            # 幂等重放（Story 22）：响应携带既有 claim 的真实终态，而非纯 pending。
            benefit = await session.scalar(select(Benefit).where(Benefit.id == benefit_id))
            payload = await build_claim_success_payload(
                session,
                benefit,
                {"outcome": "replayed", "claim_id": str(claim_id)},
                consumer_ref,
            )
            assert payload["status"] == "pending"  # 受理口径：引导进入结果页
            assert payload["delivery"]["status"] == "failed"
            assert payload["revisit_credential"]

        # 跨租户：另一租户上下文 + 相同 claim id → RLS/过滤下不可见。
        async with factory() as session:
            await set_session_tenant_context(session, other["tenant"])
            assert (
                await get_consumer_claim_status(session, other["tenant"], claim_id, consumer_ref) is None
            )

        # 旧链路释放函数（worker 语义，bypass 会话）不得对已退回的预留二次扣减。
        control_url = migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
        control_engine = create_async_engine(control_url)
        control_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with control_factory() as session:
                from sqlalchemy import text

                await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
                assert await release_failed_delivery_reservation(session, ids["tenant"], claim_id) is False
                await session.commit()
        finally:
            await control_engine.dispose()
        assert (
            await owner.fetchval(
                "SELECT stock_used FROM benefits WHERE tenant_id=$1 AND id=$2", ids["tenant"], benefit_id
            )
            == 0
        )
    finally:
        await engine.dispose()
        await runtime.close()
        await owner.close()


async def test_legacy_reserved_claim_release_is_idempotent_on_real_pg(migrated_pg_url: str) -> None:
    """旧链路仍 reserved 的 claim：worker 释放真实生效且幂等（二次调用 False）。"""

    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    ids = await _seed_catalog(owner, "claim-status-legacy-release")
    benefit_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    consumer_ref = f"anon:v1:{uuid.uuid4().hex}"
    await owner.execute(
        "INSERT INTO campaigns(id,tenant_id,name,campaign_type,status,product_id,start_at,end_at,rules_json,"
        "created_at,updated_at) VALUES($1,$2,'legacy release fixture','coupon','active',$3,"
        "now()-interval '1 hour',now()+interval '1 day','{}',now(),now())",
        campaign_id,
        ids["tenant"],
        ids["product"],
    )
    await owner.execute(
        "INSERT INTO benefits(id,tenant_id,campaign_id,name,benefit_type,config_json,"
        "stock_total,stock_used,per_person_limit,status,created_at,updated_at) "
        "VALUES($1,$2,$3,'legacy','cash_red_packet',"
        '\'{"claimed_budget":50,"budget":100}\'::jsonb,2,1,1,\'active\',now(),now())',
        benefit_id,
        ids["tenant"],
        campaign_id,
    )
    # 直接落一条仍处 reserved 的旧链路 claim（绕过触发器，模拟历史数据）。
    async with owner.transaction():
        await owner.execute("ALTER TABLE public.benefit_claims DISABLE TRIGGER USER")
        try:
            await owner.execute(
                "INSERT INTO benefit_claims(id,tenant_id,benefit_id,campaign_id,consumer_id,idempotency_key,"
                "claim_type,status,delivery_status,reserved_amount,reservation_status,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,$5,$6,'claim','success','pending',50,'reserved',now(),now())",
                claim_id,
                ids["tenant"],
                benefit_id,
                campaign_id,
                consumer_ref,
                f"claim:v1:{uuid.uuid4().hex}",
            )
        finally:
            await owner.execute("ALTER TABLE public.benefit_claims ENABLE TRIGGER USER")

    control_url = migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
    engine = create_async_engine(control_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            from sqlalchemy import text

            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            assert await release_failed_delivery_reservation(session, ids["tenant"], claim_id) is True
            await session.commit()
        async with factory() as session:
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            assert await release_failed_delivery_reservation(session, ids["tenant"], claim_id) is False
            await session.commit()
    finally:
        await engine.dispose()

    row = await owner.fetchrow(
        "SELECT (SELECT stock_used FROM benefits WHERE tenant_id=$1 AND id=$2) AS stock_used, "
        "(SELECT (config_json->>'claimed_budget')::integer FROM benefits WHERE tenant_id=$1 AND id=$2) "
        "AS claimed_budget, "
        "(SELECT reservation_status='refunded' AND status='failed' AND delivery_status='failed' "
        "FROM benefit_claims WHERE tenant_id=$1 AND id=$3) AS claim_terminal",
        ids["tenant"],
        benefit_id,
        claim_id,
    )
    assert dict(row) == {"stock_used": 0, "claimed_budget": 0, "claim_terminal": True}
    await owner.close()
