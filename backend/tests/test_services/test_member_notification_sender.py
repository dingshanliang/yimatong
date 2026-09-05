import uuid

import pytest

from app.core.config import settings
from app.services.member_notification_sender import DeliverySnapshot, _send


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _WechatClient:
    def __init__(self, *, send_payload: dict[str, object], **_: object) -> None:
        self.send_payload = send_payload

    async def __aenter__(self) -> "_WechatClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, *_: object, **__: object) -> _Response:
        return _Response({"access_token": "access-token"})

    async def post(self, *_: object, **__: object) -> _Response:
        return _Response(self.send_payload)


def _snapshot() -> DeliverySnapshot:
    return DeliverySnapshot(
        tenant_id=uuid.uuid4(),
        delivery_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        openid="openid-1",
        template_id="template-1",
        page="/member/coupons",
        data={"thing1": {"value": "复购券即将到期"}},
    )


@pytest.mark.anyio
async def test_wechat_sender_classifies_success_and_permanent_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "shared_wechat_miniprogram_secret", "test-secret")
    monkeypatch.setattr(
        "app.services.member_notification_sender.httpx.AsyncClient",
        lambda **kwargs: _WechatClient(send_payload={"errcode": 0, "msgid": "wx-message-1"}, **kwargs),
    )
    assert await _send(_snapshot()) == ("accepted", "wx-message-1", None)

    monkeypatch.setattr(
        "app.services.member_notification_sender.httpx.AsyncClient",
        lambda **kwargs: _WechatClient(send_payload={"errcode": 43101}, **kwargs),
    )
    assert await _send(_snapshot()) == ("permanent_failure", None, "wechat_43101")


@pytest.mark.anyio
async def test_wechat_sender_does_not_attempt_without_verified_recipient() -> None:
    snapshot = _snapshot()
    unavailable = DeliverySnapshot(
        tenant_id=snapshot.tenant_id,
        delivery_id=snapshot.delivery_id,
        lease_token=snapshot.lease_token,
        openid="",
        template_id=snapshot.template_id,
        page=snapshot.page,
        data=snapshot.data,
    )
    assert await _send(unavailable) == ("permanent_failure", None, "wechat_recipient_unavailable")
