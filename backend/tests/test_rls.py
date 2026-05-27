"""A1-005: PostgreSQL RLS 策略与事务级 tenant_id 设置验收测试"""

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


class TestRLSMigration:
    """验证 RLS 迁移脚本"""

    def test_rls_migration_file_exists(self):
        """RLS 相关的迁移文件应在 alembic/versions/ 中"""
        versions_dir = BACKEND_DIR / "alembic" / "versions"
        rls_files = list(versions_dir.glob("*rls*.py"))
        assert len(rls_files) > 0, "没有找到 RLS 相关的迁移文件"


class TestTenantContext:
    """验证 tenant context 设置函数"""

    def test_tenant_context_function_importable(self):
        from app.utils.rls import set_tenant_context

        assert callable(set_tenant_context)


class TestRLSHelpers:
    """验证 RLS 辅助工具"""

    def test_rls_module_exists(self):
        module_path = BACKEND_DIR / "app" / "utils" / "rls.py"
        assert module_path.is_file(), "app/utils/rls.py 不存在"

    def test_rls_module_has_functions(self):
        content = (BACKEND_DIR / "app" / "utils" / "rls.py").read_text()
        assert "set_tenant_context" in content, "缺少 set_tenant_context 函数"
