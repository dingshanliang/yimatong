from app.middleware.request_body_limit import (
    GMV_JSON_BODY_LIMIT,
    ConnectorRequestBodyLimitMiddleware,
)


def test_gmv_mutations_have_pre_materialization_body_limit():
    for path in (
        "/api/v1/gmv/orders/import",
        "/api/v1/gmv/orders/external-1/refund",
        "/api/v1/gmv/orders/external-1/cancel",
    ):
        assert (
            ConnectorRequestBodyLimitMiddleware._limit_for_scope({"type": "http", "method": "POST", "path": path})
            == GMV_JSON_BODY_LIMIT
        )


def test_gmv_reads_do_not_buffer_request_bodies():
    assert (
        ConnectorRequestBodyLimitMiddleware._limit_for_scope(
            {"type": "http", "method": "GET", "path": "/api/v1/gmv/orders"}
        )
        is None
    )
