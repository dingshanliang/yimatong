import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli.lifecycle_auth import cli_lifecycle_auth_context
from app.core.database import get_request_security_credential
from app.models.auth_security import AuthSession
from app.models.tenant import Account, Organization, Role, Tenant, account_roles
from app.utils.security import hash_password


async def _durable_account(db: AsyncSession, *, role_name: str = "admin") -> Account:
    tenant = Tenant(name="CLI auth tenant", slug=f"cli-auth-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    await db.flush()
    organization = Organization(tenant_id=tenant.id, name="CLI organization")
    role = Role(tenant_id=tenant.id, name=role_name)
    db.add_all([organization, role])
    await db.flush()
    account = Account(
        tenant_id=tenant.id,
        organization_id=organization.id,
        email=f"{role_name}-{uuid.uuid4().hex[:8]}@cli.local",
        hashed_password=hash_password("CliPassword1"),
        name="CLI actor",
    )
    db.add(account)
    await db.flush()
    await db.execute(account_roles.insert().values(tenant_id=tenant.id, account_id=account.id, role_id=role.id))
    await db.commit()
    return account


@pytest.mark.anyio
async def test_cli_lifecycle_auth_context_is_actor_bound_short_lived_and_removed(db: AsyncSession):
    account = await _durable_account(db)
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)

    async with cli_lifecycle_auth_context(
        session_factory,
        tenant_id=account.tenant_id,
        account_id=account.id,
    ) as session_id:
        assert get_request_security_credential() == ("auth_session", str(session_id))
        async with session_factory() as control:
            auth_session = await control.get(AuthSession, session_id)
            assert auth_session is not None
            assert auth_session.account_id == account.id
            assert auth_session.tenant_id == account.tenant_id
            assert auth_session.auth_version == account.auth_version
            assert auth_session.revoked_at is None
            expires_at = (
                auth_session.expires_at.replace(tzinfo=UTC)
                if auth_session.expires_at.tzinfo is None
                else auth_session.expires_at
            )
            assert datetime.now(UTC) < expires_at <= datetime.now(UTC) + timedelta(minutes=5)

    assert get_request_security_credential() is None
    async with session_factory() as control:
        remaining = await control.scalar(
            select(func.count()).select_from(AuthSession).where(AuthSession.id == session_id)
        )
        assert remaining == 0


@pytest.mark.anyio
async def test_cli_lifecycle_auth_context_resets_and_removes_session_after_failure(db: AsyncSession):
    account = await _durable_account(db)
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    session_id = None

    with pytest.raises(RuntimeError, match="business failed"):
        async with cli_lifecycle_auth_context(
            session_factory,
            tenant_id=account.tenant_id,
            account_id=account.id,
        ) as created_session_id:
            session_id = created_session_id
            raise RuntimeError("business failed")

    assert get_request_security_credential() is None
    async with session_factory() as control:
        remaining = await control.scalar(
            select(func.count()).select_from(AuthSession).where(AuthSession.id == session_id)
        )
        assert remaining == 0


@pytest.mark.anyio
async def test_cli_lifecycle_auth_context_rejects_non_admin_actor_without_session(db: AsyncSession):
    account = await _durable_account(db, role_name="operator")
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)

    with pytest.raises(RuntimeError, match="durable active tenant admin"):
        async with cli_lifecycle_auth_context(
            session_factory,
            tenant_id=account.tenant_id,
            account_id=account.id,
        ):
            pytest.fail("non-admin CLI actor must not enter lifecycle context")

    assert get_request_security_credential() is None
    async with session_factory() as control:
        remaining = await control.scalar(select(func.count()).select_from(AuthSession))
        assert remaining == 0
