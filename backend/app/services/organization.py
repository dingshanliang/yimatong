import secrets
import string
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Account, Organization
from app.utils.security import hash_password


def generate_initial_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "Ymt-" + "".join(secrets.choice(alphabet) for _ in range(length))


async def create_organization(
    db: AsyncSession, tenant_id: uuid.UUID, name: str, parent_id: uuid.UUID | None
) -> Organization:
    org = Organization(tenant_id=tenant_id, name=name, parent_id=parent_id)
    db.add(org)
    await db.flush()
    await db.refresh(org)
    return org


async def list_organizations(db: AsyncSession, tenant_id: uuid.UUID) -> list[Organization]:
    result = await db.execute(select(Organization).where(Organization.tenant_id == tenant_id))
    return list(result.scalars().all())


async def count_accounts_by_org(db: AsyncSession, tenant_id: uuid.UUID) -> dict[uuid.UUID, int]:
    result = await db.execute(
        select(Account.organization_id, func.count())
        .where(Account.tenant_id == tenant_id)
        .group_by(Account.organization_id)
    )
    return {row[0]: row[1] for row in result.all()}


async def create_account(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    organization_id: uuid.UUID,
    email: str,
    name: str,
    password: str | None,
    role_ids: list[uuid.UUID] | None = None,
) -> Account:
    password = password or generate_initial_password()
    org_result = await db.execute(
        select(Organization).where(Organization.id == organization_id, Organization.tenant_id == tenant_id)
    )
    if not org_result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Organization does not belong to current tenant")

    # Email uniqueness check within tenant
    existing = await db.execute(
        select(Account).where(Account.tenant_id == tenant_id, Account.email == email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="An account with this email already exists in this tenant")

    hashed = hash_password(password)
    account = Account(
        tenant_id=tenant_id,
        organization_id=organization_id,
        email=email,
        hashed_password=hashed,
        name=name,
    )
    db.add(account)
    await db.flush()

    if role_ids:
        from app.models.tenant import Role

        result = await db.execute(select(Role).where(Role.id.in_(role_ids), Role.tenant_id == tenant_id))
        roles = list(result.scalars().all())
        account.roles = roles

    await db.flush()
    await db.refresh(account)
    return account


async def list_accounts(db: AsyncSession, tenant_id: uuid.UUID) -> list[Account]:
    result = await db.execute(select(Account).where(Account.tenant_id == tenant_id))
    return list(result.scalars().all())


async def update_account(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    name: str | None,
    role_ids: list[uuid.UUID] | None,
) -> Account | None:
    result = await db.execute(select(Account).where(Account.id == account_id, Account.tenant_id == tenant_id))
    account = result.scalar_one_or_none()
    if not account:
        return None
    if name:
        account.name = name
    if role_ids is not None:
        from app.models.tenant import Role

        result = await db.execute(select(Role).where(Role.id.in_(role_ids), Role.tenant_id == tenant_id))
        account.roles = list(result.scalars().all())
    await db.flush()
    await db.refresh(account)
    return account
