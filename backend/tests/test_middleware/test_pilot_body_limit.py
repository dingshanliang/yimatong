from app.middleware.request_body_limit import PILOT_JSON_BODY_LIMIT, ConnectorRequestBodyLimitMiddleware


def _scope(path: str, method: str) -> dict:
    return {"type": "http", "method": method, "path": path}


def test_pilot_mutations_have_pre_materialization_body_limit() -> None:
    retro_id = "018f4a64-2a66-7f9c-8f3f-1ab40f95c301"
    tenant_id = "018f4a64-2a66-7f9c-8f3f-1ab40f95c302"

    assert (
        ConnectorRequestBodyLimitMiddleware._limit_for_scope(_scope(f"/api/v1/retrospectives/{retro_id}", "PATCH"))
        == PILOT_JSON_BODY_LIMIT
    )
    assert (
        ConnectorRequestBodyLimitMiddleware._limit_for_scope(
            _scope(f"/api/v1/platform/tenants/{tenant_id}/pilot-milestones/corrections", "POST")
        )
        == PILOT_JSON_BODY_LIMIT
    )


def test_pilot_body_limit_does_not_expand_to_reads_or_neighbor_paths() -> None:
    assert (
        ConnectorRequestBodyLimitMiddleware._limit_for_scope(_scope("/api/v1/retrospectives/anything", "GET")) is None
    )
    assert (
        ConnectorRequestBodyLimitMiddleware._limit_for_scope(
            _scope("/api/v1/platform/tenants/anything/pilot-milestones", "POST")
        )
        is None
    )
