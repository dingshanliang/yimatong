import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invite_code import InviteCodeStatus, TenantInviteCode
from app.models.tenant import Account, Organization, Tenant, TenantPlan, TenantStatus, TenantType
from app.utils.security import hash_password


def _generate_invite_code() -> str:
    """Generate a random 12-character alphanumeric invite code."""
    return secrets.token_urlsafe(9)[:12].upper()


async def create_invite_code(
    db: AsyncSession,
    created_by: uuid.UUID,
    tenant_type: str = "brand",
    max_uses: int = 1,
    expires_in_days: int | None = 30,
) -> TenantInviteCode:
    """Create a new invite code."""
    code = _generate_invite_code()
    # Ensure uniqueness
    for _ in range(10):
        existing = await db.execute(
            select(TenantInviteCode).where(TenantInviteCode.code == code)
        )
        if not existing.scalar_one_or_none():
            break
        code = _generate_invite_code()
    else:
        raise RuntimeError("Failed to generate unique invite code")

    invite = TenantInviteCode(
        code=code,
        tenant_type=tenant_type,
        max_uses=max_uses,
        used_count=0,
        status=InviteCodeStatus.active,
        expires_at=datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None,
        created_by=created_by,
    )
    db.add(invite)
    await db.flush()
    await db.refresh(invite)
    return invite


async def validate_invite_code(db: AsyncSession, code: str) -> TenantInviteCode | None:
    """Validate an invite code. Returns the code if valid, None otherwise."""
    result = await db.execute(
        select(TenantInviteCode).where(TenantInviteCode.code == code)
    )
    invite = result.scalar_one_or_none()
    if not invite:
        return None

    # Check status
    if invite.status != InviteCodeStatus.active:
        return None

    # Check expiration (handle SQLite naive datetimes)
    if invite.expires_at:
        now = datetime.now(UTC)
        expires_at = invite.expires_at
        if expires_at.tzinfo is None:
            from datetime import timezone
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            invite.status = InviteCodeStatus.expired
            await db.flush()
            return None

    # Check usage limit
    if invite.used_count >= invite.max_uses:
        invite.status = InviteCodeStatus.depleted
        await db.flush()
        return None

    return invite


async def use_invite_code(db: AsyncSession, invite: TenantInviteCode) -> None:
    """Increment the used_count of an invite code."""
    invite.used_count += 1
    if invite.used_count >= invite.max_uses:
        invite.status = InviteCodeStatus.depleted
    await db.flush()


async def list_invite_codes(
    db: AsyncSession,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[TenantInviteCode], int]:
    """List invite codes with optional status filter. Returns (items, total)."""
    query = select(TenantInviteCode).order_by(TenantInviteCode.created_at.desc())
    count_query = select(func.count()).select_from(TenantInviteCode)

    if status:
        query = query.where(TenantInviteCode.status == status)
        count_query = count_query.where(TenantInviteCode.status == status)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = list(result.scalars().all())
    return items, total


async def register_tenant_with_invite(
    db: AsyncSession,
    invite_code: str,
    name: str,
    slug: str | None,
    admin_email: str,
    admin_name: str,
    admin_password: str,
    industry: str | None = None,
) -> Tenant:
    """Register a new tenant using an invite code.

    The tenant is created with status=pending_review and must be approved
    by a platform admin before it becomes active.
    """
    invite = await validate_invite_code(db, invite_code)
    if not invite:
        raise ValueError("Invalid or expired invite code")

    # Check slug uniqueness
    from app.services.tenant import _generate_slug

    if not slug:
        slug = _generate_slug(name)

    existing = await db.execute(select(Tenant).where(Tenant.slug == slug))
    if existing.scalar_one_or_none():
        raise ValueError(f"Slug '{slug}' already exists")

    # Create tenant with pending_review status
    tenant = Tenant(
        name=name,
        slug=slug,
        status=TenantStatus.active,  # Will be changed to pending_review after invite code system is fully integrated
        plan=TenantPlan.free,
        tenant_type=TenantType(invite.tenant_type),
        industry=industry,
        quota={"max_codes": 10000, "max_campaigns": 50, "max_accounts": 10},
    )
    db.add(tenant)
    await db.flush()

    # Create default organization
    org = Organization(tenant_id=tenant.id, name=f"{name} 默认组织")
    db.add(org)
    await db.flush()

    # Create admin account
    account = Account(
        tenant_id=tenant.id,
        organization_id=org.id,
        email=admin_email,
        hashed_password=hash_password(admin_password),
        name=admin_name,
    )
    db.add(account)
    await db.flush()

    # Mark invite code as used
    await use_invite_code(db, invite)

    await db.refresh(tenant)
    return tenant


async def toggle_invite_code_status(
    db: AsyncSession,
    code_id: uuid.UUID,
    active: bool,
) -> TenantInviteCode | None:
    """Activate or deactivate an invite code."""
    result = await db.execute(
        select(TenantInviteCode).where(TenantInviteCode.id == code_id)
    )
    invite = result.scalar_one_or_none()
    if not invite:
        return None

    new_status = InviteCodeStatus.active if active else InviteCodeStatus.inactive
    # Don't reactivate expired or depleted codes
    if active and invite.status in (InviteCodeStatus.expired, InviteCodeStatus.depleted):
        return None

    invite.status = new_status
    await db.flush()
    return invite
