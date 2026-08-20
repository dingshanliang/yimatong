from app.middleware.request_body_limit import (
    COMMERCE_EVENT_BODY_LIMIT,
    PUBLIC_CONSUMER_JSON_BODY_LIMIT,
    ConnectorRequestBodyLimitMiddleware,
)
from app.middleware.tenant import _is_commerce_public_path, _is_member_miniprogram_public_path


def _scope(path: str, method: str = "POST") -> dict:
    return {"type": "http", "method": method, "path": path}


def test_commerce_public_allowlist_is_exact() -> None:
    assert _is_commerce_public_path("/api/v1/consumers/membership/commerce-handoffs")
    assert _is_commerce_public_path("/api/v1/commerce/handoffs/redeem")
    assert _is_commerce_public_path("/api/v1/commerce/events")
    assert not _is_commerce_public_path("/api/v1/commerce/events/forged")
    assert not _is_commerce_public_path("/api/v1/commerce/connections")


def test_commerce_public_mutations_are_bounded_before_json_materialization() -> None:
    middleware = ConnectorRequestBodyLimitMiddleware
    assert middleware._limit_for_scope(_scope("/api/v1/commerce/events")) == COMMERCE_EVENT_BODY_LIMIT
    assert (
        middleware._limit_for_scope(_scope("/api/v1/consumers/membership/commerce-handoffs"))
        == PUBLIC_CONSUMER_JSON_BODY_LIMIT
    )
    assert middleware._limit_for_scope(_scope("/api/v1/commerce/handoffs/redeem")) == PUBLIC_CONSUMER_JSON_BODY_LIMIT
    assert middleware._limit_for_scope(_scope("/api/v1/commerce/events", "GET")) is None
    assert middleware._limit_for_scope(_scope("/api/v1/commerce/events/forged")) is None


def test_member_miniprogram_allowlist_and_body_limit_are_exact() -> None:
    middleware = ConnectorRequestBodyLimitMiddleware
    for path in (
        "/api/v1/consumers/membership/miniprogram-session",
        "/api/v1/consumers/membership/miniprogram-bind",
    ):
        assert _is_member_miniprogram_public_path(path)
        assert middleware._limit_for_scope(_scope(path)) == PUBLIC_CONSUMER_JSON_BODY_LIMIT
    assert not _is_member_miniprogram_public_path("/api/v1/consumers/membership/miniprogram-session/forged")
    assert middleware._limit_for_scope(_scope("/api/v1/consumers/membership/miniprogram-session/forged")) is None
