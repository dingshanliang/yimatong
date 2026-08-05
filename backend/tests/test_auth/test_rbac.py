"""A1-008: 最小 RBAC 验收测试"""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.utils.auth_rbac import WEB_ROLE_PERMISSIONS, require_permission, require_role


class TestRBAC:
    def test_require_role_importable(self):
        assert callable(require_role)

    def test_predefined_roles(self):
        assert "admin" in WEB_ROLE_PERMISSIONS
        assert "operator" in WEB_ROLE_PERMISSIONS

    def test_admin_has_all_permissions(self):
        admin_perms = set(WEB_ROLE_PERMISSIONS["admin"])
        assert "tenant:manage" in admin_perms
        assert "code:generate" in admin_perms
        assert "product:create" in admin_perms

    def test_operator_has_limited_permissions(self):
        operator_perms = set(WEB_ROLE_PERMISSIONS["operator"])
        assert "code:generate" in operator_perms
        assert "tenant:manage" not in operator_perms

    def test_viewer_has_zero_business_permissions(self):
        assert WEB_ROLE_PERMISSIONS["viewer"] == []

    @pytest.mark.anyio
    async def test_viewer_cannot_use_stale_database_permissions(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/v1/campaigns", "headers": []})
        request.state.role = "viewer"
        request.state.permissions = ["campaign:create"]

        with pytest.raises(HTTPException) as exc_info:
            await require_permission("campaign:create")(request)

        assert exc_info.value.status_code == 403
