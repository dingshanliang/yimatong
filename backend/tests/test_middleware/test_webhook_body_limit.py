from app.middleware.request_body_limit import WEBHOOK_JSON_BODY_LIMIT, ConnectorRequestBodyLimitMiddleware


def _scope(path: str, method: str) -> dict:
    return {"type": "http", "method": method, "path": path}


def test_webhook_endpoint_mutations_are_bounded_before_json_materialization() -> None:
    assert (
        ConnectorRequestBodyLimitMiddleware._limit_for_scope(_scope("/api/v1/webhooks/endpoints", "POST"))
        == WEBHOOK_JSON_BODY_LIMIT
    )
    assert (
        ConnectorRequestBodyLimitMiddleware._limit_for_scope(
            _scope("/api/v1/webhooks/endpoints/00000000-0000-4000-8000-000000000001", "PATCH")
        )
        == WEBHOOK_JSON_BODY_LIMIT
    )


def test_webhook_reads_and_deletes_are_not_buffered() -> None:
    path = "/api/v1/webhooks/endpoints/00000000-0000-4000-8000-000000000001"
    assert ConnectorRequestBodyLimitMiddleware._limit_for_scope(_scope("/api/v1/webhooks/endpoints", "GET")) is None
    assert ConnectorRequestBodyLimitMiddleware._limit_for_scope(_scope(path, "DELETE")) is None
