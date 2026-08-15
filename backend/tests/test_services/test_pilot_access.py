import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services import pilot_access


def _request(**overrides):
    state = {
        "auth_method": "jwt",
        "role": "admin",
        "tenant_type": "brand",
        "acting_tenant_id": None,
        "agency_scopes": [],
        "permissions": ["analytics:view", "campaign:manage"],
        "session_id": uuid.uuid4(),
        "account_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
    }
    state.update(overrides)
    return SimpleNamespace(state=SimpleNamespace(**state))


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "operator"])
async def test_direct_brand_admin_and_operator_have_pilot_access(monkeypatch, role):
    monkeypatch.setattr(pilot_access, "require_durable_session", AsyncMock())
    request = _request(role=role)

    assert await pilot_access.require_pilot_read_access(request) == request.state.session_id
    assert await pilot_access.require_retrospective_manage_access(request) == request.state.session_id


@pytest.mark.asyncio
async def test_acting_agency_read_accepts_analytics_or_campaigns_but_manage_requires_campaigns(monkeypatch):
    monkeypatch.setattr(pilot_access, "require_durable_session", AsyncMock())
    acting_tenant_id = uuid.uuid4()

    for scope in ("analytics", "campaigns"):
        request = _request(tenant_type="agency", acting_tenant_id=acting_tenant_id, agency_scopes=[scope])
        assert await pilot_access.require_pilot_read_access(request) == request.state.session_id

    analytics_only = _request(tenant_type="agency", acting_tenant_id=acting_tenant_id, agency_scopes=["analytics"])
    with pytest.raises(HTTPException) as exc:
        await pilot_access.require_retrospective_manage_access(analytics_only)
    assert exc.value.status_code == 403

    campaigns = _request(tenant_type="agency", acting_tenant_id=acting_tenant_id, agency_scopes=["campaigns"])
    assert await pilot_access.require_retrospective_manage_access(campaigns) == campaigns.state.session_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"auth_method": "api_key"},
        {"auth_method": "platform_cookie", "tenant_type": "platform", "role": "platform_admin"},
        {"tenant_type": "brand", "role": "viewer"},
        {"tenant_type": "agency", "acting_tenant_id": None, "agency_scopes": ["campaigns"]},
        {"tenant_type": "agency", "acting_tenant_id": uuid.uuid4(), "agency_scopes": ["products"]},
    ],
)
async def test_pilot_access_rejects_non_admitted_principals(monkeypatch, overrides):
    monkeypatch.setattr(pilot_access, "require_durable_session", AsyncMock())
    request = _request(**overrides)

    with pytest.raises(HTTPException) as exc:
        await pilot_access.require_pilot_read_access(request)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_platform_correction_requires_exact_cookie_principal():
    session_id = uuid.uuid4()
    exact = _request(
        auth_method="platform_cookie",
        role="platform_admin",
        account_id="platform-admin",
        tenant_id="platform",
        tenant_type="platform",
        session_id=session_id,
    )
    assert await pilot_access.require_platform_pilot_correction_access(exact) == session_id

    for invalid in (
        _request(role="platform_admin", account_id="platform-admin", tenant_id="platform", tenant_type="platform"),
        _request(
            auth_method="platform_cookie",
            role="platform_admin",
            account_id=uuid.uuid4(),
            tenant_id="platform",
            tenant_type="platform",
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await pilot_access.require_platform_pilot_correction_access(invalid)
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_mutation_rate_limit_fails_closed(monkeypatch):
    monkeypatch.setattr(
        pilot_access._pilot_security_cache,
        "rate_limit_check_shared",
        AsyncMock(side_effect=pilot_access.SharedSecurityCacheUnavailable("down")),
    )

    with pytest.raises(HTTPException) as exc:
        await pilot_access.enforce_pilot_mutation_rate_limit(uuid.uuid4(), uuid.uuid4())
    assert exc.value.status_code == 503
