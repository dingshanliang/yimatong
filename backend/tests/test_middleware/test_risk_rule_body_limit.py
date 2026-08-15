from app.middleware.request_body_limit import RISK_RULE_JSON_BODY_LIMIT, ConnectorRequestBodyLimitMiddleware


def _scope(path: str, method: str = "POST") -> dict:
    return {"type": "http", "method": method, "path": path}


def test_risk_rule_mutations_have_a_bounded_body() -> None:
    middleware = ConnectorRequestBodyLimitMiddleware
    assert middleware._limit_for_scope(_scope("/api/v1/risk-rules")) == RISK_RULE_JSON_BODY_LIMIT
    assert middleware._limit_for_scope(_scope(f"/api/v1/risk-rules/{'a' * 36}", "PATCH")) == RISK_RULE_JSON_BODY_LIMIT
    assert middleware._limit_for_scope(_scope("/api/v1/risk-rules/evaluate/scan")) == RISK_RULE_JSON_BODY_LIMIT
    assert middleware._limit_for_scope(_scope("/api/v1/risk-notifications/mark-all-read")) == RISK_RULE_JSON_BODY_LIMIT
    assert (
        middleware._limit_for_scope(_scope(f"/api/v1/risk-notifications/{'a' * 36}/read")) == RISK_RULE_JSON_BODY_LIMIT
    )


def test_risk_rule_reads_and_unrelated_routes_are_not_buffered() -> None:
    middleware = ConnectorRequestBodyLimitMiddleware
    assert middleware._limit_for_scope(_scope("/api/v1/risk-rules", "GET")) is None
    assert middleware._limit_for_scope(_scope("/api/v1/products")) is None
