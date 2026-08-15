import hashlib
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.services import wecom_callback_authority


def _event(**overrides) -> dict:
    return {
        "Event": "change_external_contact",
        "ChangeType": "add_external_contact",
        "ExternalUserID": "external-1",
        "UserID": "staff-1",
        "State": "campaign-state",
        "UnionID": "union-1",
        "CreateTime": 1_700_000_000,
        "Sequence": 2,
        **overrides,
    }


def test_lock_contention_maps_to_exact_retryable_http_contract():
    original = MagicMock(sqlstate="55P03", pgcode="55P03")
    error = MagicMock(orig=original)

    mapped = wecom_callback_authority._map_callback_error(error)

    assert mapped is not None
    assert mapped.status_code == 409
    assert mapped.detail == "Enterprise WeChat callback is busy"
    assert mapped.headers == {"Retry-After": "1"}


@pytest.mark.anyio
async def test_verified_event_uses_callback_authority_with_canonical_digest(monkeypatch):
    tenant_id = uuid.uuid4()
    connector_id = uuid.uuid4()
    event = _event()
    row = {
        "receipt_id": uuid.uuid4(),
        "outcome": "recorded",
        "contact_id": uuid.uuid4(),
        "replayed": False,
    }
    execute_result = MagicMock()
    execute_result.mappings.return_value.one.return_value = row
    callback_db = AsyncMock()
    callback_db.execute.return_value = execute_result
    context = AsyncMock()
    context.__aenter__.return_value = callback_db
    factory = MagicMock(return_value=context)
    set_context = AsyncMock()
    monkeypatch.setattr(wecom_callback_authority, "callback_session_factory", factory)
    monkeypatch.setattr(wecom_callback_authority, "set_session_tenant_context", set_context)

    result = await wecom_callback_authority.apply_verified_wecom_contact_event(
        tenant_id=tenant_id,
        connector_id=connector_id,
        event=event,
    )

    assert result["status"] == "recorded"
    set_context.assert_awaited_once_with(callback_db, tenant_id)
    params = callback_db.execute.await_args.args[1]
    expected = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert params["payload_digest"] == hashlib.sha256(expected.encode()).hexdigest()
    assert params["tenant_id"] == tenant_id
    assert params["connector_id"] == connector_id
    assert params["change_type"] == "add_external_contact"
    assert params["user_id"] == "staff-1"
    callback_db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_exact_replay_is_reported_as_duplicate(monkeypatch):
    execute_result = MagicMock()
    execute_result.mappings.return_value.one.return_value = {
        "outcome": "recorded",
        "replayed": True,
    }
    callback_db = AsyncMock()
    callback_db.execute.return_value = execute_result
    context = AsyncMock()
    context.__aenter__.return_value = callback_db
    monkeypatch.setattr(wecom_callback_authority, "callback_session_factory", MagicMock(return_value=context))
    monkeypatch.setattr(wecom_callback_authority, "set_session_tenant_context", AsyncMock())

    result = await wecom_callback_authority.apply_verified_wecom_contact_event(
        tenant_id=uuid.uuid4(),
        connector_id=uuid.uuid4(),
        event=_event(),
    )

    assert result["status"] == "duplicate"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("event", "reason"),
    [
        (_event(Event="change_external_chat"), "unsupported_event"),
        (_event(ChangeType="unknown"), "unsupported_change_type"),
        (_event(ExternalUserID=""), "missing_external_userid"),
        (_event(CreateTime="bad"), "invalid_event_order"),
    ],
)
async def test_unsupported_or_invalid_event_never_opens_callback_session(monkeypatch, event: dict, reason: str):
    factory = MagicMock()
    monkeypatch.setattr(wecom_callback_authority, "callback_session_factory", factory)

    result = await wecom_callback_authority.apply_verified_wecom_contact_event(
        tenant_id=uuid.uuid4(),
        connector_id=uuid.uuid4(),
        event=event,
    )

    assert result == {"status": "ignored", "reason": reason}
    factory.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("user_id", [None, "", "   "])
async def test_supported_official_event_requires_user_id_before_callback_session(monkeypatch, user_id):
    factory = MagicMock()
    monkeypatch.setattr(wecom_callback_authority, "callback_session_factory", factory)

    with pytest.raises(HTTPException) as exc_info:
        await wecom_callback_authority.apply_verified_wecom_contact_event(
            tenant_id=uuid.uuid4(),
            connector_id=uuid.uuid4(),
            event=_event(UserID=user_id),
        )

    assert exc_info.value.status_code == 422
    factory.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("change_type", ["del_external_contact", "del_follow_user"])
async def test_supported_delete_forwards_missing_state_to_member_authority(monkeypatch, change_type: str):
    execute_result = MagicMock()
    execute_result.mappings.return_value.one.return_value = {
        "outcome": "recorded",
        "replayed": False,
    }
    callback_db = AsyncMock()
    callback_db.execute.return_value = execute_result
    context = AsyncMock()
    context.__aenter__.return_value = callback_db
    monkeypatch.setattr(wecom_callback_authority, "callback_session_factory", MagicMock(return_value=context))
    monkeypatch.setattr(wecom_callback_authority, "set_session_tenant_context", AsyncMock())

    result = await wecom_callback_authority.apply_verified_wecom_contact_event(
        tenant_id=uuid.uuid4(),
        connector_id=uuid.uuid4(),
        event=_event(ChangeType=change_type, State=None),
    )

    assert result["status"] == "recorded"
    assert callback_db.execute.await_args.args[1]["state"] is None
