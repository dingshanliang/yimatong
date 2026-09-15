"""WeimobAdapter 在 campaign claim worker 的接线行为（源码契约 + 手机号注入功能路径）。"""

import inspect
import uuid

import pytest

import app.utils.weimob as weimob_protocol
from app.models.consent import ConsentRecord
from app.models.member import ConsumerProfile
from app.models.tenant import Tenant
from app.services.campaign_claim_worker import _process_leased, _resolve_consumer_openid, _resolve_consumer_phone
from app.services.connectors.weimob import WeimobAdapter


def test_worker_wires_phone_identity_branch_and_hooks():
    source = inspect.getsource(_process_leased)

    # prepare 钩子在 runtime 视图构造前执行（token 重取回写，返回 transient 视图）
    assert "await adapter.prepare(db, connector)" in source
    assert source.index("await adapter.prepare(db, connector)") < source.index(
        "runtime_connector = connector_with_runtime_secrets(prepared_connector)"
    )
    # reconcile 分支对任意实现该方法的适配器开放
    assert 'hasattr(adapter, "reconcile")' in source
    # 发放成功钩子失败不得回滚结算
    assert "Delivery success hook failed" in source
    # 终态失败标记外部券 error
    assert "mark_external_coupon_sync_error_by_claim" in source
    # 手机号身份分支与 openid 分支并列，且同样走 wechat_benefit_delivery 场景
    assert 'getattr(adapter, "requires_phone", False)' in source
    phone_branch = source[source.index('getattr(adapter, "requires_phone", False)') :][:600]
    assert 'scenario="wechat_benefit_delivery"' in phone_branch
    assert "_resolve_consumer_phone" in phone_branch


def test_weimob_adapter_declares_phone_and_wallet_hooks():
    source = inspect.getsource(WeimobAdapter)
    assert WeimobAdapter.requires_phone is True
    assert not getattr(WeimobAdapter, "requires_openid", False)
    assert "confirm_external_coupon_sync" in source
    assert "CUSTOMER_IMPORT_PATH" in source  # 手机号换 wid 身份桥
    assert "callback_ack_payload" in source  # 微盟 ACK 契约


async def _consumer_with_phone(db, *, suppressed: bool = False) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    db.add(Tenant(id=tenant_id, name="phone tenant", slug=f"phone-{tenant_id.hex[:8]}"))
    consumer_id = uuid.uuid4()
    from app.utils.crypto import encrypt_consumer_phone

    ciphertext, nonce, key_id = encrypt_consumer_phone(tenant_id, consumer_id, "13800138000")
    db.add(
        ConsumerProfile(
            id=consumer_id,
            tenant_id=tenant_id,
            phone_ciphertext=ciphertext,
            phone_nonce=nonce,
            phone_key_id=key_id,
            lead_contact_suppressed=suppressed,
        )
    )
    await db.flush()
    return tenant_id, consumer_id


@pytest.mark.anyio
async def test_resolve_phone_requires_scenario_scoped_consent(db):
    tenant_id, consumer_id = await _consumer_with_phone(db)

    # 未授权 → LookupError（走发放失败重试链，不静默使用手机号）
    with pytest.raises(LookupError):
        await _resolve_consumer_phone(db, tenant_id, str(consumer_id), scenario="wechat_benefit_delivery")

    # 现金场景的同意不能跨界用于权益发放（场景隔离回归保护）
    db.add(
        ConsentRecord(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            consumer_id=consumer_id,
            consent_type="privacy",
            scenario="wechat_cash_payout",
            status="granted",
        )
    )
    await db.flush()
    with pytest.raises(LookupError):
        await _resolve_consumer_phone(db, tenant_id, str(consumer_id), scenario="wechat_benefit_delivery")

    # 权益场景授权后才可用
    db.add(
        ConsentRecord(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            consumer_id=consumer_id,
            consent_type="privacy",
            scenario="wechat_benefit_delivery",
            status="granted",
        )
    )
    await db.flush()
    phone = await _resolve_consumer_phone(db, tenant_id, str(consumer_id), scenario="wechat_benefit_delivery")
    assert phone == "13800138000"


@pytest.mark.anyio
async def test_resolve_phone_rejects_cross_tenant_and_suppressed(db):
    tenant_id, consumer_id = await _consumer_with_phone(db)
    other_tenant = uuid.uuid4()

    with pytest.raises(LookupError):
        await _resolve_consumer_phone(db, other_tenant, str(consumer_id), scenario="wechat_benefit_delivery")

    # lead_contact_suppressed（隐私治理抑制）视同不可用
    suppressed_tenant, suppressed_consumer = await _consumer_with_phone(db, suppressed=True)
    db.add(
        ConsentRecord(
            id=uuid.uuid4(),
            tenant_id=suppressed_tenant,
            consumer_id=suppressed_consumer,
            consent_type="privacy",
            scenario="wechat_benefit_delivery",
            status="granted",
        )
    )
    await db.flush()
    with pytest.raises(LookupError):
        await _resolve_consumer_phone(
            db, suppressed_tenant, str(suppressed_consumer), scenario="wechat_benefit_delivery"
        )


