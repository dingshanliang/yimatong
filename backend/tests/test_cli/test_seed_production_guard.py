import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from typer.testing import CliRunner

from app.cli.seed import app, settings

runner = CliRunner()
BACKEND_DIR = Path(__file__).resolve().parents[2]


MUTATING_COMMANDS = [
    ["tenant", "--name", "Guard tenant", "--slug", "guard-tenant"],
    [
        "product",
        "--tenant",
        "guard-tenant",
        "--brand",
        "Guard brand",
        "--product-name",
        "Guard product",
        "--sku",
        "GUARD-SKU",
    ],
    ["code", "--tenant", "guard-tenant", "--batch-code", "GUARD-BATCH"],
    ["all"],
    ["demo"],
]

IDENTITY_BEARING_PRODUCTION_COMMANDS = [
    ["-m", "app.cli", "all", "--allow-production"],
    ["-m", "app.cli", "demo", "--allow-production"],
    ["scripts/seed_demo.py", "generate", "--target", "demo", "--allow-production"],
    ["scripts/seed_demo.py", "reset", "--target", "demo", "--allow-production"],
    ["-m", "app.cli", "baseline", "build", "--target", "baseline-base", "--allow-production"],
]


def test_rich_demo_seed_never_persists_raw_wechat_openid():
    source = (BACKEND_DIR / "scripts" / "seed_demo.py").read_text()

    # 演示数据不再伪造任何微信身份（明文/哈希/加密均不允许）；
    # 消费者身份统一走 authority service（ConsumerProfile / MemberIdentityCredential）。
    assert "wechat_openid=" not in source
    assert "openid" not in source


@pytest.mark.parametrize("arguments", MUTATING_COMMANDS, ids=lambda arguments: arguments[0])
def test_every_mutating_seed_command_rejects_production_before_db(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
):
    ensure_tenant = AsyncMock(side_effect=AssertionError("tenant DB entrypoint must not run"))
    find_tenant = AsyncMock(side_effect=AssertionError("tenant lookup must not run"))
    session_factory = Mock(side_effect=AssertionError("runtime DB session must not open"))
    subprocess_run = Mock(side_effect=AssertionError("demo subprocess must not start"))
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr("app.cli.seed._ensure_tenant", ensure_tenant)
    monkeypatch.setattr("app.cli.seed._find_tenant_id", find_tenant)
    monkeypatch.setattr("app.cli.seed.async_session", session_factory)
    monkeypatch.setattr(subprocess, "run", subprocess_run)

    result = runner.invoke(app, arguments)

    assert result.exit_code == 2
    assert "production" in result.output
    ensure_tenant.assert_not_awaited()
    find_tenant.assert_not_awaited()
    session_factory.assert_not_called()
    subprocess_run.assert_not_called()


@pytest.mark.parametrize("command", ["tenant", "product", "code", "all", "demo"])
def test_every_mutating_seed_command_exposes_same_production_authority(command: str):
    result = runner.invoke(app, [command, "--help"])

    assert result.exit_code == 0
    assert "--allow-production" in result.output


@pytest.mark.parametrize("admin_email", ["admin@example.com", " ADMIN@EXAMPLE.COM ", "\tAdmin@Example.Com\n"])
def test_production_tenant_seed_rejects_canonical_default_identity_even_when_authorized(
    admin_email: str,
    monkeypatch: pytest.MonkeyPatch,
):
    ensure_tenant = AsyncMock(side_effect=AssertionError("unsafe identity must not reach DB"))
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr("app.cli.seed._ensure_tenant", ensure_tenant)

    result = runner.invoke(
        app,
        [
            "tenant",
            "--name",
            "Production tenant",
            "--slug",
            "production-tenant",
            "--admin-email",
            admin_email,
            "--admin-password",
            "ProductionSeedPassword123",
            "--allow-production",
        ],
    )

    assert result.exit_code == 2
    assert "默认管理员" in result.output
    ensure_tenant.assert_not_awaited()


@pytest.mark.parametrize(
    "arguments",
    [["all", "--allow-production"], ["demo", "--allow-production"]],
    ids=["all", "demo"],
)
def test_repository_identity_seed_aliases_reject_production_authority_before_entrypoint(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
):
    ensure_tenant = AsyncMock(side_effect=AssertionError("tenant DB entrypoint must not run"))
    subprocess_run = Mock(side_effect=AssertionError("demo subprocess must not start"))
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr("app.cli.seed._ensure_tenant", ensure_tenant)
    monkeypatch.setattr(subprocess, "run", subprocess_run)

    result = runner.invoke(app, arguments)

    assert result.exit_code == 2
    assert "内置演示账号或密码" in result.output
    ensure_tenant.assert_not_awaited()
    subprocess_run.assert_not_called()


def test_explicit_production_authority_accepts_bounded_tenant_with_non_default_identity(
    monkeypatch: pytest.MonkeyPatch,
):
    ensure_tenant = AsyncMock(return_value=("tenant-id", True))
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr("app.cli.seed._ensure_tenant", ensure_tenant)

    result = runner.invoke(
        app,
        [
            "tenant",
            "--name",
            "Production tenant",
            "--slug",
            "production-tenant",
            "--admin-email",
            "owner@production.example",
            "--admin-password",
            "ProductionSeedPassword123",
            "--allow-production",
        ],
    )

    assert result.exit_code == 0
    ensure_tenant.assert_awaited_once()
    assert ensure_tenant.await_args.kwargs["slug"] == "production-tenant"
    assert ensure_tenant.await_args.kwargs["admin_email"] == "owner@production.example"


