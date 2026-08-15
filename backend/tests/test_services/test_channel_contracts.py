import ast
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.channel import (
    AccountScopeCreate,
    AllocationArchive,
    AllocationReassign,
    BatchAssign,
    DistributorCreate,
    DistributorUpdate,
    StoreAllocation,
)
from app.services import channel_access
from app.utils.auth_rbac import WEB_ROLE_PERMISSIONS


def _assert_named_bind_parity(sql: str, params: ast.Dict) -> None:
    assert all(key is not None and isinstance(key, ast.Constant) and isinstance(key.value, str) for key in params.keys)
    parameter_names = {key.value for key in params.keys if isinstance(key, ast.Constant)}
    bind_names = set(re.findall(r"(?<!:):([A-Za-z_]\w*)", sql))
    assert parameter_names == bind_names


def test_all_channel_sql_adapters_bind_exact_named_parameters():
    services = Path(__file__).parents[2] / "app" / "services"
    authority_tree = ast.parse((services / "channel_authority.py").read_text())
    checked = 0
    for call in (node for node in ast.walk(authority_tree) if isinstance(node, ast.Call)):
        if not isinstance(call.func, ast.Name) or call.func.id != "_call":
            continue
        assert len(call.args) == 3
        sql_node, params_node = call.args[1:]
        assert isinstance(sql_node, ast.Constant) and isinstance(sql_node.value, str)
        assert isinstance(params_node, ast.Dict)
        _assert_named_bind_parity(sql_node.value, params_node)
        checked += 1

    channel_tree = ast.parse((services / "channel.py").read_text())
    for call in (node for node in ast.walk(channel_tree) if isinstance(node, ast.Call)):
        if len(call.args) < 2 or not isinstance(call.args[1], ast.Dict):
            continue
        sql = ast.unparse(call.args[0])
        if "get_my_channel_scope" not in sql:
            continue
        sql_constants = [node.value for node in ast.walk(call.args[0]) if isinstance(node, ast.Constant)]
        assert len(sql_constants) == 1 and isinstance(sql_constants[0], str)
        _assert_named_bind_parity(sql_constants[0], call.args[1])
        checked += 1

    assert checked == 14


def test_channel_permission_matrix_matches_admin_operator_viewer_contract():
    channel_permissions = {"channel:read", "channel:manage", "channel:allocate", "channel:scope"}
    assert channel_permissions <= set(WEB_ROLE_PERMISSIONS["admin"])
    assert channel_permissions - {"channel:scope"} <= set(WEB_ROLE_PERMISSIONS["operator"])
    assert "channel:scope" not in WEB_ROLE_PERMISSIONS["operator"]
    assert channel_permissions.isdisjoint(WEB_ROLE_PERMISSIONS["viewer"])


def test_channel_requests_are_strict_and_bounded():
    with pytest.raises(ValidationError):
        DistributorCreate(name="valid", unexpected=True)
    with pytest.raises(ValidationError):
        DistributorCreate(name=" ")
    with pytest.raises(ValidationError):
        DistributorCreate(name="x" * 121)
    with pytest.raises(ValidationError):
        DistributorUpdate(expected_version=1)
    assert DistributorUpdate(expected_version=1, contact_name=None, contact_phone=None).model_fields_set == {
        "expected_version",
        "contact_name",
        "contact_phone",
    }
    with pytest.raises(ValidationError):
        DistributorUpdate(expected_version=1, contact_phone="not-a-phone")


def test_batch_assignment_requires_exactly_one_target():
    target = uuid.uuid4()
    assert BatchAssign(region_id=target).region_id == target
    with pytest.raises(ValidationError):
        BatchAssign()
    with pytest.raises(ValidationError):
        BatchAssign(region_id=target, distributor_id=target)


def test_allocation_target_must_match_exactly():
    target = uuid.uuid4()
    common = {"batch_id": uuid.uuid4(), "quantity": 1, "reason": "initial allocation"}
    assert StoreAllocation(**common, target_type="store", store_id=target).store_id == target
    with pytest.raises(ValidationError):
        StoreAllocation(**common, target_type="store", region_id=target)
    with pytest.raises(ValidationError):
        AllocationReassign(
            expected_version=1,
            target_type="store",
            store_id=target,
            quantity=1,
            reason=" ",
        )
    with pytest.raises(ValidationError):
        AllocationArchive(expected_version=1, reason=" ")


def test_account_scope_target_must_match_exactly():
    target = uuid.uuid4()
    common = {"account_id": uuid.uuid4()}
    assert AccountScopeCreate(**common, scope_type="store", store_id=target).store_id == target
    with pytest.raises(ValidationError):
        AccountScopeCreate(**common, scope_type="store", region_id=target)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("state", "allowed"),
    [
        ({"tenant_type": "brand", "auth_method": "jwt", "acting_tenant_id": None}, True),
        ({"tenant_type": "brand", "auth_method": "api_key", "acting_tenant_id": None}, False),
        ({"tenant_type": "agency", "auth_method": "jwt", "acting_tenant_id": str(uuid.uuid4())}, False),
    ],
)
async def test_brand_channel_principal_rejects_api_keys_and_acting_agencies(monkeypatch, state, allowed):
    monkeypatch.setattr(channel_access, "require_durable_session", AsyncMock())
    request = SimpleNamespace(state=SimpleNamespace(**state))
    if allowed:
        await channel_access.require_brand_channel_principal(request)
    else:
        with pytest.raises(HTTPException) as exc:
            await channel_access.require_brand_channel_principal(request)
        assert exc.value.status_code == 403
