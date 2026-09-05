from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.services.wechat_miniprogram import exchange_miniprogram_code


@pytest.mark.anyio
async def test_code_exchange_rejects_missing_shared_app_configuration(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.services.wechat_miniprogram.settings.shared_wechat_miniprogram_appid", "")
    monkeypatch.setattr("app.services.wechat_miniprogram.settings.shared_wechat_miniprogram_secret", "")

    with pytest.raises(HTTPException) as exc_info:
        await exchange_miniprogram_code("code")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "shared_miniprogram_not_configured"


@pytest.mark.anyio
async def test_code_exchange_returns_only_verified_issuer_and_subject(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.services.wechat_miniprogram.settings.shared_wechat_miniprogram_appid", "shared-appid")
    monkeypatch.setattr("app.services.wechat_miniprogram.settings.shared_wechat_miniprogram_secret", "shared-secret")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"openid": "openid-1", "session_key": "must-not-escape"}
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = response

    with patch("app.services.wechat_miniprogram.httpx.AsyncClient", return_value=client):
        identity = await exchange_miniprogram_code("one-time-code")

    assert identity.issuer == "shared-appid"
    assert identity.openid == "openid-1"
    assert not hasattr(identity, "session_key")
    assert client.get.await_args.kwargs["params"]["secret"] == "shared-secret"


@pytest.mark.anyio
async def test_code_exchange_maps_provider_transport_failure_to_stable_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.services.wechat_miniprogram.settings.shared_wechat_miniprogram_appid", "shared-appid")
    monkeypatch.setattr("app.services.wechat_miniprogram.settings.shared_wechat_miniprogram_secret", "shared-secret")
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.side_effect = httpx.ConnectError("offline")

    with (
        patch("app.services.wechat_miniprogram.httpx.AsyncClient", return_value=client),
        pytest.raises(HTTPException) as exc_info,
    ):
        await exchange_miniprogram_code("one-time-code")

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "wechat_identity_unavailable"
