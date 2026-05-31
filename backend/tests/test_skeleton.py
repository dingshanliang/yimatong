"""A1-001: FastAPI 项目骨架与目录结构验收测试"""

import importlib
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent


class TestDirectoryStructure:
    """验证项目目录结构"""

    EXPECTED_DIRS = [
        "app",
        "app/api",
        "app/api/v1",
        "app/core",
        "app/models",
        "app/schemas",
        "app/services",
        "app/tasks",
        "app/middleware",
        "app/utils",
        "tests",
    ]

    @pytest.mark.parametrize("dir_path", EXPECTED_DIRS)
    def test_directory_exists(self, dir_path: str):
        assert (BACKEND_DIR / dir_path).is_dir(), f"目录 {dir_path} 不存在"

    def test_main_py_exists(self):
        assert (BACKEND_DIR / "app" / "main.py").is_file(), "app/main.py 不存在"

    def test_init_files_exist(self):
        for dir_path in self.EXPECTED_DIRS:
            if dir_path == "tests":
                init_file = BACKEND_DIR / dir_path / "__init__.py"
            else:
                init_file = BACKEND_DIR / dir_path / "__init__.py"
            if dir_path == "app/api/v1":
                # v1 目录需要 __init__.py
                assert init_file.is_file(), f"{dir_path}/__init__.py 不存在"


class TestFastAPIApp:
    """验证 FastAPI 应用可导入和启动"""

    def test_app_importable(self):
        app = importlib.import_module("app.main")
        assert hasattr(app, "app"), "app.main 模块没有 'app' 属性"

    def test_app_is_fastapi_instance(self):
        from fastapi import FastAPI

        from app.main import app

        assert isinstance(app, FastAPI), "app 不是 FastAPI 实例"

    def test_app_title(self):
        from app.main import app

        assert app.title in ("一码通", "一码通 API", "Yimatong", "yimatong")

    def test_health_endpoint_exists(self):
        from app.main import app

        routes = [r.path for r in app.routes]
        assert "/health" in routes, "/health 端点不存在"


class TestConfig:
    """验证配置管理"""

    def test_config_importable(self):
        mod = importlib.import_module("app.core.config")
        assert hasattr(mod, "settings"), "app.core.config 没有 'settings' 属性"

    def test_settings_has_database_url(self):
        from app.core.config import settings

        assert hasattr(settings, "database_url"), "settings 没有 database_url 属性"

    def test_settings_has_redis_url(self):
        from app.core.config import settings

        assert hasattr(settings, "redis_url"), "settings 没有 redis_url 属性"

    def test_settings_is_pydantic_settings(self):
        from pydantic_settings import BaseSettings

        from app.core.config import settings

        assert isinstance(settings, BaseSettings), "settings 不是 BaseSettings 实例"


class TestLintAndFormat:
    """验证代码质量工具配置"""

    def test_ruff_config_exists(self):
        config_path = BACKEND_DIR / "pyproject.toml"
        assert config_path.is_file(), "pyproject.toml 不存在"

    def test_ruff_config_has_target_version(self):
        content = (BACKEND_DIR / "pyproject.toml").read_text()
        assert "target-version" in content, "ruff 配置缺少 target-version"

    def test_ruff_config_has_line_length(self):
        content = (BACKEND_DIR / "pyproject.toml").read_text()
        assert "line-length" in content, "ruff 配置缺少 line-length"


class TestReadme:
    """验证 README 包含启动说明"""

    def test_readme_exists(self):
        assert (BACKEND_DIR / "README.md").is_file(), "README.md 不存在"

    def test_readme_has_startup_instructions(self):
        content = (BACKEND_DIR / "README.md").read_text().lower()
        assert "uv" in content or "pip" in content, "README 缺少安装/启动说明"
        assert "uvicorn" in content, "README 缺少 uvicorn 启动说明"
