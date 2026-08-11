"""红包领取流程专项测试（claim_red_packet + 失败补偿）。

之前红包领取几乎没测试（仅 1 个 benefit-type 校验测试）。这里用 fake adapter
覆盖四种转账结果：
- success → claim=delivered，预算扣减保留
- failed  → 预算/库存加回去，claim=failed，抛 RedPacketTransferFailed
- pending → claim=claimed，delivery=pending，预算保留（交给重试队列）
- 库存/预算耗尽 → 在扣减阶段就抛 RuntimeError，不进转账

注意 config_json 里的 claimed_budget/budget：SQLite 不支持 jsonb_set，因此
_claim_red_packet 的乐观锁 SQL 在 PG 之外不可直接跑。本测试把 _DEDUCT 和
_REFUND 两条 raw SQL 替换成等价的 Python 内存扣减（monkeypatch），专注于
补偿逻辑（状态流转、抛错、回滚）而不是 PG 专属 SQL 语法。
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Benefit, BenefitClaim, Campaign
from app.models.connector import BenefitDelivery, Connector
from app.models.member import ConsumerProfile
from app.models.tenant import Tenant
from app.services import redpacket as rp_module
from app.services.connectors import registry as registry_module
from app.services.connectors.base import BaseConnectorAdapter, DeliveryResult
from app.services.redpacket import RedPacketTransferFailed, claim_red_packet
from tests.conftest import TestSessionLocal


class _FakeAdapter(BaseConnectorAdapter):
    """可控的转账适配器：按 outcome 返回 success/pending/failed。"""

    outcome = "success"
    message = ""

    async def sync_stock(self, connector):
        return 0

    async def deliver(self, connector, consumer_id, benefit_config):
        return DeliveryResult(status=self.outcome, message=self.message, external_id="fake-bill")

    async def parse_callback(self, connector, request_body, headers):
        from app.services.connectors.base import CallbackResult

        return CallbackResult()

    async def validate_config(self, config):
        return True, ""

    async def verify_callback(self, connector, request_body, headers):
        return False


FAKE_TYPE = "fake_redpacket_test"


def _set_adapter_outcome(monkeypatch, outcome, message=""):
    _FakeAdapter.outcome = outcome
    _FakeAdapter.message = message
    monkeypatch.setitem(registry_module._registry, FAKE_TYPE, _FakeAdapter)


async def _make_world(db: AsyncSession, *, budget=1000, claimed=0, stock_total=10, stock_used=0):
    """构造一套红包权益数据；返回关键 id。"""
    tenant_id = uuid.uuid4()
    db.add(Tenant(id=tenant_id, name="红包测试租户", slug=f"rp-{tenant_id.hex[:8]}"))
    await db.flush()
    consumer = ConsumerProfile(tenant_id=tenant_id)
    db.add(consumer)
    campaign = Campaign(
        tenant_id=tenant_id,
        name="红包活动",
        campaign_type="cash_red_packet",
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        end_at=datetime(2027, 12, 31, 23, 59, 59, tzinfo=UTC),
        rules_json={},
    )
    db.add(campaign)
    await db.flush()
    benefit = Benefit(
        tenant_id=tenant_id,
        campaign_id=campaign.id,
        name="现金红包",
        benefit_type="cash_red_packet",
        config_json={
            "amount_type": "fixed",
            "fixed_amount": 100,
            "budget": budget,
            "claimed_budget": claimed,
            "transfer_remark": "测试红包",
        },
        stock_total=stock_total,
        stock_used=stock_used,
        per_person_limit=10,
        status="active",
    )
    db.add(benefit)
    await db.flush()
    connector = Connector(
        tenant_id=tenant_id,
        name="微信支付",
        connector_type=FAKE_TYPE,
        enabled=True,
        config={},
    )
    db.add(connector)
    await db.commit()
    return {
        "tenant_id": tenant_id,
        "consumer_id": str(consumer.id),
        "benefit_id": benefit.id,
        "connector_id": connector.id,
    }


@pytest.fixture(autouse=True)
def _patch_budget_sql(monkeypatch):
    """把 PG 专属的 jsonb_set SQL 换成 Python 内存扣减，让 SQLite 也能跑补偿逻辑。"""

    async def _fake_deduct(db, benefit_id, amount):
        from sqlalchemy.orm.attributes import flag_modified

        ben = await db.get(Benefit, benefit_id)
        cfg = dict(ben.config_json or {})
        if ben.stock_used >= ben.stock_total:
            return 0
        if cfg.get("claimed_budget", 0) + amount > cfg.get("budget", 0):
            return 0
        cfg["claimed_budget"] = cfg.get("claimed_budget", 0) + amount
        ben.config_json = cfg
        flag_modified(ben, "config_json")
        ben.stock_used = ben.stock_used + 1
        return 1

    async def _fake_refund(db, benefit_id, amount):
        from sqlalchemy.orm.attributes import flag_modified

        ben = await db.get(Benefit, benefit_id)
        if ben.stock_used <= 0:
            return 0
        cfg = dict(ben.config_json or {})
        cfg["claimed_budget"] = max(cfg.get("claimed_budget", 0) - amount, 0)
        ben.config_json = cfg
        flag_modified(ben, "config_json")
        ben.stock_used = ben.stock_used - 1
        return 1

    monkeypatch.setattr(rp_module, "deduct_redpacket_budget", _fake_deduct)
    monkeypatch.setattr(rp_module, "refund_redpacket_budget", _fake_refund)


@pytest.mark.anyio
async def test_claim_success_marks_delivered():
    async with TestSessionLocal() as db:
        ids = await _make_world(db)
        _FakeAdapter.outcome = "success"
        registry_module._registry[FAKE_TYPE] = _FakeAdapter
        result = await claim_red_packet(
            db=db,
            benefit_id=ids["benefit_id"],
            tenant_id=ids["tenant_id"],
            connector_id=ids["connector_id"],
            consumer_id=ids["consumer_id"],
            openid="openid_x",
            total_count=0,
        )
        await db.commit()

    assert result["status"] == "success"
    async with TestSessionLocal() as db:
        claim = (
            await db.execute(BenefitClaim.__table__.select().where(BenefitClaim.consumer_id == ids["consumer_id"]))
        ).first()
        assert claim.status == "delivered"
        ben = await db.get(Benefit, ids["benefit_id"])
        assert ben.stock_used == 1
        assert ben.config_json["claimed_budget"] == 100  # 扣了 100


@pytest.mark.anyio
async def test_claim_failed_refunds_budget_and_marks_failed():
    async with TestSessionLocal() as db:
        ids = await _make_world(db)
        _FakeAdapter.outcome = "failed"
        _FakeAdapter.message = "INSUFFICIENT_FUNDS"
        registry_module._registry[FAKE_TYPE] = _FakeAdapter
        with pytest.raises(RedPacketTransferFailed, match="INSUFFICIENT_FUNDS"):
            await claim_red_packet(
                db=db,
                benefit_id=ids["benefit_id"],
                tenant_id=ids["tenant_id"],
                connector_id=ids["connector_id"],
                consumer_id=ids["consumer_id"],
                openid="openid_x",
                total_count=0,
            )
        # 模拟调用方 _handle_cash_red_packet_claim：失败时 commit 持久化补偿 + failed claim。
        await db.commit()

    async with TestSessionLocal() as db:
        ben = await db.get(Benefit, ids["benefit_id"])
        # 关键断言：失败后预算和库存都加回去了（没有泄漏）
        assert ben.stock_used == 0
        assert ben.config_json["claimed_budget"] == 0
        claim_row = (
            await db.execute(BenefitClaim.__table__.select().where(BenefitClaim.consumer_id == ids["consumer_id"]))
        ).first()
        assert claim_row.status == "failed"
        delivery = (
            await db.execute(
                BenefitDelivery.__table__.select().where(BenefitDelivery.consumer_id == ids["consumer_id"])
            )
        ).first()
        assert delivery.status == "failed"


@pytest.mark.anyio
async def test_claim_pending_keeps_budget_and_delivery_pending():
    async with TestSessionLocal() as db:
        ids = await _make_world(db)
        _FakeAdapter.outcome = "pending"
        _FakeAdapter.message = "timeout"
        registry_module._registry[FAKE_TYPE] = _FakeAdapter
        result = await claim_red_packet(
            db=db,
            benefit_id=ids["benefit_id"],
            tenant_id=ids["tenant_id"],
            connector_id=ids["connector_id"],
            consumer_id=ids["consumer_id"],
            openid="openid_x",
            total_count=0,
        )
        await db.commit()

    assert result["status"] == "pending"
    async with TestSessionLocal() as db:
        ben = await db.get(Benefit, ids["benefit_id"])
        # pending：预算保留扣减，交给重试队列
        assert ben.stock_used == 1
        assert ben.config_json["claimed_budget"] == 100
        claim_row = (
            await db.execute(BenefitClaim.__table__.select().where(BenefitClaim.consumer_id == ids["consumer_id"]))
        ).first()
        assert claim_row.status == "claimed"


@pytest.mark.anyio
async def test_claim_out_of_stock_raises_before_transfer():
    async with TestSessionLocal() as db:
        ids = await _make_world(db, stock_total=1, stock_used=1)
        registry_module._registry[FAKE_TYPE] = _FakeAdapter
        with pytest.raises(RuntimeError, match="红包已抢光"):
            await claim_red_packet(
                db=db,
                benefit_id=ids["benefit_id"],
                tenant_id=ids["tenant_id"],
                connector_id=ids["connector_id"],
                consumer_id=ids["consumer_id"],
                openid="openid_x",
                total_count=0,
            )


@pytest.mark.anyio
async def test_claim_budget_exhausted_raises_before_transfer():
    async with TestSessionLocal() as db:
        # 预算已被先前领取耗尽（claimed == budget），remaining=0，扣减前就抛错。
        ids = await _make_world(db, budget=100, claimed=100)
        registry_module._registry[FAKE_TYPE] = _FakeAdapter
        with pytest.raises(RuntimeError, match="红包预算已用尽"):
            await claim_red_packet(
                db=db,
                benefit_id=ids["benefit_id"],
                tenant_id=ids["tenant_id"],
                connector_id=ids["connector_id"],
                consumer_id=ids["consumer_id"],
                openid="openid_x",
                total_count=0,
            )
