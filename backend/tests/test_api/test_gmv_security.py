from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError
from starlette.requests import Request

from app.api.v1 import gmv as gmv_api
from app.schemas.gmv import (
    AutoAttributionRequest,
    ConfirmedAttributionRequest,
    ExternalOrderCancelRequest,
    ExternalOrderImportRequest,
    ExternalOrderRefundRequest,
    GmvDashboardResponse,
    GmvRoiItem,
)
from app.services import gmv as gmv_service
from app.services import gmv_access
from app.services.gmv_authority import (
    canonical_order_payload_digest,
    derive_order_event_idempotency_key,
    record_external_order_value_event,
)
from app.services.redis_cache import SharedSecurityCacheUnavailable


def _request(**state_values) -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/gmv/orders/import", "headers": []})
    request.state.__dict__.update(state_values)
    return request


def _valid_order(**overrides):
    value = {
        "external_id": "marketplace-order-1",
        "amount": Decimal("12.34"),
        "currency": "CNY",
        "status": "confirmed",
        "order_time": datetime.now(UTC),
        "customer_reference": "marketplace-customer-1",
    }
    value.update(overrides)
    return value


def test_import_contract_is_strict_bounded_and_identity_bearing():
    parsed = ExternalOrderImportRequest(
        source_system="approved-erp",
        orders=[_valid_order()],
    )
    assert parsed.orders[0].amount == Decimal("12.34")

    invalid_payloads = [
        {"source_system": "approved-erp", "orders": []},
        {"source_system": "approved-erp", "orders": [_valid_order(amount=Decimal("NaN"))]},
        {"source_system": "approved-erp", "orders": [_valid_order(currency="USD")]},
        {"source_system": "approved-erp", "orders": [_valid_order(order_time=datetime.now())]},
        {"source_system": "approved-erp", "orders": [_valid_order(phone=None, customer_reference=None)]},
        {"source_system": "unknown source", "orders": [_valid_order()]},
        {"source_system": "approved-erp", "orders": [_valid_order(untrusted=True)]},
        {"source_system": "approved-erp", "orders": [_valid_order() for _ in range(201)]},
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            ExternalOrderImportRequest.model_validate(payload)


def test_adjustment_contracts_require_reason_time_and_positive_decimal():
    now = datetime.now(UTC)
    assert ExternalOrderRefundRequest(
        source_system="approved-erp",
        amount=Decimal("1.00"),
        reason="customer return",
        occurred_at=now,
    ).amount == Decimal("1.00")
    assert (
        ExternalOrderCancelRequest(
            source_system="approved-erp",
            reason="payment reversed",
            occurred_at=now,
        ).occurred_at
        == now
    )

    with pytest.raises(ValidationError):
        ExternalOrderRefundRequest(
            source_system="approved-erp",
            amount=Decimal("-1"),
            reason="customer return",
            occurred_at=now,
        )
    with pytest.raises(ValidationError):
        ExternalOrderCancelRequest(
            source_system="approved-erp",
            reason=" ",
            occurred_at=now,
        )


def test_auto_attribution_contract_is_bounded():
    for payload in (
        {"window_hours": 0},
        {"window_hours": 719},
        {"window_hours": 2160},
        {"limit": 0},
        {"limit": 501},
    ):
        with pytest.raises(ValidationError):
            AutoAttributionRequest.model_validate(payload)


def test_confirmed_attribution_contract_accepts_only_snapshotted_windows_and_aware_scan_time():
    valid = {
        "external_order_id": uuid4(),
        "consumer_id": uuid4(),
        "scan_event_id": uuid4(),
        "scan_time": datetime.now(UTC),
    }
    assert ConfirmedAttributionRequest(**valid).attribution_window_hours == 720
    assert ConfirmedAttributionRequest(**valid, attribution_window_hours=168).attribution_window_hours == 168

    for invalid in (
        {**valid, "attribution_window_hours": 719},
        {**valid, "attribution_window_hours": 2160},
        {**valid, "scan_time": datetime.now()},
        {**valid, "unexpected": True},
    ):
        with pytest.raises(ValidationError):
            ConfirmedAttributionRequest.model_validate(invalid)


def test_gmv_read_models_reject_non_cohort_rates_and_converted_only_denominators():
    dashboard = {
        "total_gmv": 100,
        "attributed_orders": 1,
        "total_orders": 2,
        "unattributed_orders": 1,
        "quarantined_order_count": 0,
        "order_data_quality": "complete",
        "attribution_rate": None,
        "attribution_rate_status": "unavailable_non_cohort",
        "daily_trend": [],
        "by_channel": [],
        "by_campaign": [],
    }
    assert GmvDashboardResponse.model_validate(dashboard).attribution_rate is None
    with pytest.raises(ValidationError):
        GmvDashboardResponse.model_validate({**dashboard, "attribution_rate": 50})

    roi = {
        "campaign_id": uuid4(),
        "campaign_name": "campaign",
        "status": "active",
        "budget": 100,
        "attributed_gmv": 200,
        "attributed_orders": 2,
        "scan_count": None,
        "scan_uv": None,
        "scan_cost": None,
        "conversion_rate": None,
        "conversion_rate_status": "unavailable_missing_campaign_eligible_cohort",
        "roi": 2,
        "avg_confidence": 1,
    }
    assert GmvRoiItem.model_validate(roi).conversion_rate is None
    for field in ("scan_count", "scan_uv", "scan_cost", "conversion_rate"):
        with pytest.raises(ValidationError):
            GmvRoiItem.model_validate({**roi, field: 1})


def test_order_digest_and_per_row_idempotency_are_canonical_and_payload_bound():
    request_key = uuid4()
    first = derive_order_event_idempotency_key(request_key, "approved-erp", "order-1")
    assert first == derive_order_event_idempotency_key(request_key, "approved-erp", "order-1")
    assert first != derive_order_event_idempotency_key(request_key, "approved-erp", "order-2")
    payload = {"amount": Decimal("12.30"), "occurred_at": datetime(2026, 1, 1, tzinfo=UTC)}
    assert canonical_order_payload_digest(payload) == canonical_order_payload_digest(dict(reversed(payload.items())))
    assert canonical_order_payload_digest(payload) != canonical_order_payload_digest(
        {**payload, "amount": Decimal("12.31")}
    )


def test_authority_sqlstate_mapping_is_fail_closed_and_busy_is_retryable():
    class Origin(Exception):
        def __init__(self, sqlstate):
            self.sqlstate = sqlstate

    busy = DBAPIError.instance("statement", {}, Origin("55P03"), Origin, postgresql.dialect())
    mapped = gmv_api._authority_http_error(busy)
    assert mapped.status_code == 409
    assert mapped.headers == {"Retry-After": "1"}
    denied = DBAPIError.instance("statement", {}, Origin("42501"), Origin, postgresql.dialect())
    assert gmv_api._authority_http_error(denied).status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize(
    "state_values",
    [
        {"auth_method": "api_key", "tenant_type": "brand", "role": "admin", "acting_tenant_id": None},
        {
            "auth_method": "platform_cookie",
            "tenant_type": "platform",
            "role": "platform_admin",
            "acting_tenant_id": None,
        },
        {"auth_method": "jwt", "tenant_type": "agency", "role": "admin", "acting_tenant_id": uuid4()},
        {"auth_method": "jwt", "tenant_type": "brand", "role": "viewer", "acting_tenant_id": None},
        {"auth_method": "jwt", "tenant_type": "brand", "role": "distributor", "acting_tenant_id": None},
    ],
)
async def test_order_boundary_rejects_non_direct_brand_principals(monkeypatch, state_values):
    monkeypatch.setattr(gmv_access, "require_durable_session", AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await gmv_access.require_direct_brand_order_principal(_request(**state_values))
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_order_boundary_accepts_only_durable_direct_admin_or_operator(monkeypatch):
    durable = AsyncMock()
    monkeypatch.setattr(gmv_access, "require_durable_session", durable)
    session_id = uuid4()
    for role in ("admin", "operator"):
        resolved = await gmv_access.require_direct_brand_order_principal(
            _request(
                auth_method="jwt",
                tenant_type="brand",
                role=role,
                acting_tenant_id=None,
                session_id=str(session_id),
            )
        )
        assert resolved == session_id
    assert durable.await_count == 2


@pytest.mark.anyio
async def test_order_authority_binds_only_durable_session_identity():
    db = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.one.return_value = {"replayed": False}
    db.execute.return_value = result
    tenant_id, session_id, idempotency_key = uuid4(), uuid4(), uuid4()

    await record_external_order_value_event(
        db,
        tenant_id=tenant_id,
        source_system="approved-erp",
        external_order_id="order-1",
        event_type="order_confirmed",
        amount=Decimal("12.34"),
        currency="CNY",
        idempotency_key=idempotency_key,
        payload_digest="a" * 64,
        provenance_digest="b" * 64,
        auth_session_id=session_id,
        occurred_at=datetime.now(UTC),
    )

    statement, parameters = db.execute.await_args.args
    assert set(parameters) == {
        "tenant_id",
        "source_system",
        "external_order_id",
        "event_type",
        "amount",
        "currency",
        "idempotency_key",
        "payload_digest",
        "provenance_digest",
        "auth_session_id",
        "occurred_at",
        "order_time",
        "phone_hash",
        "product_name",
        "channel",
        "reason",
    }
    assert parameters["auth_session_id"] == str(session_id)
    sql = str(statement)
    assert ":auth_session_id" in sql
    assert ":actor_id" not in sql
    assert ":actor_type" not in sql
    assert ":provenance_type" not in sql
    assert ":provenance_verified" not in sql


@pytest.mark.anyio
async def test_confirm_attribution_endpoint_passes_only_exact_evidence_to_database_authority(monkeypatch):
    body = ConfirmedAttributionRequest(
        external_order_id=uuid4(),
        consumer_id=uuid4(),
        scan_event_id=uuid4(),
        scan_time=datetime.now(UTC),
        attribution_window_hours=720,
    )
    authority = AsyncMock(return_value={"attribution_id": uuid4(), "authority_status": "confirmed", "replayed": False})
    limiter = AsyncMock()
    monkeypatch.setattr(gmv_api, "confirm_gmv_attribution", authority)
    monkeypatch.setattr(gmv_api, "enforce_order_mutation_rate_limit", limiter)
    tenant_id, account_id, session_id, idempotency_key = uuid4(), uuid4(), uuid4(), uuid4()

    result = await gmv_api.confirm_attribution_endpoint(
        body,
        idempotency_key,
        AsyncMock(),
        tenant_id,
        account_id,
        session_id,
    )

    assert result["authority_status"] == "confirmed"
    limiter.assert_awaited_once_with(tenant_id, account_id, "gmv-attribution")
    kwargs = authority.await_args.kwargs
    assert set(kwargs) == {
        "tenant_id",
        "auth_session_id",
        "order_id",
        "consumer_id",
        "scan_event_id",
        "scan_event_time",
        "window_hours",
        "idempotency_key",
        "payload_digest",
    }
    assert kwargs["order_id"] == body.external_order_id
    assert kwargs["auth_session_id"] == session_id
    assert kwargs["scan_event_id"] == body.scan_event_id
    assert kwargs["payload_digest"] == canonical_order_payload_digest(body.canonical_payload())


@pytest.mark.anyio
async def test_attribution_adapter_binds_validated_session_and_generates_only_authority_ids():
    db = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.one.return_value = {
        "attribution_id": uuid4(),
        "authority_status": "confirmed",
        "replayed": False,
    }
    db.execute.return_value = result
    tenant_id, session_id = uuid4(), uuid4()

    await gmv_service.confirm_gmv_attribution(
        db,
        tenant_id=tenant_id,
        auth_session_id=session_id,
        order_id=uuid4(),
        consumer_id=uuid4(),
        scan_event_id=uuid4(),
        scan_event_time=datetime.now(UTC),
        window_hours=720,
        idempotency_key=str(uuid4()),
        payload_digest="a" * 64,
    )

    statement, parameters = db.execute.await_args.args
    assert parameters["tenant_id"] == str(tenant_id)
    assert parameters["auth_session_id"] == str(session_id)
    assert UUID(parameters["attribution_id"]).version == 7
    assert UUID(parameters["confirmation_id"]).version == 7
    assert "auth_session_id" in str(statement)
    assert "actor_account_id" not in parameters
    assert "actor_role" not in parameters
    assert "provenance" not in parameters


@pytest.mark.anyio
async def test_order_rate_limit_is_shared_actor_and_source_and_fails_closed(monkeypatch):
    allowed = AsyncMock(side_effect=[(True, 1), (True, 1)])
    monkeypatch.setattr(gmv_access._gmv_security_cache, "rate_limit_check_shared", allowed)
    tenant_id, actor_id = uuid4(), uuid4()
    await gmv_access.enforce_order_mutation_rate_limit(tenant_id, actor_id, "approved-erp")
    assert allowed.await_count == 2
    assert str(tenant_id) not in allowed.await_args_list[0].args[0]
    assert str(actor_id) not in allowed.await_args_list[0].args[0]
    assert "approved-erp" not in allowed.await_args_list[1].args[0]

    monkeypatch.setattr(
        gmv_access._gmv_security_cache,
        "rate_limit_check_shared",
        AsyncMock(side_effect=SharedSecurityCacheUnavailable("down")),
    )
    with pytest.raises(HTTPException) as exc:
        await gmv_access.enforce_order_mutation_rate_limit(tenant_id, actor_id, "approved-erp")
    assert exc.value.status_code == 503


@pytest.mark.anyio
async def test_order_rate_limit_rejection_has_retry_after(monkeypatch):
    monkeypatch.setattr(
        gmv_access._gmv_security_cache,
        "rate_limit_check_shared",
        AsyncMock(side_effect=[(True, 1), (False, 0)]),
    )
    with pytest.raises(HTTPException) as exc:
        await gmv_access.enforce_order_mutation_rate_limit(uuid4(), uuid4(), "approved-erp")
    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "60"}
