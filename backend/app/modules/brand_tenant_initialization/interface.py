"""品牌租户初始化的稳定外部 interface。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum


class InitialAdminState(StrEnum):
    active = "active"
    pending_activation = "pending_activation"


@dataclass(frozen=True)
class PlatformOpening:
    """平台代客户创建；平台不得设置客户永久密码。"""

    operator_id: str
    plan_name: str = "free"
    tenant_type: str = "brand"


@dataclass(frozen=True)
class ControlledInviteOpening:
    """持受控邀请码的客户自行注册并当场设置密码。"""

    invite_code: str
    chosen_password: str


@dataclass(frozen=True)
class TrustedAutomationOpening:
    """仅供 CLI、Demo 和基准数据包等受信组合根使用。"""

    actor: str
    chosen_password: str
    plan_name: str = "free"
    stable_tenant_key: str | None = None


Opening = PlatformOpening | ControlledInviteOpening | TrustedAutomationOpening


@dataclass(frozen=True)
class InitializeBrandTenant:
    name: str
    admin_name: str
    admin_email: str
    opening: Opening
    industry: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class InitializationReceipt:
    """返回该回执即表示租户初始化原子部分已经完整写入当前事务。"""

    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    initial_admin_id: uuid.UUID
    tenant_key: str
    plan_name: str
    initial_admin_state: InitialAdminState
    category_count: int


class BrandTenantInitializationError(Exception):
    """稳定错误 interface；不向调用方泄漏数据库实现。"""


class InvalidInitializationInput(BrandTenantInitializationError):
    pass


class OpeningDenied(BrandTenantInitializationError):
    pass


class BrandTenantAlreadyExists(BrandTenantInitializationError):
    pass


class PlanDefinitionUnavailable(BrandTenantInitializationError):
    pass


class PermissionTemplateInvalid(BrandTenantInitializationError):
    pass
