import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.invite_code import InviteCodeStatus, TenantInviteCode
from app.models.invite_registration import InviteRegistrationReceipt
from app.modules.brand_tenant_initialization import (
    BrandTenantInitialization,
    ControlledInviteOpening,
    InitializeBrandTenant,
)
from app.utils.email import normalize_email


def _generate_invite_code() -> str:
    """Generate a random 12-character alphanumeric invite code."""
    return secrets.token_urlsafe(9)[:12].upper()


async def create_invite_code(
    db: AsyncSession,
    created_by_actor: str,
    tenant_type: str = "brand",
    max_uses: int = 1,
    expires_in_days: int | None = 30,
) -> TenantInviteCode:
    """Create a new invite code."""
    code = _generate_invite_code()
    # Ensure uniqueness
    for _ in range(10):
        existing = await db.execute(select(TenantInviteCode).where(TenantInviteCode.code == code))
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
        created_by_actor=created_by_actor,
    )
    db.add(invite)
    await db.flush()
    await db.refresh(invite)
    return invite


async def validate_invite_code(db: AsyncSession, code: str) -> TenantInviteCode | None:
    """Validate an invite code. Returns the code if valid, None otherwise.

    This is a read-only validation; it does NOT mutate invite status.
    Callers should handle status transitions (expired / depleted) explicitly.
    """
    result = await db.execute(select(TenantInviteCode).where(TenantInviteCode.code == code))
    invite = result.scalar_one_or_none()
    if not invite:
        return None

    if invite.status != InviteCodeStatus.active:
        return None

    if invite.expires_at:
        now = datetime.now(UTC)
        expires_at = invite.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < now:
            return None

    if invite.used_count >= invite.max_uses:
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


class IdempotencyConflictError(ValueError):
    """The key was already bound to a different canonical request."""


@dataclass(frozen=True)
class TenantRegistrationResult:
    tenant_id: uuid.UUID
    tenant_slug: str


def _keyed_digest(value: bytes) -> str:
    """Return a non-reversible persisted digest using the configured HMAC pepper."""
    return hmac.new(settings.hmac_pepper.encode(), value, hashlib.sha256).hexdigest()


def _canonical_registration_hash(
    *,
    invite_code: str,
    name: str,
    admin_email: str,
    admin_name: str,
    admin_password: str,
    industry: str | None,
) -> str:
    canonical = {
        "admin_email": normalize_email(admin_email),
        "admin_name": admin_name.strip(),
        "admin_password": admin_password,
        "industry": industry.strip() if industry else None,
        "invite_code": invite_code.strip().upper(),
        "name": name.strip(),
    }
    serialized = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return _keyed_digest(serialized)


async def register_tenant_with_invite(
    db: AsyncSession,
    idempotency_key: str,
    invite_code: str,
    name: str,
    slug: str | None,
    admin_email: str,
    admin_name: str,
    admin_password: str,
    industry: str | None = None,
) -> TenantRegistrationResult:
    """原子初始化品牌租户并持久化可重放的公共注册回执。"""
    if slug is not None:
        raise ValueError("租户标识由系统自动生成，无需填写")

    admin_email = normalize_email(admin_email)
    key_hash = _keyed_digest(idempotency_key.encode())
    request_hash = _canonical_registration_hash(
        invite_code=invite_code,
        name=name,
        admin_email=admin_email,
        admin_name=admin_name,
        admin_password=admin_password,
        industry=industry,
    )
    receipt_row = (
        await db.execute(
            select(InviteRegistrationReceipt).where(InviteRegistrationReceipt.idempotency_key_hash == key_hash)
        )
    ).scalar_one_or_none()
    if receipt_row is not None and not hmac.compare_digest(receipt_row.request_hash, request_hash):
        raise IdempotencyConflictError("同一幂等键不能用于不同的注册请求")

    if receipt_row is None:
        candidate = InviteRegistrationReceipt(idempotency_key_hash=key_hash, request_hash=request_hash)
        try:
            async with db.begin_nested():
                db.add(candidate)
                await db.flush()
            receipt_row = candidate
        except IntegrityError:
            receipt_row = (
                await db.execute(
                    select(InviteRegistrationReceipt).where(InviteRegistrationReceipt.idempotency_key_hash == key_hash)
                )
            ).scalar_one()
            if not hmac.compare_digest(receipt_row.request_hash, request_hash):
                raise IdempotencyConflictError("同一幂等键不能用于不同的注册请求") from None

    if receipt_row.tenant_id is not None and receipt_row.tenant_slug is not None:
        return TenantRegistrationResult(tenant_id=receipt_row.tenant_id, tenant_slug=receipt_row.tenant_slug)
    if receipt_row.tenant_id is not None or receipt_row.tenant_slug is not None:  # pragma: no cover
        raise RuntimeError("注册幂等回执不完整")

    try:
        receipt = await BrandTenantInitialization(db).initialize(
            InitializeBrandTenant(
                name=name,
                admin_name=admin_name,
                admin_email=admin_email,
                industry=industry,
                opening=ControlledInviteOpening(
                    invite_code=invite_code.strip().upper(),
                    chosen_password=admin_password,
                ),
            )
        )
    except Exception as exc:
        from app.modules.brand_tenant_initialization.interface import BrandTenantInitializationError

        if isinstance(exc, BrandTenantInitializationError):
            raise ValueError(str(exc)) from exc
        raise

    receipt_row.tenant_id = receipt.tenant_id
    receipt_row.tenant_slug = receipt.tenant_key
    await db.flush()
    return TenantRegistrationResult(tenant_id=receipt.tenant_id, tenant_slug=receipt.tenant_key)


async def toggle_invite_code_status(
    db: AsyncSession,
    code_id: uuid.UUID,
    active: bool,
) -> TenantInviteCode | None:
    """Activate or deactivate an invite code."""
    result = await db.execute(select(TenantInviteCode).where(TenantInviteCode.id == code_id))
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
