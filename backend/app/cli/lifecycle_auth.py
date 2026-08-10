"""Short-lived control-plane authentication for official CLI lifecycle work."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uuid6 import uuid7

from app.core.database import (
    reset_request_security_credential,
    set_request_security_credential,
    set_session_tenant_context,
)
from app.models.auth_security import AuthSession
from app.models.tenant import Account, Role, Tenant, TenantStatus, account_roles

CLI_LIFECYCLE_SESSION_TTL = timedelta(minutes=5)


@asynccontextmanager
async def cli_lifecycle_auth_context(
    control_session_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
) -> AsyncIterator[uuid.UUID]:
    """Bind one non-issued, short-lived admin session around CLI lifecycle work."""

    session_id = uuid7()
    async with control_session_factory() as control:
        await set_session_tenant_context(control, tenant_id)
        account = await control.scalar(
            select(Account)
            .join(
                account_roles,
                (account_roles.c.tenant_id == Account.tenant_id) & (account_roles.c.account_id == Account.id),
            )
            .join(
                Role,
                (Role.tenant_id == account_roles.c.tenant_id) & (Role.id == account_roles.c.role_id),
            )
            .join(Tenant, Tenant.id == Account.tenant_id)
            .where(
                Account.tenant_id == tenant_id,
                Account.id == account_id,
                Account.is_active.is_(True),
                Role.name == "admin",
                Tenant.status == TenantStatus.active,
            )
        )
        if account is None:
            raise RuntimeError("CLI lifecycle authority requires a durable active tenant admin")
        control.add(
            AuthSession(
                id=session_id,
                account_id=account.id,
                tenant_id=account.tenant_id,
                auth_version=account.auth_version,
                current_refresh_jti=f"cli-{uuid7().hex}",
                expires_at=datetime.now(UTC) + CLI_LIFECYCLE_SESSION_TTL,
            )
        )
        await control.commit()

    token = set_request_security_credential("auth_session", str(session_id))
    try:
        yield session_id
    finally:
        reset_request_security_credential(token)
        async with control_session_factory() as control:
            await set_session_tenant_context(control, tenant_id)
            await control.execute(
                delete(AuthSession).where(
                    AuthSession.id == session_id,
                    AuthSession.tenant_id == tenant_id,
                    AuthSession.account_id == account_id,
                )
            )
            await control.commit()
