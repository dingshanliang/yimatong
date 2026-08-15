import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.services import wechat_oauth_authority as authority
from app.utils.crypto import decrypt_wechat_openid, hash_wechat_openid


class _Mappings:
    def __init__(self, row: dict):
        self.row = row

    def one(self):
        return self.row


class _Result:
    def __init__(self, row: dict):
        self.row = row

    def mappings(self):
        return _Mappings(self.row)


class _Session:
    def __init__(self, row: dict):
        self.execute = AsyncMock(return_value=_Result(row))
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@pytest.mark.anyio
async def test_oauth_binding_uses_callback_role_and_exact_subject_authority(monkeypatch):
    tenant_id = uuid.uuid4()
    consent_id = uuid.uuid4()
    scan_event_id = uuid.uuid4()
    consumer_id = uuid.uuid4()
    benefit_id = uuid.uuid4()
    session = _Session(
        {
            "outcome": "bound",
            "consumer_id": consumer_id,
            "consent_id": consent_id,
            "bound_at": datetime.now(UTC),
            "replayed": False,
        }
    )
    bind_tenant = AsyncMock()
    monkeypatch.setattr(authority, "callback_session_factory", lambda: session)
    monkeypatch.setattr(authority, "set_session_tenant_context", bind_tenant)

    result = await authority.bind_wechat_oauth_consumer_authority(
        tenant_id=tenant_id,
        consent_id=consent_id,
        scan_event_id=scan_event_id,
        scan_time=datetime.now(UTC),
        public_id="PUBLIC001",
        visitor_id="visitor-1",
        token_consumer_id=consumer_id,
        openid="openid-authoritative",
        benefit_id=benefit_id,
    )

    assert result["consumer_id"] == consumer_id
    bind_tenant.assert_awaited_once_with(session, tenant_id)
    statement, params = session.execute.await_args.args
    assert "bind_wechat_oauth_consumer" in str(statement)
    assert params["requested_consumer"] == consumer_id
    assert params["openid_hash"] == hash_wechat_openid(tenant_id, "openid-authoritative")
    assert params["openid_ciphertext"] != b"openid-authoritative"
    assert (
        decrypt_wechat_openid(
            tenant_id,
            consumer_id,
            params["openid_ciphertext"],
            params["openid_nonce"],
            params["openid_key_id"],
        )
        == "openid-authoritative"
    )
    assert params["benefit_id"] == benefit_id
    assert isinstance(params["audit_id"], uuid.UUID)
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_anonymous_oauth_replay_accepts_the_previously_bound_consumer(monkeypatch):
    tenant_id = uuid.uuid4()
    consent_id = uuid.uuid4()
    existing_consumer_id = uuid.uuid4()
    session = _Session(
        {
            "outcome": "bound",
            "consumer_id": existing_consumer_id,
            "consent_id": consent_id,
            "bound_at": datetime.now(UTC),
            "replayed": True,
        }
    )
    monkeypatch.setattr(authority, "callback_session_factory", lambda: session)
    monkeypatch.setattr(authority, "set_session_tenant_context", AsyncMock())

    result = await authority.bind_wechat_oauth_consumer_authority(
        tenant_id=tenant_id,
        consent_id=consent_id,
        scan_event_id=uuid.uuid4(),
        scan_time=datetime.now(UTC),
        public_id="PUBLIC001",
        visitor_id="visitor-1",
        token_consumer_id=None,
        openid="openid-authoritative",
        benefit_id=uuid.uuid4(),
    )

    assert result["consumer_id"] == existing_consumer_id
    session.commit.assert_awaited_once()
