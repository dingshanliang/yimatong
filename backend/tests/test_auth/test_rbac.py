"""A1-008: 最小 RBAC 验收测试"""

from app.utils.rbac import require_role


class TestRBAC:
    def test_require_role_importable(self):
        assert callable(require_role)

    def test_predefined_roles(self):
        from app.utils.rbac import ROLES

        assert "admin" in ROLES
        assert "operator" in ROLES

    def test_admin_has_all_permissions(self):
        from app.utils.rbac import ROLES

        admin_perms = set(ROLES["admin"])
        assert "tenant:manage" in admin_perms
        assert "code:generate" in admin_perms
        assert "product:create" in admin_perms

    def test_operator_has_limited_permissions(self):
        from app.utils.rbac import ROLES

        operator_perms = set(ROLES["operator"])
        assert "code:generate" in operator_perms
        assert "tenant:manage" not in operator_perms
