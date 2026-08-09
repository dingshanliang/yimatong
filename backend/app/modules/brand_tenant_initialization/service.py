"""品牌租户初始化 implementation。

事务提交由当前请求或 CLI 的 session owner 负责；本 implementation 只 flush，
任何异常都会沿外部 seam 传播，使 session owner 回滚整个初始化事务。
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.constants.categories import get_default_categories
from app.models.invite_code import InviteCodeStatus, TenantInviteCode
from app.models.plan import PlanDefinition, TenantQuotaUsage
from app.models.tenant import (
    Account,
    Organization,
    Permission,
    Role,
    Tenant,
    TenantPlan,
    TenantStatus,
    TenantType,
    account_roles,
    role_permissions,
)
from app.modules.brand_tenant_initialization.interface import (
    BrandTenantAlreadyExists,
    ControlledInviteOpening,
    InitialAdminState,
    InitializationReceipt,
    InitializeBrandTenant,
    InvalidInitializationInput,
    OpeningDenied,
    PermissionTemplateInvalid,
    PlanDefinitionUnavailable,
    PlatformOpening,
    TrustedAutomationOpening,
)
from app.services.audit import write_audit_log
from app.services.entitlement import validate_feature_flags
from app.services.quota import (
    QUOTA_RECONCILIATION_SOURCE_REVISION,
    is_current_quota_epoch_active,
    lock_quota_rollout_state,
    validate_quota_config,
)
from app.utils.auth_rbac import WEB_ROLE_PERMISSIONS
from app.utils.email import normalize_email
from app.utils.security import hash_password, validate_password_strength


class BrandTenantInitialization:
    """所有品牌租户创建入口必须跨越的唯一 external seam。"""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def initialize(self, command: InitializeBrandTenant) -> InitializationReceipt:
        self._validate_common(command)
        opening = command.opening

        if isinstance(opening, PlatformOpening):
            plan_name = opening.plan_name
            actor_id = opening.operator_id
            password_hash = hash_password(secrets.token_urlsafe(32))
            admin_state = InitialAdminState.pending_activation
            stable_key = None
            invite = None
            tenant_type = TenantType(opening.tenant_type)
            if tenant_type not in {TenantType.brand, TenantType.agency}:
                raise InvalidInitializationInput("平台当前仅支持开通品牌或代运营租户")
        elif isinstance(opening, ControlledInviteOpening):
            validate_password_strength(opening.chosen_password)
            invite = await self._lock_valid_brand_invite(opening.invite_code)
            plan_name = "free"
            # PlatformAuditLog.operator_id is VARCHAR(36); the action/resource
            # already identify this as an invite opening, so persist the UUID
            # itself instead of an over-length ``invite:<uuid>`` label.
            actor_id = str(invite.id)
            password_hash = hash_password(opening.chosen_password)
            admin_state = InitialAdminState.active
            stable_key = None
            tenant_type = TenantType.brand
        elif isinstance(opening, TrustedAutomationOpening):
            validate_password_strength(opening.chosen_password)
            plan_name = opening.plan_name
            actor_id = opening.actor
            password_hash = hash_password(opening.chosen_password)
            admin_state = InitialAdminState.active
            stable_key = opening.stable_tenant_key
            invite = None
            tenant_type = TenantType.brand
        else:  # pragma: no cover - sealed union guard
            raise InvalidInitializationInput("不支持的租户开通来源")

        plan = await self._load_plan(plan_name)
        tenant_key = stable_key or await self._generate_available_key(command.name)
        await self._assert_tenant_key_available(tenant_key)
        # Shared epoch lock linearizes tenant birth against the final activation
        # transaction. Activation-first creates a current-ready tenant; tenant-
        # first leaves a pending marker which blocks activation until reconciled.
        rollout_state = await lock_quota_rollout_state(self._db)
        quota_epoch_active = is_current_quota_epoch_active(rollout_state)

        role_templates = {name: tuple(WEB_ROLE_PERMISSIONS.get(name, ())) for name in ("admin", "operator", "viewer")}
        permission_codes = tuple(dict.fromkeys(code for codes in role_templates.values() for code in codes))
        if not role_templates["admin"] or any(len(codes) != len(set(codes)) for codes in role_templates.values()):
            raise PermissionTemplateInvalid("品牌管理员权限模板无效")

        tenant_id, organization_id, account_id = uuid7(), uuid7(), uuid7()
        categories = list(get_default_categories(command.industry))
        tenant = Tenant(
            id=tenant_id,
            name=command.name.strip(),
            slug=tenant_key,
            status=TenantStatus.active,
            plan=TenantPlan(plan.name),
            tenant_type=tenant_type,
            industry=command.industry,
            notes=command.notes,
            quota=dict(plan.quota_defaults or {}),
            enabled_features=dict(plan.feature_flags or {}),
            categories=categories,
        )
        organization = Organization(
            id=organization_id,
            tenant_id=tenant_id,
            name=f"{command.name.strip()} 默认组织",
        )
        account = Account(
            id=account_id,
            tenant_id=tenant_id,
            organization_id=organization_id,
            email=normalize_email(command.admin_email),
            hashed_password=password_hash,
            name=command.admin_name.strip(),
            is_active=admin_state is InitialAdminState.active,
        )
        # This tenant and its initial account are born inside the canonical
        # transaction, so their complete authoritative state is proven without
        # a legacy backfill window.
        quota_usage = TenantQuotaUsage(
            tenant_id=tenant_id,
            accounts=1,
            reconciled_at=datetime.now(UTC) if quota_epoch_active else None,
            source_revision=QUOTA_RECONCILIATION_SOURCE_REVISION if quota_epoch_active else None,
            enforcement_ready=quota_epoch_active,
        )
        self._db.add(tenant)
        await self._db.flush()
        self._db.add_all([organization, account, quota_usage])
        await self._db.flush()

        role_descriptions = {"admin": "品牌管理员", "operator": "运营人员", "viewer": "无业务操作权限成员"}
        roles = {
            name: Role(tenant_id=tenant_id, name=name, description=role_descriptions[name]) for name in role_templates
        }
        self._db.add_all(list(roles.values()))
        await self._db.flush()

        permissions = [
            Permission(tenant_id=tenant_id, code=code, description=f"品牌管理员默认权限：{code}")
            for code in permission_codes
        ]
        self._db.add_all(permissions)
        await self._db.flush()
        await self._db.execute(account_roles.insert().values(account_id=account_id, role_id=roles["admin"].id))
        permissions_by_code = {permission.code: permission for permission in permissions}
        role_permission_rows = [
            {"role_id": roles[role_name].id, "permission_id": permissions_by_code[code].id}
            for role_name, codes in role_templates.items()
            for code in codes
        ]
        if role_permission_rows:
            await self._db.execute(role_permissions.insert(), role_permission_rows)

        if invite is not None:
            invite.used_count += 1
            if invite.used_count >= invite.max_uses:
                invite.status = InviteCodeStatus.depleted

        await write_audit_log(
            self._db,
            operator_id=actor_id,
            target_tenant_id=str(tenant_id),
            action="brand_tenant_initialized",
            resource=f"tenant:{tenant_key}",
            details={
                "opening": type(opening).__name__,
                "plan_definition_id": plan.id,
                "initial_admin_state": admin_state.value,
                "permission_count": len(permission_codes),
                "category_count": len(categories),
            },
        )
        await self._db.flush()

        return InitializationReceipt(
            tenant_id=tenant_id,
            organization_id=organization_id,
            initial_admin_id=account_id,
            tenant_key=tenant_key,
            plan_name=plan.name,
            initial_admin_state=admin_state,
            category_count=len(categories),
        )

    @staticmethod
    def _validate_common(command: InitializeBrandTenant) -> None:
        if not command.name.strip():
            raise InvalidInitializationInput("租户名称不能为空")
        if not command.admin_name.strip():
            raise InvalidInitializationInput("管理员姓名不能为空")
        if "@" not in command.admin_email:
            raise InvalidInitializationInput("管理员邮箱格式无效")

    async def _load_plan(self, plan_name: str) -> PlanDefinition:
        plan = (
            await self._db.execute(
                select(PlanDefinition).where(
                    PlanDefinition.name == plan_name,
                    PlanDefinition.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if plan is None:
            raise PlanDefinitionUnavailable(f"套餐不可用：{plan_name}")
        if plan.name not in {item.value for item in TenantPlan}:
            raise PlanDefinitionUnavailable(f"套餐未映射到租户权益：{plan.name}")
        try:
            validate_quota_config(plan.quota_defaults)
            validate_feature_flags(plan.feature_flags)
        except ValueError as exc:
            raise PlanDefinitionUnavailable(f"套餐配置不可用：{plan.name}") from exc
        return plan

    async def _lock_valid_brand_invite(self, code: str) -> TenantInviteCode:
        invite = (
            await self._db.execute(select(TenantInviteCode).where(TenantInviteCode.code == code).with_for_update())
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        expires_at = invite.expires_at if invite else None
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if (
            invite is None
            or invite.status != InviteCodeStatus.active
            or invite.tenant_type != TenantType.brand.value
            or invite.used_count >= invite.max_uses
            or (expires_at is not None and expires_at <= now)
        ):
            raise OpeningDenied("邀请码无效、已过期或已用完")
        return invite

    async def _generate_available_key(self, name: str) -> str:
        base = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]+", "-", name.lower().strip())).strip("-")
        base = (base or f"tenant-{uuid.uuid4().hex[:8]}")[:50]
        await self._lock_tenant_key_namespace(base)
        if not await self._tenant_key_exists(base):
            return base
        for _ in range(10):
            suffix = secrets.token_hex(3)
            candidate = f"{base[:43]}-{suffix}"
            if not await self._tenant_key_exists(candidate):
                return candidate
        raise BrandTenantAlreadyExists("无法生成唯一租户标识")

    async def _assert_tenant_key_available(self, tenant_key: str) -> None:
        if not re.fullmatch(r"[a-z0-9-]{1,50}", tenant_key):
            raise InvalidInitializationInput("稳定租户标识只能包含小写字母、数字和连字符")
        await self._lock_tenant_key_namespace(tenant_key)
        if await self._tenant_key_exists(tenant_key):
            raise BrandTenantAlreadyExists(f"租户标识已存在：{tenant_key}")

    async def _lock_tenant_key_namespace(self, tenant_key: str) -> None:
        """Serialize allocation of one slug namespace for the transaction.

        A unique index alone turns concurrent same-name openings into a 500 for
        the loser. The transaction advisory lock makes the second request see
        the committed base slug and choose a suffix instead.
        """
        if self._db.get_bind().dialect.name == "postgresql":
            await self._db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant_key, 0))"),
                {"tenant_key": tenant_key},
            )

    async def _tenant_key_exists(self, tenant_key: str) -> bool:
        return (
            await self._db.execute(select(Tenant.id).where(Tenant.slug == tenant_key))
        ).scalar_one_or_none() is not None
