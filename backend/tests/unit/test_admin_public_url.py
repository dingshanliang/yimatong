import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.services.auth import generate_password_reset


def _settings(**overrides) -> Settings:
    defaults = {
        "database_url": "postgresql+asyncpg://yimatong_app:runtime-only@db:5432/yimatong",
        "control_database_url": "postgresql+asyncpg://yimatong_control:control-only@db:5432/yimatong",
        "callback_database_url": "postgresql+asyncpg://yimatong_callback:callback-only@db:5432/yimatong",
        "secret_key": "prod-secret-key-8YQ2jZ6xF4mN9pR7sT5vW3kL1cB0dA",
        "hmac_pepper": "prod-hmac-pepper-1Kx9Qm4Vt7Za2Nc8Wd5Yp3Rf6Bs0Gj",
        "ip_hash_secret": "prod-ip-hash-secret-6Tp2Mz8Qa4Wn9Yc1Rk7Vf5Bj3Hs0Ld",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def test_admin_public_url_defaults_to_local_admin_in_development():
    configured = _settings()

    assert configured.admin_public_url == "http://localhost:3000"
    assert configured.build_admin_url("/register", {"invite_code": "A B"}) == (
        "http://localhost:3000/register?invite_code=A+B"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "admin.example.com",
        "ftp://admin.example.com",
        "https://user:password@admin.example.com",
        "https://admin.example.com/app",
        "https://admin.example.com?source=config",
        "https://admin.example.com#fragment",
        "https://admin.example.com:invalid",
    ],
)
def test_admin_public_url_rejects_non_origin_values(value: str):
    with pytest.raises(ValidationError):
        _settings(admin_public_url=value)


@pytest.mark.parametrize(
    "value",
    [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://admin.example.com",
    ],
)
def test_production_requires_explicit_public_https_admin_url(value: str):
    with pytest.raises(ValidationError, match="ADMIN_PUBLIC_URL"):
        _settings(environment="production", admin_public_url=value)


def test_production_rejects_missing_admin_public_url_configuration():
    with pytest.raises(ValidationError, match="ADMIN_PUBLIC_URL"):
        _settings(environment="production")


def test_production_accepts_public_https_admin_url():
    configured = _settings(
        environment="production",
        admin_public_url="https://admin.example.com/",
        h5_public_url="https://h5.example.com/",
        platform_public_url="https://platform.example.com/",
        cookie_secure=True,
    )

    assert configured.admin_public_url == "https://admin.example.com"


def test_production_platform_cookie_security_fails_fast():
    base = {
        "environment": "production",
        "admin_public_url": "https://admin.example.com",
        "h5_public_url": "https://h5.example.com",
        "platform_public_url": "https://platform.example.com",
    }
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        _settings(**base, cookie_secure=False)
    with pytest.raises(ValidationError, match="COOKIE_SAMESITE"):
        _settings(**base, cookie_secure=True, cookie_samesite="None")


def test_production_requires_public_https_platform_origin():
    with pytest.raises(ValidationError, match="PLATFORM_PUBLIC_URL"):
        _settings(
            environment="production",
            admin_public_url="https://admin.example.com",
            h5_public_url="https://h5.example.com",
            platform_public_url="http://localhost:3002",
            cookie_secure=True,
        )


def test_production_requires_public_https_h5_origin():
    with pytest.raises(ValidationError, match="H5_PUBLIC_URL"):
        _settings(
            environment="production",
            admin_public_url="https://admin.example.com",
            h5_public_url="http://localhost:3001",
            platform_public_url="https://platform.example.com",
            cookie_secure=True,
        )


@pytest.mark.asyncio
async def test_password_reset_uses_canonical_admin_public_url():
    account_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    result = MagicMock()
    result.scalar_one_or_none.return_value = object()
    db = AsyncMock()
    db.execute.return_value = result
    cache = AsyncMock()

    with (
        patch.object(settings, "admin_public_url", "https://admin.example.com"),
        patch("app.services.audit.write_audit_log", new=AsyncMock()),
    ):
        generated = await generate_password_reset(
            db=db,
            account_id_str=str(account_id),
            tenant_id=tenant_id,
            cache=cache,
        )

    parsed = urlsplit(generated["reset_url"])
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "https",
        "admin.example.com",
        "/reset-password",
    )
    assert parse_qs(parsed.query) == {
        "token": [generated["reset_token"]],
        "account_id": [str(account_id)],
    }
