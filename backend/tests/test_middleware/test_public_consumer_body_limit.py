import pytest

from app.middleware.request_body_limit import (
    CHANNEL_JSON_BODY_LIMIT,
    PUBLIC_CONSUMER_JSON_BODY_LIMIT,
    RISK_DIVERSION_JSON_BODY_LIMIT,
    WECOM_CALLBACK_BODY_LIMIT,
    ConnectorRequestBodyLimitMiddleware,
)


async def _run_request(
    path: str,
    chunks: list[bytes],
    content_length: bytes | None = None,
    *,
    method: str = "POST",
):
    called = False
    sent: list[dict] = []

    async def app(_scope, _receive, send):
        nonlocal called
        called = True
        request = await _receive()
        received_length = str(len(request.get("body", b""))).encode()
        await send(
            {"type": "http.response.start", "status": 204, "headers": [(b"x-received-body-length", received_length)]}
        )
        await send({"type": "http.response.body", "body": b""})

    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    headers = [] if content_length is None else [(b"content-length", content_length)]
    scope = {"type": "http", "method": method, "path": path, "headers": headers}
    await ConnectorRequestBodyLimitMiddleware(app)(scope, receive, send)
    return called, sent


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/benefit-claims",
        "/api/v1/public/benefits/018f0f65-7ad4-7cc4-b874-57d93b10ab12/claim",
        "/api/v1/public/consents",
        "/api/v1/public/consents/018f0f65-7ad4-7cc4-b874-57d93b10ab12/withdraw",
        "/api/v1/consumers/lead-capture",
        "/api/v1/public/leads",
    ],
)
async def test_public_consumer_mutations_reject_declared_body_over_16_kib_before_app(path: str):
    called, sent = await _run_request(path, [b"{}"], str(PUBLIC_CONSUMER_JSON_BODY_LIMIT + 1).encode())

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/benefit-claims",
        "/api/v1/public/benefits/018f0f65-7ad4-7cc4-b874-57d93b10ab12/claim",
        "/api/v1/consumers/lead-capture",
    ],
)
async def test_public_consumer_mutation_rejects_chunked_body_over_16_kib_before_app(path: str):
    called, sent = await _run_request(
        path,
        [b"a" * 10_000, b"b" * 6_385],
    )

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.anyio
async def test_public_benefit_claim_alias_allows_exact_16_kib_boundary():
    path = "/api/v1/public/benefits/018f0f65-7ad4-7cc4-b874-57d93b10ab12/claim"
    called, sent = await _run_request(path, [b"a" * PUBLIC_CONSUMER_JSON_BODY_LIMIT])

    assert called is True
    assert sent[0]["status"] == 204


@pytest.mark.anyio
async def test_unrelated_public_path_is_not_added_to_consumer_body_bypass_contract():
    called, sent = await _run_request("/api/v1/public/unrelated", [b"a" * 20_000])

    assert called is True
    assert sent[0]["status"] == 204


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/public/benefits/claim",
        "/api/v1/public/benefits/id/claim/extra",
        "/api/v1/public/other/id/claim",
    ],
)
def test_public_benefit_claim_matcher_does_not_broaden_the_public_shape(path: str):
    from app.middleware.request_body_limit import is_public_benefit_claim_path

    assert is_public_benefit_claim_path(path) is False


