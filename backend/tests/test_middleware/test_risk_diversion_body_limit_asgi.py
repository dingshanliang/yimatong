import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app, origins
from app.middleware.request_body_limit import RISK_DIVERSION_JSON_BODY_LIMIT, RISK_RULE_JSON_BODY_LIMIT


def _allowed_origin() -> str:
    return "http://localhost:3000" if origins == ["*"] else origins[0]


async def _chunked_oversized_body():
    yield b"a" * 32_768
    yield b"b" * (RISK_DIVERSION_JSON_BODY_LIMIT - 32_767)


async def _chunked_oversized_rule_body():
    yield b"a" * 32_768
    yield b"b" * (RISK_RULE_JSON_BODY_LIMIT - 32_767)


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["evidence", "transition"])
@pytest.mark.parametrize("transfer", ["declared", "chunked"])
async def test_unauthenticated_oversized_diversion_mutation_is_rejected_before_tenant_auth(
    action: str,
    transfer: str,
):
    path = f"/api/v1/risk-dashboard/diversion-clues/018f0f65-7ad4-7cc4-b874-57d93b10ab12/{action}"
    request_id = f"repair7-{transfer}-{action}"
    content = b"a" * (RISK_DIVERSION_JSON_BODY_LIMIT + 1) if transfer == "declared" else _chunked_oversized_body()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            path,
            content=content,
            headers={"Content-Type": "application/json", "Origin": _allowed_origin(), "X-Request-ID": request_id},
        )

    assert response.status_code == 413
    assert response.headers["x-request-id"] == request_id
    assert response.headers.get("access-control-allow-origin") in {_allowed_origin(), "*"}


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["evidence", "transition"])
async def test_unauthenticated_diversion_mutation_at_exact_limit_reaches_tenant_auth(action: str):
    path = f"/api/v1/risk-dashboard/diversion-clues/018f0f65-7ad4-7cc4-b874-57d93b10ab12/{action}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            path,
            content=b"a" * RISK_DIVERSION_JSON_BODY_LIMIT,
            headers={"Content-Type": "application/json", "X-Request-ID": f"repair7-boundary-{action}"},
        )

    assert response.status_code == 401
    assert response.headers["x-request-id"] == f"repair7-boundary-{action}"


@pytest.mark.anyio
async def test_diversion_body_cap_keeps_cors_preflight_outermost():
    path = "/api/v1/risk-dashboard/diversion-clues/018f0f65-7ad4-7cc4-b874-57d93b10ab12/evidence"
    origin = _allowed_origin()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            path,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,authorization",
            },
        )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") in {origin, "*"}
    assert "POST" in response.headers["access-control-allow-methods"]


@pytest.mark.anyio
@pytest.mark.parametrize("transfer", ["declared", "chunked"])
async def test_unauthenticated_oversized_risk_rule_mutation_is_rejected_before_auth(transfer: str):
    request_id = f"risk-rule-{transfer}"
    content = b"a" * (RISK_RULE_JSON_BODY_LIMIT + 1) if transfer == "declared" else _chunked_oversized_rule_body()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/risk-rules",
            content=content,
            headers={"Content-Type": "application/json", "Origin": _allowed_origin(), "X-Request-ID": request_id},
        )
    assert response.status_code == 413
    assert response.headers["x-request-id"] == request_id
    assert response.headers.get("access-control-allow-origin") in {_allowed_origin(), "*"}


@pytest.mark.anyio
async def test_risk_rule_mutation_at_exact_limit_reaches_tenant_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/risk-rules",
            content=b"a" * RISK_RULE_JSON_BODY_LIMIT,
            headers={"Content-Type": "application/json", "X-Request-ID": "risk-rule-boundary"},
        )
    assert response.status_code == 401
    assert response.headers["x-request-id"] == "risk-rule-boundary"
