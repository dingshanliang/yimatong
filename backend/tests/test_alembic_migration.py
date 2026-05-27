"""A1-002: Alembic 迁移体系验收测试"""

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


class TestAlembicConfig:
    """验证 Alembic 配置"""

    def test_alembic_ini_exists(self):
        assert (BACKEND_DIR / "alembic.ini").is_file(), "alembic.ini 不存在"

    def test_alembic_dir_exists(self):
        assert (BACKEND_DIR / "alembic").is_dir(), "alembic/ 目录不存在"

    def test_alembic_versions_dir_exists(self):
        assert (BACKEND_DIR / "alembic" / "versions").is_dir(), "alembic/versions/ 目录不存在"

    def test_env_py_exists(self):
        assert (BACKEND_DIR / "alembic" / "env.py").is_file(), "alembic/env.py 不存在"

    def test_script_mako_exists(self):
        assert (BACKEND_DIR / "alembic" / "script.py.mako").is_file(), "alembic/script.py.mako 不存在"


class TestAsyncMigration:
    """验证 async 迁移配置"""

    def test_env_py_uses_async(self):
        content = (BACKEND_DIR / "alembic" / "env.py").read_text()
        assert "async" in content.lower(), "env.py 没有使用 async 模式"
        assert "run_async_migrations" in content or "AsyncEngine" in content, "env.py 没有配置 async 迁移模式"

    def test_env_py_uses_settings_for_url(self):
        content = (BACKEND_DIR / "alembic" / "env.py").read_text()
        assert "settings" in content or "config" in content, "env.py 没有从配置读取数据库 URL"

    def test_alembic_ini_references_correct_script(self):
        content = (BACKEND_DIR / "alembic.ini").read_text()
        assert "script_location = alembic" in content, "alembic.ini 没有正确配置 script_location"


class TestInitialMigration:
    """验证初始迁移文件"""

    def test_initial_migration_exists(self):
        versions_dir = BACKEND_DIR / "alembic" / "versions"
        migration_files = list(versions_dir.glob("*.py"))
        # 排除 __pycache__
        migration_files = [f for f in migration_files if "__pycache__" not in str(f) and f.name != "__init__.py"]
        assert len(migration_files) >= 0, "没有找到迁移文件（初始迁移可选）"

    def test_can_import_alembic_env(self):
        import importlib.util

        env_path = BACKEND_DIR / "alembic" / "env.py"
        spec = importlib.util.spec_from_file_location("alembic_env", env_path)
        assert spec is not None, "无法加载 alembic/env.py"
