import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


def test_rule_contract_rejects_unknown_fields_and_mismatched_config() -> None:
    from app.schemas.risk_rule import RiskRuleCreate

    with pytest.raises(ValidationError):
        RiskRuleCreate.model_validate(
            {
                "name": "IP frequency",
                "rule_type": "ip_frequency",
                "action": "block",
                "config": {"window_minutes": 10, "max_requests": 5, "unexpected": True},
            }
        )
    with pytest.raises(ValidationError):
        RiskRuleCreate.model_validate(
            {
                "name": "IP frequency",
                "rule_type": "ip_frequency",
                "action": "block",
                "config": {"allowed_regions": ["CN"]},
            }
        )


def test_exact_scan_evaluation_has_no_attacker_supplied_context() -> None:
    from app.schemas.risk_rule import ExactScanRiskEvaluate

    with pytest.raises(ValidationError):
        ExactScanRiskEvaluate.model_validate(
            {
                "scan_event_id": str(uuid.uuid4()),
                "rule_id": str(uuid.uuid4()),
                "context": {"request_count": 999999},
            }
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("state", "detail"),
    [
        ({"tenant_type": "brand", "auth_method": "api_key", "role": "full_access"}, "Brand risk access required"),
        (
            {"tenant_type": "brand", "auth_method": "jwt", "role": "admin", "acting_tenant_id": uuid.uuid4()},
            "Brand risk access required",
        ),
        ({"tenant_type": "agency", "auth_method": "jwt", "role": "admin"}, "Brand risk access required"),
    ],
)
async def test_risk_principal_rejects_api_key_acting_and_non_brand(monkeypatch, state, detail) -> None:
    from app.services.risk_access import require_brand_risk_principal

    request = MagicMock()
    request.state = MagicMock()
    for key, value in state.items():
        setattr(request.state, key, value)
    monkeypatch.setattr("app.services.risk_access.require_durable_session", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as raised:
        await require_brand_risk_principal(request)
    assert raised.value.status_code == 403
    assert raised.value.detail == detail


def test_risk_permission_matrix_is_admin_operator_only() -> None:
    from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS, WEB_ROLE_PERMISSIONS

    permissions = {"risk:read", "risk:manage", "risk:evaluate"}
    assert permissions <= set(WEB_ROLE_PERMISSIONS["admin"])
    assert permissions <= set(WEB_ROLE_PERMISSIONS["operator"])
    assert permissions.isdisjoint(WEB_ROLE_PERMISSIONS["viewer"])
    assert all(permissions.isdisjoint(grants) for grants in API_KEY_ROLE_PERMISSIONS.values())


@pytest.mark.anyio
async def test_direct_brand_jwt_principal_is_allowed(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.services.risk_access import require_brand_risk_principal

    monkeypatch.setattr("app.services.risk_access.require_durable_session", AsyncMock())
    request = SimpleNamespace(
        state=SimpleNamespace(tenant_type="brand", auth_method="jwt", acting_tenant_id=None)
    )
    await require_brand_risk_principal(request)
