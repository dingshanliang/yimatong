"""初始管理员激活 module。

租户初始化已提交后再调用本 module。签发失败可以重试，不得回滚或重复初始化租户。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_opening import PlatformTenantOpening
from app.models.tenant import Account, Role, Tenant, TenantStatus, account_roles
from app.services.auth import generate_password_reset
from app.services.redis_cache import AsyncRedisCache


@dataclass(frozen=True)
class ActivationTicket:
    initial_admin_id: uuid.UUID
    url: str


class InitialAdminActivationError(Exception):
    pass


class InitialAdminNotPending(InitialAdminActivationError):
    pass


class InitialAdminActivation:
    def __init__(self, db: AsyncSession, cache: AsyncRedisCache):
        self._db = db
        self._cache = cache

    async def issue_or_reissue(
        self,
        *,
        tenant_id: uuid.UUID,
        operator_id: str,
        initial_admin_id: uuid.UUID | None = None,
    ) -> ActivationTicket:
        tenant_status = await self._db.scalar(select(Tenant.status).where(Tenant.id == tenant_id).with_for_update())
        if tenant_status == TenantStatus.terminated:
            raise InitialAdminNotPending("已终止租户不能重新签发管理员激活链接")
        opening_stmt = select(PlatformTenantOpening).where(
            PlatformTenantOpening.tenant_id == tenant_id,
            PlatformTenantOpening.initial_admin_state == "pending_activation",
        )
        if initial_admin_id is not None:
            opening_stmt = opening_stmt.where(PlatformTenantOpening.initial_admin_id == initial_admin_id)
        opening = (await self._db.execute(opening_stmt.with_for_update())).scalar_one_or_none()
        if opening is None or opening.initial_admin_id is None:
            raise InitialAdminNotPending("没有待激活的初始管理员")
        initial_admin_id = opening.initial_admin_id
        stmt = (
            select(Account)
            .join(account_roles, account_roles.c.account_id == Account.id)
            .join(Role, Role.id == account_roles.c.role_id)
            .where(
                Account.tenant_id == tenant_id,
                Account.is_active.is_(False),
                Role.tenant_id == tenant_id,
                Role.name == "admin",
            )
        )
        if initial_admin_id is not None:
            stmt = stmt.where(Account.id == initial_admin_id)
        account = (await self._db.execute(stmt.limit(1))).scalar_one_or_none()
        if account is None:
            raise InitialAdminNotPending("没有待激活的初始管理员")

        result = await generate_password_reset(
            db=self._db,
            account_id_str=str(account.id),
            tenant_id=tenant_id,
            cache=self._cache,
            operator_id=operator_id,
            activate_account=True,
            activation_opening_id=opening.id,
        )
        return ActivationTicket(initial_admin_id=account.id, url=result["reset_url"])

    async def cancel_pending(self, *, tenant_id: uuid.UUID) -> bool:
        """在租户终止事务内撤销待激活状态，使已签发链接无法完成激活。"""
        opening = (
            await self._db.execute(
                select(PlatformTenantOpening)
                .where(
                    PlatformTenantOpening.tenant_id == tenant_id,
                    PlatformTenantOpening.initial_admin_state == "pending_activation",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if opening is None:
            return False
        opening.initial_admin_state = "cancelled"
        return True
