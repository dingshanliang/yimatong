"""wechat_benefit_delivery consent 授予链路测试。

S4 迁移（a34cfb8027f0）补齐了该 purpose 的策略种子；本文件证明：
策略行存在 → 公开授予权威可用（ConsentRecord.scenario=purpose，与 worker 读取闭环）；
策略行缺失 → 404（此前的断链行为）。
"""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.consent import ConsentRecord, ConsumerConsentPolicy, ConsumerConsentPolicyCurrent
from app.models.scan import ScanEvent
from app.models.tenant import Tenant
from app.models.visitor import AnonymousVisitor
from app.services.consent import grant_consumer_consent_authority

_PURPOSE = "wechat_benefit_delivery"


async def _seed_scan_authority(db) -> tuple[uuid.UUID, uuid.UUID, datetime]:
    """grant 权威要求真实存在的扫码事件与访客（subject 绑定）。"""
    tenant_id = uuid.uuid4()
    db.add(Tenant(id=tenant_id, name="benefit-delivery tenant", slug=f"bd-{tenant_id.hex[:8]}"))
    visitor_id = f"visitor-{uuid.uuid4().hex[:12]}"
    scan_event_id = uuid.uuid4()
    scan_time = datetime.now(UTC).replace(microsecond=0)
    db.add(
        AnonymousVisitor(
            tenant_id=tenant_id,
            visitor_id=visitor_id,
            last_seen_at=scan_time,
        )
    )
    db.add(
        ScanEvent(
            id=scan_event_id,
            tenant_id=tenant_id,
            public_id="BD-CODE-1",
            visitor_id=visitor_id,
            scan_time=scan_time,
            is_valid_visit=True,
        )
    )
    await db.flush()
    return tenant_id, scan_event_id, scan_time


async def _seed_policy(db, tenant_id: uuid.UUID, *, purpose: str = _PURPOSE) -> tuple[str, str]:
    policy_id = uuid.uuid4()
    db.add(
        ConsumerConsentPolicy(
            id=policy_id,
            tenant_id=tenant_id,
            purpose=purpose,
            consent_type="data_share",
            policy_version="2026-09-15-v1",
            policy_digest="e" * 64,
            policy_title="外部权益发放授权",
            policy_content="测试策略内容。",
            effective_at=datetime.now(UTC),
        )
    )
    db.add(ConsumerConsentPolicyCurrent(tenant_id=tenant_id, purpose=purpose, policy_id=policy_id))
    await db.flush()
    return "2026-09-15-v1", "e" * 64


@pytest.mark.anyio
async def test_benefit_delivery_purpose_is_grantable_when_policy_seeded(db):
    tenant_id, scan_event_id, scan_time = await _seed_scan_authority(db)
    version, digest = await _seed_policy(db, tenant_id)

    result = await grant_consumer_consent_authority(
        db,
        tenant_id=tenant_id,
        purpose=_PURPOSE,
        expected_version=version,
        expected_digest=digest,
        scan_event_id=scan_event_id,
        scan_time=scan_time,
        public_id="BD-CODE-1",
        visitor_id=(
            await db.scalar(select(AnonymousVisitor.visitor_id).where(AnonymousVisitor.tenant_id == tenant_id))
        ),
        token_consumer_id=None,
        ip_hash="0" * 64,
        user_agent="pytest",
        idempotency_key=f"grant-{uuid.uuid4()}",
    )
    assert result["status"] == "granted"
    assert result["purpose"] == _PURPOSE
    assert result["replayed"] is False

    # scenario=purpose：与 campaign_claim_worker 读取的 scenario 严格一致
    record = await db.get(ConsentRecord, uuid.UUID(str(result["consent_id"])))
    assert record.scenario == "wechat_benefit_delivery"
    assert record.status == "granted"


@pytest.mark.anyio
async def test_benefit_delivery_grant_is_idempotent(db):
    tenant_id, scan_event_id, scan_time = await _seed_scan_authority(db)
    version, digest = await _seed_policy(db, tenant_id)
    visitor_id = await db.scalar(select(AnonymousVisitor.visitor_id).where(AnonymousVisitor.tenant_id == tenant_id))
    idempotency_key = f"grant-{uuid.uuid4()}"

    first = await grant_consumer_consent_authority(
        db,
        tenant_id=tenant_id,
        purpose=_PURPOSE,
        expected_version=version,
        expected_digest=digest,
        scan_event_id=scan_event_id,
        scan_time=scan_time,
        public_id="BD-CODE-1",
        visitor_id=visitor_id,
        token_consumer_id=None,
        ip_hash="0" * 64,
        user_agent="pytest",
        idempotency_key=idempotency_key,
    )
    replay = await grant_consumer_consent_authority(
        db,
        tenant_id=tenant_id,
        purpose=_PURPOSE,
        expected_version=version,
        expected_digest=digest,
        scan_event_id=scan_event_id,
        scan_time=scan_time,
        public_id="BD-CODE-1",
        visitor_id=visitor_id,
        token_consumer_id=None,
        ip_hash="0" * 64,
        user_agent="pytest",
        idempotency_key=idempotency_key,
    )
    assert replay["replayed"] is True
    assert replay["consent_id"] == first["consent_id"]


@pytest.mark.anyio
async def test_benefit_delivery_purpose_missing_policy_still_404(db):
    """策略未播种时授予被拒（迁移前的断链行为，作为种子的存在性证明）。"""
    tenant_id, scan_event_id, scan_time = await _seed_scan_authority(db)
    visitor_id = await db.scalar(select(AnonymousVisitor.visitor_id).where(AnonymousVisitor.tenant_id == tenant_id))

    with pytest.raises(HTTPException) as exc:
        await grant_consumer_consent_authority(
            db,
            tenant_id=tenant_id,
            purpose=_PURPOSE,
            expected_version="2026-09-15-v1",
            expected_digest="e" * 64,
            scan_event_id=scan_event_id,
            scan_time=scan_time,
            public_id="BD-CODE-1",
            visitor_id=visitor_id,
            token_consumer_id=None,
            ip_hash="0" * 64,
            user_agent="pytest",
            idempotency_key=f"grant-{uuid.uuid4()}",
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "consent_policy_not_found"
