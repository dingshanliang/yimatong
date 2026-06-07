"""A1-008: 最小 RBAC 验收测试"""

from app.utils.auth_rbac import WEB_ROLE_PERMISSIONS, require_role


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
