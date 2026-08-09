import secrets
import string
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.tenant import Account, Organization, Role, account_roles
from app.services.audit import write_audit_log
from app.services.quota import CumulativeQuotaKey, check_quota_for_tenant
from app.utils import escape_like_pattern
from app.utils.email import normalize_email
from app.utils.security import hash_password


def generate_initial_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "Ymt-" + "".join(secrets.choice(alphabet) for _ in range(length))


async def create_organization(
    db: AsyncSession, tenant_id: uuid.UUID, name: str, parent_id: uuid.UUID | None
) -> Organization:
    if parent_id is not None:
        parent = (
            await db.execute(
                select(Organization).where(
                    Organization.id == parent_id,
                    Organization.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if parent is None:
            raise ValueError("Parent organization not found in current tenant")
    org = Organization(tenant_id=tenant_id, name=name, parent_id=parent_id)
    db.add(org)
    await db.flush()
    await db.refresh(org)
    return org


async def list_organizations(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
) -> dict:
    """Returns {items: list[Organization], total: int}"""
    query = select(Organization).where(Organization.tenant_id == tenant_id)
    count_query = select(func.count()).select_from(Organization).where(Organization.tenant_id == tenant_id)

    if q:
        escaped = escape_like_pattern(q)
        query = query.where(Organization.name.ilike(f"%{escaped}%", escape="\\"))
        count_query = count_query.where(Organization.name.ilike(f"%{escaped}%", escape="\\"))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return {"items": items, "total": total}


async def list_organization_tree(db: AsyncSession, tenant_id: uuid.UUID) -> list[Organization]:
    """Return the complete tenant-scoped organization set used to build hierarchy selectors."""
    result = await db.execute(
        select(Organization)
        .where(Organization.tenant_id == tenant_id)
        .order_by(Organization.created_at, Organization.id)
    )
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
    must_change_password: bool = False,
) -> Account:
    email = normalize_email(email)
    password = password or generate_initial_password()
    org_result = await db.execute(
        select(Organization).where(Organization.id == organization_id, Organization.tenant_id == tenant_id)
    )
    if not org_result.scalar_one_or_none():
        raise ValueError("Organization does not belong to current tenant")

    # Email uniqueness check within tenant
    existing = await db.execute(select(Account).where(Account.tenant_id == tenant_id, Account.email == email))
    if existing.scalar_one_or_none():
        raise ValueError("An account with this email already exists in this tenant")

    await check_quota_for_tenant(db, tenant_id, CumulativeQuotaKey.MAX_ACCOUNTS, Account)

    hashed = hash_password(password)
    account = Account(
        tenant_id=tenant_id,
        organization_id=organization_id,
        email=email,
        hashed_password=hashed,
        name=name,
        must_change_password=must_change_password,
        roles=[],
    )
    db.add(account)
    await db.flush()

    if role_ids:
        from app.models.tenant import Role

        result = await db.execute(
            select(Role).where(
                Role.id.in_(role_ids),
                Role.tenant_id == tenant_id,
                Role.name.in_(("admin", "operator", "viewer")),
            )
        )
        roles = list(result.scalars().all())
        if len(roles) != len(set(role_ids)):
            raise ValueError("One or more roles are not assignable built-in roles")
        account.roles = roles

    await db.flush()
    await db.refresh(account)
    return account


async def list_accounts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
) -> dict:
    """Returns {items: list[Account], total: int}"""
    query = select(Account).where(Account.tenant_id == tenant_id)
    count_query = select(func.count()).select_from(Account).where(Account.tenant_id == tenant_id)

    if q:
        escaped = escape_like_pattern(q)
        search_filter = Account.name.ilike(f"%{escaped}%", escape="\\") | Account.email.ilike(
            f"%{escaped}%", escape="\\"
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return {"items": items, "total": total}


async def update_organization(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    org_id: uuid.UUID,
    name: str | None,
    parent_id: uuid.UUID | None,
    parent_id_provided: bool = False,
) -> Organization | None:
    """Update organization fields.
    - name: only updated if not None
    - parent_id: only updated if parent_id_provided is True
      (caller should set this based on exclude_unset)
    """
    result = await db.execute(
        select(Organization).where(Organization.id == org_id, Organization.tenant_id == tenant_id)
    )
    org = result.scalar_one_or_none()
    if not org:
        return None

    if name is not None:
        org.name = name

    if parent_id_provided:
        if parent_id is not None and parent_id == org_id:
            raise ValueError("Organization cannot be its own parent")

        if parent_id is not None:
            # Walk up the ancestor chain to detect cycles
            visited: set[uuid.UUID] = {org_id}
            current_id = parent_id
            while current_id is not None:
                if current_id in visited:
                    raise ValueError("Circular reference detected in organization hierarchy")
                visited.add(current_id)
                ancestor = await db.execute(select(Organization.parent_id).where(Organization.id == current_id))
                current_id = ancestor.scalar_one_or_none()

            # Verify parent belongs to same tenant
            parent_result = await db.execute(
                select(Organization).where(Organization.id == parent_id, Organization.tenant_id == tenant_id)
            )
            if not parent_result.scalar_one_or_none():
                raise ValueError("Parent organization not found in current tenant")

        org.parent_id = parent_id

    await db.flush()
    await db.refresh(org)
    return org


async def delete_organization(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    org_id: uuid.UUID,
) -> bool:
    """Delete an organization. Rejects if it has children or associated accounts."""
    result = await db.execute(
        select(Organization).where(Organization.id == org_id, Organization.tenant_id == tenant_id)
    )
    org = result.scalar_one_or_none()
    if not org:
        raise ValueError("Organization not found")

    # Check for child organizations
    children_result = await db.execute(
        select(func.count()).select_from(Organization).where(Organization.parent_id == org_id)
    )
    child_count = children_result.scalar() or 0
    if child_count > 0:
        raise ValueError(f"Cannot delete: organization has {child_count} child organization(s)")

    # Check for associated accounts
    account_result = await db.execute(
        select(func.count()).select_from(Account).where(Account.organization_id == org_id)
    )
    account_count = account_result.scalar() or 0
    if account_count > 0:
        raise ValueError(f"Cannot delete: organization has {account_count} associated account(s)")

    await db.delete(org)
    await db.flush()
    return True


async def update_account(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    actor_id: uuid.UUID,
    name: str | None,
    organization_id: uuid.UUID | None = None,
    role_ids: list[uuid.UUID] | None = None,
) -> Account | None:
    result = await db.execute(
        select(Account).where(Account.id == account_id, Account.tenant_id == tenant_id).with_for_update()
    )
    account = result.scalar_one_or_none()
    if not account:
        return None
    if name:
        account.name = name
    if organization_id is not None:
        # Validate org belongs to same tenant
        org_result = await db.execute(
            select(Organization).where(Organization.id == organization_id, Organization.tenant_id == tenant_id)
        )
        if not org_result.scalar_one_or_none():
            raise ValueError("Organization does not belong to current tenant")
        account.organization_id = organization_id
    if role_ids is not None:
        from app.models.tenant import Role

        result = await db.execute(
            select(Role).where(
                Role.id.in_(role_ids),
                Role.tenant_id == tenant_id,
                Role.name.in_(("admin", "operator", "viewer")),
            )
        )
        roles = list(result.scalars().all())
        if len(roles) != len(set(role_ids)):
            raise ValueError("One or more roles do not belong to current tenant")
        if {role.id for role in account.roles} != {role.id for role in roles}:
            was_admin = any(role.name == "admin" for role in account.roles)
            remains_admin = any(role.name == "admin" for role in roles)
            if account.is_active and was_admin and not remains_admin:
                if account.id == actor_id:
                    raise ValueError("不能移除当前登录账户的管理员角色")
                active_admin_ids = set(
                    (
                        await db.execute(
                            select(Account.id)
                            .join(account_roles, account_roles.c.account_id == Account.id)
                            .join(Role, Role.id == account_roles.c.role_id)
                            .where(
                                Account.tenant_id == tenant_id,
                                Account.is_active.is_(True),
                                Role.tenant_id == tenant_id,
                                Role.name == "admin",
                            )
                            .with_for_update(of=Account)
                        )
                    ).scalars()
                )
                if len(active_admin_ids) <= 1:
                    raise ValueError("不能移除租户最后一个有效管理员的角色")
            account.roles = roles
            account.auth_version += 1
    await db.flush()
    await db.refresh(account)
    return account


async def set_account_active_status(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    account_id: uuid.UUID,
    is_active: bool,
    reason: str,
) -> Account | None:
    """启用或停用租户账户，并使该账户此前签发的 token 全部失效。"""
    result = await db.execute(
        select(Account)
        .options(selectinload(Account.roles))
        .where(Account.id == account_id, Account.tenant_id == tenant_id)
        .with_for_update()
    )
    account = result.scalar_one_or_none()
    if not account:
        return None

    if not is_active and account.id == actor_id:
        raise ValueError("不能停用当前登录账户")

    if account.is_active == is_active:
        return account

    if not is_active and any(role.name == "admin" for role in account.roles):
        active_admin_ids = (
            await db.execute(
                select(Account.id)
                .join(account_roles, account_roles.c.account_id == Account.id)
                .join(Role, Role.id == account_roles.c.role_id)
                .where(
                    Account.tenant_id == tenant_id,
                    Account.is_active.is_(True),
                    Role.tenant_id == tenant_id,
                    Role.name == "admin",
                )
                .with_for_update(of=Account)
            )
        ).scalars()
        if len(set(active_admin_ids)) <= 1:
            raise ValueError("不能停用租户最后一个有效管理员")

    before = "enabled" if account.is_active else "disabled"
    after = "enabled" if is_active else "disabled"
    account.is_active = is_active
    account.auth_version += 1

    await write_audit_log(
        db,
        operator_id=str(actor_id),
        target_tenant_id=str(tenant_id),
        action="account_enabled" if is_active else "account_disabled",
        resource=f"account:{account.id}",
        details={
            "target_account_id": str(account.id),
            "reason": reason,
            "before": before,
            "after": after,
        },
    )
    await db.flush()
    await db.refresh(account)
    return account