@pytest.mark.anyio
async def test_channel_mutation_rejects_declared_body_before_json_parsing():
    called, sent = await _run_request(
        "/api/v1/channels/distributors",
        [b"{}"],
        str(CHANNEL_JSON_BODY_LIMIT + 1).encode(),
    )

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.anyio
async def test_channel_mutation_rejects_chunked_body_before_json_parsing():
    called, sent = await _run_request(
        "/api/v1/channels/code-allocations",
        [b"a" * 40_000, b"b" * 30_000],
    )

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["evidence", "transition"])
async def test_diversion_mutation_rejects_declared_body_over_64_kib_before_handler_or_db(action: str):
    path = f"/api/v1/risk-dashboard/diversion-clues/018f0f65-7ad4-7cc4-b874-57d93b10ab12/{action}"
    called, sent = await _run_request(path, [b"{}"], str(RISK_DIVERSION_JSON_BODY_LIMIT + 1).encode())

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["evidence", "transition"])
async def test_diversion_mutation_rejects_chunked_body_over_64_kib_before_handler_or_db(action: str):
    path = f"/api/v1/risk-dashboard/diversion-clues/018f0f65-7ad4-7cc4-b874-57d93b10ab12/{action}"
    called, sent = await _run_request(path, [b"a" * 40_000, b"b" * (RISK_DIVERSION_JSON_BODY_LIMIT - 39_999)])

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["evidence", "transition"])
async def test_diversion_mutation_allows_body_at_exact_64_kib_boundary(action: str):
    path = f"/api/v1/risk-dashboard/diversion-clues/018f0f65-7ad4-7cc4-b874-57d93b10ab12/{action}"
    called, sent = await _run_request(path, [b"a" * RISK_DIVERSION_JSON_BODY_LIMIT])

    assert called is True
    assert sent[0]["status"] == 204
    assert sent[0]["headers"] == [(b"x-received-body-length", str(RISK_DIVERSION_JSON_BODY_LIMIT).encode())]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/risk-dashboard/diversion-clues",
        "/api/v1/risk-dashboard/diversion-clues/018f0f65-7ad4-7cc4-b874-57d93b10ab12/history",
        "/api/v1/risk-dashboard/alerts/ticket",
    ],
)
async def test_other_risk_dashboard_posts_are_not_added_to_diversion_body_cap(path: str):
    called, sent = await _run_request(path, [b"a" * (RISK_DIVERSION_JSON_BODY_LIMIT + 1)])

    assert called is True
    assert sent[0]["status"] == 204


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["GET", "PATCH"])
async def test_diversion_body_cap_is_post_only(method: str):
    path = "/api/v1/risk-dashboard/diversion-clues/018f0f65-7ad4-7cc4-b874-57d93b10ab12/evidence"
    called, sent = await _run_request(
        path,
        [b"a" * (RISK_DIVERSION_JSON_BODY_LIMIT + 1)],
        method=method,
    )

    assert called is True
    assert sent[0]["status"] == 204


@pytest.mark.anyio
@pytest.mark.parametrize("connector_id", ["018f0f65-7ad4-7cc4-b874-57d93b10ab12", "not-a-uuid"])
async def test_wecom_callback_rejects_declared_body_over_64_kib_before_app(connector_id: str):
    called, sent = await _run_request(
        f"/api/v1/integrations/wecom/callback/{connector_id}",
        [b"{}"],
        str(WECOM_CALLBACK_BODY_LIMIT + 1).encode(),
    )

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.anyio
async def test_wecom_callback_rejects_chunked_body_over_64_kib_before_app():
    called, sent = await _run_request(
        "/api/v1/integrations/wecom/callback/not-a-uuid",
        [b"a" * 40_000, b"b" * (WECOM_CALLBACK_BODY_LIMIT - 39_999)],
    )

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.anyio
async def test_wecom_callback_allows_exact_64_kib_boundary():
    called, sent = await _run_request(
        "/api/v1/integrations/wecom/callback/not-a-uuid",
        [b"a" * WECOM_CALLBACK_BODY_LIMIT],
    )

    assert called is True
    assert sent[0]["status"] == 204


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/integrations/wecom/callback",
        "/api/v1/integrations/wecom/callback/id/extra",
        "/api/v1/integrations/wecom/not-callback/id",
    ],
)
def test_wecom_callback_body_matcher_does_not_broaden_route_shape(path: str):
    from app.middleware.request_body_limit import is_wecom_callback_path

    assert is_wecom_callback_path(path) is False
