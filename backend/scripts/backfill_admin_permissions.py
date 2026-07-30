"""一次性补齐历史品牌租户的空 admin 权限。

默认仅演练；显式传入 --apply 才提交。已有任意权限的 admin 角色一律跳过，
多个 admin 角色、无 admin 账号等歧义情况只报告，不自动修改。
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import _is_pg, async_session_factory
from app.models.tenant import (
    Permission,
    Role,
    Tenant,
    TenantType,
    account_roles,
    role_permissions,
)
from app.services.audit import write_audit_log
from app.utils.auth_rbac import WEB_ROLE_PERMISSIONS


async def backfill_empty_admin_permissions(db: AsyncSession) -> dict[str, list[str]]:
    brand_tenant_ids = {
        str(tenant_id)
        for tenant_id in (await db.execute(select(Tenant.id).where(Tenant.tenant_type == TenantType.brand))).scalars()
    }
    roles = list(
        (
            await db.execute(
                select(Role)
                .join(Tenant, Tenant.id == Role.tenant_id)
                .where(Tenant.tenant_type == TenantType.brand, Role.name == "admin")
                .order_by(Role.tenant_id, Role.created_at)
            )
        )
        .scalars()
        .all()
    )
    roles_by_tenant: dict[str, list[Role]] = defaultdict(list)
    for role in roles:
        roles_by_tenant[str(role.tenant_id)].append(role)

    report: dict[str, list[str]] = {"repaired": [], "skipped_custom": [], "manual_review": []}
    for tenant_id in sorted(brand_tenant_ids - roles_by_tenant.keys()):
        report["manual_review"].append(f"{tenant_id}:missing_admin_role")
    permission_codes = tuple(WEB_ROLE_PERMISSIONS["admin"])
    for tenant_id, tenant_roles in roles_by_tenant.items():
        if len(tenant_roles) != 1:
            report["manual_review"].append(f"{tenant_id}:multiple_admin_roles")
            continue
        role = tenant_roles[0]
        account_count = await db.scalar(
            select(func.count()).select_from(account_roles).where(account_roles.c.role_id == role.id)
        )
        if not account_count:
            report["manual_review"].append(f"{tenant_id}:admin_role_without_account")
            continue
        link_count = await db.scalar(
            select(func.count()).select_from(role_permissions).where(role_permissions.c.role_id == role.id)
        )
        if link_count:
            report["skipped_custom"].append(tenant_id)
            continue

        existing_permissions = {
            permission.code: permission
            for permission in (
                await db.execute(
                    select(Permission).where(
                        Permission.tenant_id == role.tenant_id,
                        Permission.code.in_(permission_codes),
                    )
                )
            )
            .scalars()
            .all()
        }
        missing = [
            Permission(
                tenant_id=role.tenant_id,
                code=code,
                description=f"品牌管理员默认权限：{code}",
            )
            for code in permission_codes
            if code not in existing_permissions
        ]
        db.add_all(missing)
        await db.flush()
        for permission in missing:
            existing_permissions[permission.code] = permission
        await db.execute(
            role_permissions.insert(),
            [{"role_id": role.id, "permission_id": existing_permissions[code].id} for code in permission_codes],
        )
        await write_audit_log(
            db,
            operator_id="maintenance:backfill-admin-permissions",
            target_tenant_id=tenant_id,
            action="backfill_admin_permissions",
            resource=f"role:{role.id}",
            details={"permission_count": len(permission_codes)},
        )
        report["repaired"].append(tenant_id)
    return report


async def _run(*, apply: bool) -> None:
    async with async_session_factory() as db:
        if _is_pg:
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        report = await backfill_empty_admin_permissions(db)
        if apply:
            await db.commit()
        else:
            await db.rollback()

    mode = "已提交" if apply else "仅演练，未提交"
    print(f"{mode}：补齐 {len(report['repaired'])} 个，保留自定义 {len(report['skipped_custom'])} 个")
    for item in report["manual_review"]:
        print(f"需人工检查：{item}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="提交修复；默认只演练并回滚")
    args = parser.parse_args()
    asyncio.run(_run(apply=args.apply))


if __name__ == "__main__":
    main()
