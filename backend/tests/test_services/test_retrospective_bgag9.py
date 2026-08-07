"""OpsTask 提醒负责人路由测试（beads: yimatong-bgag.9，PRD pilot-learning-retrospective §4.2/§5）。

PRD §4.2 "同时生成一条关联 OpsTask 提醒负责人完成复盘" + §5 代运营人员生成提醒/填写复盘。
负责人 = 该客户 active 代运营授权指向的 agency 租户内的运营账号（campaign:manage 权限，
对齐复盘 PATCH 权限），而非 granted_by（那是授权代运营的品牌方账号）。
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from uuid6 import uuid7

from app.models.tenant import (
    Account,
    AgencyAuthorization,
    AgencyAuthStatus,
    OpsTask,
    Organization,
    Permission,
    Role,
    Tenant,
    TenantType,
    account_roles,
    role_permissions,
)
from app.services.retrospective import generate_for_tenant, list_retrospectives
from app.utils.auth_rbac import WEB_ROLE_PERMISSIONS
from app.utils.security import hash_password
from tests.conftest import seed_pilot_launch_release, seed_pilot_tenant


async def _seed_agency_with_operator(db, *, operator_email="ops@agency.test"):
    """创建一个 agency 租户 + 一个有 campaign:manage 权限的运营账号，返回 (tenant, account)。"""
    agency_id = uuid7()
    agency = Tenant(
        id=agency_id,
        name="测试代运营",
        slug=f"agency-{agency_id.hex[:8]}",
        tenant_type=TenantType.agency,
    )
    org = Organization(id=uuid7(), tenant_id=agency_id, name="运营总部")
    db.add_all([agency, org])
    await db.flush()

    account = Account(
        id=uuid7(),
        tenant_id=agency_id,
        organization_id=org.id,
        email=operator_email,
        hashed_password=hash_password("Password1"),
        name="代运营负责人",
    )
    # 角色 + 权限（复用 WEB_ROLE_PERMISSIONS["operator"]，含 campaign:manage）
    role = Role(tenant_id=agency_id, name="operator", description="运营")
    perms = [
        Permission(tenant_id=agency_id, code=code, description=f"test {code}")
        for code in WEB_ROLE_PERMISSIONS["operator"]
    ]
    db.add_all([account, role, *perms])
    await db.flush()
    await db.execute(account_roles.insert().values(account_id=account.id, role_id=role.id))
    if perms:
        await db.execute(
            role_permissions.insert(),
            [{"role_id": role.id, "permission_id": p.id} for p in perms],
        )
    await db.flush()
    return agency, account


@pytest.mark.asyncio
async def test_reminder_ops_task_assigned_to_agency_operator(db):
    """有 active 代运营授权：OpsTask.assigned_to 指向 agency 租户的运营负责人（非 granted_by）。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    client_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, client_id, launched_at=launched)
    agency, operator = await _seed_agency_with_operator(db)
    # granted_by 是品牌方账号（授权代运营的人），不应作为负责人
    brand_grantor = uuid.uuid4()
    db.add(
        AgencyAuthorization(
            agency_tenant_id=agency.id,
            client_tenant_id=client_id,
            scope=["analytics"],
            status=AgencyAuthStatus.active,
            granted_by=brand_grantor,
        )
    )
    await db.flush()

    await generate_for_tenant(db, client_id, now=launched + timedelta(days=10))
    retro = (await list_retrospectives(db, client_id))[0]
    task = await db.get(OpsTask, retro.ops_task_id)
    assert task is not None
    # 路由到 agency 运营负责人，而非 granted_by（品牌方）
    assert task.assigned_to == operator.id
    assert task.assigned_to != brand_grantor


@pytest.mark.asyncio
async def test_reminder_ops_task_unassigned_without_authorization(db):
    """无代运营授权：assigned_to 为 None（任务进入待认领池）。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)

    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=10))
    retro = (await list_retrospectives(db, tenant_id))[0]
    task = await db.get(OpsTask, retro.ops_task_id)
    assert task is not None
    assert task.assigned_to is None


@pytest.mark.asyncio
async def test_reminder_ops_task_ignores_revoked_authorization(db):
    """已撤销（revoked）的代运营授权不作为负责人来源。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    client_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, client_id, launched_at=launched)
    agency, operator = await _seed_agency_with_operator(db, operator_email="ops2@agency.test")
    db.add(
        AgencyAuthorization(
            agency_tenant_id=agency.id,
            client_tenant_id=client_id,
            scope=["analytics"],
            status=AgencyAuthStatus.revoked,
            granted_by=uuid.uuid4(),
        )
    )
    await db.flush()

    await generate_for_tenant(db, client_id, now=launched + timedelta(days=10))
    retro = (await list_retrospectives(db, client_id))[0]
    task = await db.get(OpsTask, retro.ops_task_id)
    assert task is not None
    assert task.assigned_to is None  # revoked 授权 → 无 agency_tenant_id → None


@pytest.mark.asyncio
async def test_reminder_ops_task_skips_inactive_operator(db):
    """agency 租户内运营账号被停用（is_active=False）→ 不路由给该账号，回退 None。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    client_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, client_id, launched_at=launched)
    agency, operator = await _seed_agency_with_operator(db, operator_email="inactive@agency.test")
    operator.is_active = False
    await db.flush()
    db.add(
        AgencyAuthorization(
            agency_tenant_id=agency.id,
            client_tenant_id=client_id,
            scope=["analytics"],
            status=AgencyAuthStatus.active,
            granted_by=uuid.uuid4(),
        )
    )
    await db.flush()

    await generate_for_tenant(db, client_id, now=launched + timedelta(days=10))
    retro = (await list_retrospectives(db, client_id))[0]
    task = await db.get(OpsTask, retro.ops_task_id)
    assert task is not None
    assert task.assigned_to is None  # 唯一运营账号已停用