@pytest.mark.parametrize("arguments", MUTATING_COMMANDS[:3], ids=lambda arguments: arguments[0])
def test_subprocess_production_rejection_creates_no_database(
    arguments: list[str],
    tmp_path: Path,
):
    runtime_db = tmp_path / "runtime.sqlite"
    control_db = tmp_path / "control.sqlite"
    callback_db = tmp_path / "callback.sqlite"
    environment = os.environ.copy()
    environment.update(
        {
            "ENVIRONMENT": "production",
            "DATABASE_URL": f"sqlite+aiosqlite:///{runtime_db}",
            "CONTROL_DATABASE_URL": f"sqlite+aiosqlite:///{control_db}",
            "CALLBACK_DATABASE_URL": f"sqlite+aiosqlite:///{callback_db}",
            "SECRET_KEY": "production-secret-key-9dd2177f7d104205a14b93e6",
            "HMAC_PEPPER": "production-hmac-pepper-901bc458e0644e728b3d1475",
            "IP_HASH_SECRET": "production-ip-secret-f0ee260ac501437990b78ec1",
            "ADMIN_PUBLIC_URL": "https://admin.example.test",
            "H5_PUBLIC_URL": "https://h5.example.test",
            "PLATFORM_PUBLIC_URL": "https://platform.example.test",
            "COOKIE_SECURE": "true",
        }
    )

    result = subprocess.run(
        [sys.executable, "-m", "app.cli", *arguments],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "production" in result.stdout + result.stderr
    assert not runtime_db.exists()
    assert not control_db.exists()
    assert not callback_db.exists()


@pytest.mark.parametrize(
    "arguments",
    IDENTITY_BEARING_PRODUCTION_COMMANDS,
    ids=["all", "demo-wrapper", "seed-demo-generate", "seed-demo-reset", "baseline-build"],
)
def test_identity_bearing_seed_subprocesses_reject_production_authority_with_zero_writes(
    arguments: list[str],
    tmp_path: Path,
):
    runtime_db = tmp_path / "runtime.sqlite"
    control_db = tmp_path / "control.sqlite"
    callback_db = tmp_path / "callback.sqlite"
    environment = os.environ.copy()
    environment.update(
        {
            "ENVIRONMENT": "production",
            "DATABASE_URL": f"sqlite+aiosqlite:///{runtime_db}",
            "CONTROL_DATABASE_URL": f"sqlite+aiosqlite:///{control_db}",
            "CALLBACK_DATABASE_URL": f"sqlite+aiosqlite:///{callback_db}",
            "SECRET_KEY": "production-secret-key-9dd2177f7d104205a14b93e6",
            "HMAC_PEPPER": "production-hmac-pepper-901bc458e0644e728b3d1475",
            "IP_HASH_SECRET": "production-ip-secret-f0ee260ac501437990b78ec1",
            "ADMIN_PUBLIC_URL": "https://admin.example.test",
            "H5_PUBLIC_URL": "https://h5.example.test",
            "PLATFORM_PUBLIC_URL": "https://platform.example.test",
            "COOKIE_SECURE": "true",
        }
    )

    result = subprocess.run(
        [sys.executable, *arguments],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "production" in result.stdout + result.stderr
    assert not runtime_db.exists()
    assert not control_db.exists()
    assert not callback_db.exists()


@pytest.mark.parametrize("admin_email", [" ADMIN@EXAMPLE.COM ", "\tAdmin@Example.Com\n"])
def test_tenant_seed_subprocess_rejects_canonical_default_email_before_database(
    admin_email: str,
    tmp_path: Path,
):
    runtime_db = tmp_path / "runtime.sqlite"
    control_db = tmp_path / "control.sqlite"
    callback_db = tmp_path / "callback.sqlite"
    environment = os.environ.copy()
    environment.update(
        {
            "ENVIRONMENT": "production",
            "DATABASE_URL": f"sqlite+aiosqlite:///{runtime_db}",
            "CONTROL_DATABASE_URL": f"sqlite+aiosqlite:///{control_db}",
            "CALLBACK_DATABASE_URL": f"sqlite+aiosqlite:///{callback_db}",
            "SECRET_KEY": "production-secret-key-9dd2177f7d104205a14b93e6",
            "HMAC_PEPPER": "production-hmac-pepper-901bc458e0644e728b3d1475",
            "IP_HASH_SECRET": "production-ip-secret-f0ee260ac501437990b78ec1",
            "ADMIN_PUBLIC_URL": "https://admin.example.test",
            "H5_PUBLIC_URL": "https://h5.example.test",
            "PLATFORM_PUBLIC_URL": "https://platform.example.test",
            "COOKIE_SECURE": "true",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli",
            "tenant",
            "--name",
            "Production tenant",
            "--slug",
            "production-tenant",
            "--admin-email",
            admin_email,
            "--admin-password",
            "ProductionSeedPassword123",
            "--allow-production",
        ],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "默认管理员" in result.stdout + result.stderr
    assert not runtime_db.exists()
    assert not control_db.exists()
    assert not callback_db.exists()
