"""YouzanAdapter 在 campaign claim worker 的接线行为（源码契约 + openid 注入功能路径）。"""

import inspect
import uuid

import pytest

import app.utils.youzan as youzan_protocol
from app.models.consent import ConsentRecord
from app.models.member import ConsumerProfile
from app.models.tenant import Tenant
from app.services.campaign_claim_worker import _process_leased, _resolve_consumer_openid
from app.services.connectors.youzan import YouzanAdapter


def test_worker_prepared_connector_and_hooks_are_wired():
    source = inspect.getsource(_process_leased)

    # prepare 钩子在 runtime 视图构造前执行（token 刷新回写，返回 transient 视图）
    assert "await adapter.prepare(db, connector)" in source
    assert source.index("await adapter.prepare(db, connector)") < source.index(
        "runtime_connector = connector_with_runtime_secrets(prepared_connector)"
    )
    # reconcile 分支对任意实现该方法的适配器开放（不再 isinstance GenericHttp）
    assert 'hasattr(adapter, "reconcile")' in source
    # 发放成功钩子失败不得回滚结算
    assert "Delivery success hook failed" in source
    # 终态失败标记外部券 error
    assert "mark_external_coupon_sync_error_by_claim" in source


def test_youzan_adapter_declares_openid_and_wallet_hooks():
    source = inspect.getsource(YouzanAdapter)
    assert YouzanAdapter.requires_openid is True
    assert "confirm_external_coupon_sync" in source
    assert "GRANT_TYPE_SILENT" in source  # 自用型静默兜底


async def _consumer_with_openid(db) -> tuple[uuid.UUID, uuid.UUID]:
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
    await db.flush()
    return tenant_id, consumer_id


@pytest.mark.anyio
async def test_resolve_openid_requires_scenario_scoped_consent(db):
    tenant_id, consumer_id = await _consumer_with_openid(db)

    # 未授权 → LookupError（走发放失败重试链，不静默使用 openid）
    with pytest.raises(LookupError):
        await _resolve_consumer_openid(db, tenant_id, str(consumer_id), scenario="wechat_benefit_delivery")

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
        await _resolve_consumer_openid(db, tenant_id, str(consumer_id), scenario="wechat_benefit_delivery")

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
    openid = await _resolve_consumer_openid(db, tenant_id, str(consumer_id), scenario="wechat_benefit_delivery")
    assert openid == "openid-secret-1"


@pytest.mark.anyio
async def test_resolve_openid_rejects_cross_tenant_consumer(db):
    tenant_id, consumer_id = await _consumer_with_openid(db)
    other_tenant = uuid.uuid4()

    with pytest.raises(LookupError):
        await _resolve_consumer_openid(db, other_tenant, str(consumer_id), scenario="wechat_benefit_delivery")


@pytest.mark.anyio
async def test_prepare_refreshes_token_in_tenant_scoped_session_and_returns_transient_view(db, monkeypatch):
    """prepare 的独立刷新会话必须设置租户上下文（生产 RLS 生效前提）并返回 transient 视图。"""

    import app.core.database as core_database
    from app.services.connectors.secrets import decrypt_secrets, encrypt_secrets
    from tests.conftest import TestSessionLocal

    tenant_id = uuid.uuid4()
    db.add(Tenant(id=tenant_id, name="prepare tenant", slug=f"prep-{tenant_id.hex[:8]}"))
    connector_id = uuid.uuid4()
    stale_secrets = encrypt_secrets(
        {
            "client_secret": "sec-1",
            "access_token": "stale",
            "refresh_token": "rt-old",
            "token_expires_at": "2020-01-01T00:00:00+00:00",
        }
    )
    from app.models.connector import Connector as ConnectorModel

    db.add(
        ConnectorModel(
            id=connector_id,
            tenant_id=tenant_id,
            name="prepare 连接器",
            connector_type="youzan",
            config={"client_id": "cid-1"},
            secrets_encrypted=stale_secrets,
            enabled=True,
        )
    )
    await db.commit()

    # prepare 用独立 session factory；重定向到同一 SQLite 测试库
    from sqlalchemy.ext.asyncio import async_sessionmaker

    test_factory = async_sessionmaker(TestSessionLocal.kw["bind"], expire_on_commit=False)

    context_calls = []
    real_set_context = core_database.set_session_tenant_context

    async def spy_set_context(session, tid):
        context_calls.append(tid)
        return await real_set_context(session, tid)

    async def fake_fetch_token(**kwargs):
        assert kwargs["client_id"] == "cid-1"
        if kwargs.get("grant_type") == "refresh_token":
            # 用 prepare 可捕获的传输层异常模拟 refresh 失败，触发静默兜底
            import httpx as httpx_mod

            raise httpx_mod.ConnectError("refresh refused in test")
        return {"access_token": "at-fresh", "expires_in": 604800}

    monkeypatch.setattr(core_database, "async_session_factory", test_factory)
    monkeypatch.setattr(core_database, "set_session_tenant_context", spy_set_context)
    monkeypatch.setattr(youzan_protocol, "fetch_token", fake_fetch_token)

    from sqlalchemy import select as sa_select

    connector = (await db.execute(sa_select(ConnectorModel).where(ConnectorModel.id == connector_id))).scalar_one()
    adapter = YouzanAdapter()
    view = await adapter.prepare(db, connector)

    # 独立会话设置了租户上下文（RLS 前提）
    assert context_calls == [tenant_id]
    # 返回 transient 视图携带新 token，且非传入 ORM 对象
    assert view is not connector
    assert view.config["access_token"] == "at-fresh"
    # 静默重取未返回 refresh_token 时旧值被清空，不回写死值
    persisted = decrypt_secrets(view.secrets_encrypted)
    assert persisted["access_token"] == "at-fresh"
    assert not persisted.get("refresh_token")
