"""
Integration test fixtures — in-process ASGI client backed by SQLite.
No external services (PostgreSQL, Redis, backend server) required.
"""

import os
import sys
import uuid
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure backend/ is on sys.path
backend_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(backend_dir))

# Set test env vars before importing app code
os.environ.setdefault("database_url", "sqlite+aiosqlite://")
os.environ.setdefault("redis_url", "redis://localhost:6379/0")
os.environ.setdefault("secret_key", "test-secret-key-for-e2e")
os.environ.setdefault("AES_MASTER_KEY_V1", "00" * 32)
os.environ.setdefault("HMAC_PEPPER", "ff" * 32)

from app.models.base import Base  # noqa: E402
from app.utils.crypto import EnvKeyProvider, init_crypto  # noqa: E402

init_crypto(EnvKeyProvider())

TEST_DB_URL = "sqlite+aiosqlite://"
_test_engine = create_async_engine(TEST_DB_URL, echo=False)
_TestSessionFactory = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)

# ---------- per-test database setup ----------


@pytest_asyncio.fixture(autouse=True)
async def _setup_db():
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------- override get_db so all routes use SQLite ----------

from app.core.database import get_db  # noqa: E402


async def _override_get_db():
    async with _TestSessionFactory() as session:
        yield session


# ---------- seed a tenant + admin account for login ----------

from app.models.tenant import Account, Organization, Tenant  # noqa: E402
from app.utils.security import hash_password  # noqa: E402

TEST_TENANT_ID = uuid.uuid4()
TEST_ORG_ID = uuid.uuid4()
TEST_ACCOUNT_ID = uuid.uuid4()
TEST_EMAIL = "admin@test.com"
TEST_PASSWORD = "admin123"


async def _seed_admin():
    async with _TestSessionFactory() as session:
        tenant = Tenant(id=TEST_TENANT_ID, name="Test Tenant", slug="test-tenant")
        org = Organization(id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, name="Default Org")
        account = Account(
            id=TEST_ACCOUNT_ID,
            tenant_id=TEST_TENANT_ID,
            organization_id=TEST_ORG_ID,
            email=TEST_EMAIL,
            hashed_password=hash_password(TEST_PASSWORD),
            name="Admin",
        )
        session.add_all([tenant, org, account])
        await session.commit()


# ---------- ASGI client fixture ----------


@pytest_asyncio.fixture
async def client():
    from app.main import app  # import after env is configured

    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        await _seed_admin()
        yield ac

    app.dependency_overrides.clear()