@pytest.mark.anyio
async def test_resolve_phone_missing_envelope_and_bad_id(db):
    tenant_id = uuid.uuid4()
    db.add(Tenant(id=tenant_id, name="empty tenant", slug=f"empty-{tenant_id.hex[:8]}"))
    consumer_id = uuid.uuid4()
    db.add(ConsumerProfile(id=consumer_id, tenant_id=tenant_id))
    await db.flush()

    # 无手机号信封 → LookupError
    with pytest.raises(LookupError):
        await _resolve_consumer_phone(db, tenant_id, str(consumer_id), scenario="wechat_benefit_delivery")
    # 非法 ID → consumer_profile_missing
    with pytest.raises(LookupError) as exc:
        await _resolve_consumer_phone(db, tenant_id, "not-a-uuid", scenario="wechat_benefit_delivery")
    assert str(exc.value) == "consumer_profile_missing"


@pytest.mark.anyio
async def test_openid_resolution_still_works_after_consent_core_refactor(db):
    """openid 解析与手机号解析共用同意校验核心，重构后行为不回归。"""
    tenant_id = uuid.uuid4()
    db.add(Tenant(id=tenant_id, name="openid tenant", slug=f"openid-{tenant_id.hex[:8]}"))
    consumer_id = uuid.uuid4()
    from app.utils.crypto import encrypt_wechat_openid

    ciphertext, nonce, key_id = encrypt_wechat_openid(tenant_id, consumer_id, "openid-secret-1")
    db.add(
        ConsumerProfile(
            id=consumer_id,
            tenant_id=tenant_id,
            wechat_openid_ciphertext=ciphertext,
            wechat_openid_nonce=nonce,
            wechat_openid_key_id=key_id,
        )
    )
    db.add(
        ConsentRecord(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            consumer_id=consumer_id,
            consent_type="privacy",
            scenario="wechat_benefit_delivery",
            status="granted",
        )
    )
    await db.flush()
    openid = await _resolve_consumer_openid(db, tenant_id, str(consumer_id), scenario="wechat_benefit_delivery")
    assert openid == "openid-secret-1"


@pytest.mark.anyio
async def test_prepare_reissues_token_in_tenant_scoped_session_and_returns_transient_view(db, monkeypatch):
    """prepare 的独立重取会话必须设置租户上下文（生产 RLS 生效前提）并返回 transient 视图。"""

    import app.core.database as core_database
    from app.services.connectors.secrets import decrypt_secrets, encrypt_secrets
    from tests.conftest import TestSessionLocal

    tenant_id = uuid.uuid4()
    db.add(Tenant(id=tenant_id, name="prepare tenant", slug=f"wm-prep-{tenant_id.hex[:8]}"))
    connector_id = uuid.uuid4()
    stale_secrets = encrypt_secrets(
        {
            "client_secret": "sec-1",
            "access_token": "stale",
            "token_expires_at": "2020-01-01T00:00:00+00:00",
        }
    )
    from app.models.connector import Connector as ConnectorModel

    db.add(
        ConnectorModel(
            id=connector_id,
            tenant_id=tenant_id,
            name="prepare 连接器",
            connector_type="weimob",
            config={"client_id": "cid-1", "shop_id": "shop-1", "shop_type": "public_account_id"},
            secrets_encrypted=stale_secrets,
            enabled=True,
        )
    )
    await db.commit()

    from sqlalchemy.ext.asyncio import async_sessionmaker

    test_factory = async_sessionmaker(TestSessionLocal.kw["bind"], expire_on_commit=False)

    context_calls = []
    real_set_context = core_database.set_session_tenant_context

    async def spy_set_context(session, tid):
        context_calls.append(tid)
        return await real_set_context(session, tid)

    async def fake_fetch_token(**kwargs):
        # client_credentials 单一模式：凭据 + 店铺四元组
        assert kwargs["client_id"] == "cid-1"
        assert kwargs["client_secret"] == "sec-1"
        assert kwargs["shop_id"] == "shop-1"
        assert kwargs["shop_type"] == "public_account_id"
        return {"access_token": "at-fresh", "expires_in": 604799}

    monkeypatch.setattr(core_database, "async_session_factory", test_factory)
    monkeypatch.setattr(core_database, "set_session_tenant_context", spy_set_context)
    monkeypatch.setattr(weimob_protocol, "fetch_token", fake_fetch_token)

    from sqlalchemy import select as sa_select

    connector = (await db.execute(sa_select(ConnectorModel).where(ConnectorModel.id == connector_id))).scalar_one()
    adapter = WeimobAdapter()
    view = await adapter.prepare(db, connector)

    # 独立会话设置了租户上下文（RLS 前提）
    assert context_calls == [tenant_id]
    # 返回 transient 视图携带新 token，且非传入 ORM 对象（不弄脏持久化状态）
    assert view is not connector
    assert view.config["access_token"] == "at-fresh"
    persisted = decrypt_secrets(view.secrets_encrypted)
    assert persisted["access_token"] == "at-fresh"
    assert persisted["token_refreshed_at"]
