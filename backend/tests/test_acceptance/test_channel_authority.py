"""Focused PostgreSQL acceptance for U07A channel authority."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import database
from app.main import app
from app.middleware.rate_limit import rate_limiter
from app.services.channel import list_allocations
from app.services.redis_cache import AsyncRedisCache
from app.utils.security import create_access_token, hash_password
from tests.test_acceptance.conftest import seed_baseline

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _seed_environment(owner_url: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "database_url": owner_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"),
            "control_database_url": owner_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@"),
            "callback_database_url": owner_url.replace("yimatong:yimatong@", "yimatong_callback:yimatong_callback@"),
            "migration_database_url": owner_url,
            "environment": "test",
        }
    )
    return environment


def _run_seed_command(owner_url: str, *arguments: str, rich: bool = False) -> None:
    command = (
        ["uv", "run", "python", "scripts/seed_demo.py", *arguments]
        if rich
        else ["uv", "run", "python", "-m", "app.cli", *arguments]
    )
    result = subprocess.run(
        command,
        cwd=BACKEND_DIR,
        env=_seed_environment(owner_url),
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def _runtime(database_url: str) -> asyncpg.Connection:
    return await asyncpg.connect(
        database_url.replace("postgresql+asyncpg://", "postgresql://").replace(
            "yimatong:yimatong@", "yimatong_app:yimatong_app@"
        )
    )


async def test_channel_authority_is_actor_bound_idempotent_and_capacity_safe(migrated_pg_url: str) -> None:
    baseline = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(baseline["baseline_tenant"]["id"])
    batch_id = uuid.UUID(baseline["code_batch"]["id"])
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    runtime = await _runtime(migrated_pg_url)
    try:
        account = await owner.fetchrow(
            "SELECT id,auth_version FROM accounts WHERE tenant_id=$1 AND email=$2",
            tenant_id,
            baseline["baseline_tenant"]["admin_email"],
        )
        assert account is not None
        session_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,now()+interval '1 hour',now(),now())",
            session_id,
            tenant_id,
            account["id"],
            account["auth_version"],
            uuid.uuid4().hex,
        )
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))

        distributor_id = uuid.uuid4()
        audit_id = uuid.uuid4()
        create_args = (
            tenant_id,
            session_id,
            audit_id,
            distributor_id,
            "dist-create",
            "华东经销商",
            f"DIST-{uuid.uuid4().hex[:8]}",
            None,
            "ciphertext-first",
            "phone-hash-first",
            "active",
        )
        first = await runtime.fetchrow(
            "SELECT * FROM create_channel_distributor($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)", *create_args
        )
        replay = await runtime.fetchrow(
            "SELECT * FROM create_channel_distributor($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            *(
                create_args[:2]
                + (uuid.uuid4(), uuid.uuid4())
                + create_args[4:8]
                + ("ciphertext-retry",)
                + create_args[9:]
            ),
        )
        assert first is not None and replay is not None
        assert first["resource_id"] == replay["resource_id"] == distributor_id
        assert first["replayed"] is False and replay["replayed"] is True
        assert await owner.fetchval("SELECT count(*) FROM channel_action_receipts WHERE tenant_id=$1", tenant_id) == 1
        assert await owner.fetchval("SELECT count(*) FROM platform_audit_log WHERE id=$1", audit_id) == 1
        assert (
            await owner.fetchval(
                "SELECT contact_phone_encrypted FROM distributors WHERE tenant_id=$1 AND id=$2",
                tenant_id,
                distributor_id,
            )
            == "ciphertext-first"
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await runtime.fetchrow(
                "SELECT * FROM create_channel_distributor($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                *(
                    create_args[:2]
                    + (uuid.uuid4(), uuid.uuid4())
                    + create_args[4:9]
                    + ("phone-hash-different",)
                    + create_args[10:]
                ),
            )

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with runtime.transaction():
                await runtime.execute(
                    "INSERT INTO distributors(id,tenant_id,name,code,status,version,contact_phone_recovery_state) "
                    "VALUES($1,$2,'forged','forged','active',1,'absent')",
                    uuid.uuid4(),
                    tenant_id,
                )

        region_id = uuid.uuid4()
        region_create_args = (
            tenant_id,
            session_id,
            uuid.uuid4(),
            region_id,
            "region-create",
            "华东区",
            f"REG-{uuid.uuid4().hex[:8]}",
            "上海",
            "上海",
            "city",
            "[]",
            distributor_id,
            "active",
        )
        created_region = await runtime.fetchrow(
            "SELECT * FROM create_channel_region($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13)",
            *region_create_args,
        )
        replayed_region = await runtime.fetchrow(
            "SELECT * FROM create_channel_region($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13)",
            *(region_create_args[:2] + (uuid.uuid4(), uuid.uuid4()) + region_create_args[4:]),
        )
        assert created_region is not None and replayed_region is not None
        assert replayed_region["resource_id"] == region_id and replayed_region["replayed"] is True
        store_id = uuid.uuid4()
        store_create_args = (
            tenant_id,
            session_id,
            uuid.uuid4(),
            store_id,
            "store-create",
            "静安门店",
            f"STORE-{uuid.uuid4().hex[:8]}",
            region_id,
            distributor_id,
            "上海市静安区",
            "active",
        )
        created_store = await runtime.fetchrow(
            "SELECT * FROM create_channel_store($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            *store_create_args,
        )
        replayed_store = await runtime.fetchrow(
            "SELECT * FROM create_channel_store($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            *(store_create_args[:2] + (uuid.uuid4(), uuid.uuid4()) + store_create_args[4:]),
        )
        assert created_store is not None and replayed_store is not None
        assert replayed_store["resource_id"] == store_id and replayed_store["replayed"] is True

        distributor_update_args = (
            tenant_id,
            session_id,
            uuid.uuid4(),
            distributor_id,
            1,
            "dist-update",
            "华东经销商更新",
            None,
            "ciphertext-update-first",
            "phone-hash-first",
            "active",
        )
        updated_distributor = await runtime.fetchrow(
            "SELECT * FROM update_channel_distributor($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            *distributor_update_args,
        )
        replayed_distributor_update = await runtime.fetchrow(
            "SELECT * FROM update_channel_distributor($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            *(
                distributor_update_args[:2]
                + (uuid.uuid4(),)
                + distributor_update_args[3:8]
                + ("ciphertext-update-retry",)
                + distributor_update_args[9:]
            ),
        )
        assert updated_distributor is not None and replayed_distributor_update is not None
        assert updated_distributor["version"] == 2 and replayed_distributor_update["replayed"] is True

        store_update_args = (
            tenant_id,
            session_id,
            uuid.uuid4(),
            store_id,
            1,
            "store-update",
            "静安门店更新",
            region_id,
            distributor_id,
            "上海市静安区更新",
            "active",
        )
        updated_store = await runtime.fetchrow(
            "SELECT * FROM update_channel_store($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)", *store_update_args
        )
        replayed_store_update = await runtime.fetchrow(
            "SELECT * FROM update_channel_store($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            *(store_update_args[:2] + (uuid.uuid4(),) + store_update_args[3:]),
        )
        assert updated_store is not None and replayed_store_update is not None
        assert updated_store["version"] == 2 and replayed_store_update["replayed"] is True

        batch_assign_args = (
            tenant_id,
            session_id,
            uuid.uuid4(),
            batch_id,
            "batch-assign",
            distributor_id,
            region_id,
        )
        assigned_batch = await runtime.fetchrow(
            "SELECT * FROM assign_code_batch_channel($1,$2,$3,$4,$5,$6,$7)", *batch_assign_args
        )
        replayed_batch = await runtime.fetchrow(
            "SELECT * FROM assign_code_batch_channel($1,$2,$3,$4,$5,$6,$7)",
            *(batch_assign_args[:2] + (uuid.uuid4(),) + batch_assign_args[3:]),
        )
        assert assigned_batch is not None and replayed_batch is not None
        assert replayed_batch["resource_id"] == batch_id and replayed_batch["replayed"] is True

        auto_distributor_id = uuid.uuid4()
        auto_distributor_args = (
            tenant_id,
            session_id,
            uuid.uuid4(),
            auto_distributor_id,
            "dist-create-auto-code",
            "自动编码经销商",
            None,
            None,
            None,
            None,
            "active",
        )
        auto_distributor = await runtime.fetchrow(
            "SELECT * FROM create_channel_distributor($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            *auto_distributor_args,
        )
        auto_distributor_replay = await runtime.fetchrow(
            "SELECT * FROM create_channel_distributor($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            *(
                auto_distributor_args[:2]
                + (uuid.uuid4(), uuid.uuid4())
                + auto_distributor_args[4:6]
                + ("   ",)
                + auto_distributor_args[7:]
            ),
        )
        assert auto_distributor is not None and auto_distributor_replay is not None
        assert auto_distributor["resource_id"] == auto_distributor_replay["resource_id"] == auto_distributor_id
        assert auto_distributor_replay["replayed"] is True
        assert (
            await owner.fetchval(
                "SELECT code FROM distributors WHERE tenant_id=$1 AND id=$2", tenant_id, auto_distributor_id
            )
            == f"DIST-{auto_distributor_id.hex.upper()}"
        )

        auto_region_id = uuid.uuid4()
        await runtime.fetchrow(
            "SELECT * FROM create_channel_region($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13)",
            tenant_id,
            session_id,
            uuid.uuid4(),
            auto_region_id,
            "region-create-auto-code",
            "自动编码区域",
            None,
            "上海",
            "上海",
            "city",
            None,
            auto_distributor_id,
            "active",
        )
        assert (
            await owner.fetchval("SELECT code FROM regions WHERE tenant_id=$1 AND id=$2", tenant_id, auto_region_id)
            == f"REG-{auto_region_id.hex.upper()}"
        )
        assert await owner.fetchval(
            "SELECT coverage_areas IS NULL FROM regions WHERE tenant_id=$1 AND id=$2", tenant_id, auto_region_id
        )
        updated_auto_region = await runtime.fetchrow(
            "SELECT * FROM update_channel_region($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13)",
            tenant_id,
            session_id,
            uuid.uuid4(),
            auto_region_id,
            1,
            "region-update-null-coverage",
            "自动编码区域",
            "上海",
            "上海",
            "city",
            None,
            auto_distributor_id,
            "active",
        )
        assert updated_auto_region is not None and updated_auto_region["version"] == 2
        replayed_auto_region = await runtime.fetchrow(
            "SELECT * FROM update_channel_region($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13)",
            tenant_id,
            session_id,
            uuid.uuid4(),
            auto_region_id,
            1,
            "region-update-null-coverage",
            "自动编码区域",
            "上海",
            "上海",
            "city",
            None,
            auto_distributor_id,
            "active",
        )
        assert replayed_auto_region is not None and replayed_auto_region["replayed"] is True
        assert await owner.fetchval(
            "SELECT coverage_areas IS NULL FROM regions WHERE tenant_id=$1 AND id=$2", tenant_id, auto_region_id
        )

        auto_store_id = uuid.uuid4()
        await runtime.fetchrow(
            "SELECT * FROM create_channel_store($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            tenant_id,
            session_id,
            uuid.uuid4(),
            auto_store_id,
            "store-create-auto-code",
            "自动编码门店",
            "",
            auto_region_id,
            auto_distributor_id,
            None,
            "active",
        )
        assert (
            await owner.fetchval("SELECT code FROM stores WHERE tenant_id=$1 AND id=$2", tenant_id, auto_store_id)
            == f"STORE-{auto_store_id.hex.upper()}"
        )

        duplicate_audit_id = uuid.uuid4()
        with pytest.raises(asyncpg.UniqueViolationError):
            await runtime.fetchrow(
                "SELECT * FROM create_channel_distributor($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                tenant_id,
                session_id,
                duplicate_audit_id,
                uuid.uuid4(),
                "dist-create-duplicate-code",
                "重复显式编码",
                create_args[6],
                None,
                None,
                None,
                "active",
            )
        assert await owner.fetchval("SELECT count(*) FROM platform_audit_log WHERE id=$1", duplicate_audit_id) == 0

        allocation_id = uuid.uuid4()
        allocation_audit = uuid.uuid4()
        allocation_args = (
            tenant_id,
            session_id,
            allocation_audit,
            allocation_id,
            "allocation-create",
            batch_id,
            "store",
            store_id,
            1,
            "首次门店配货",
        )
        allocated = await runtime.fetchrow(
            "SELECT * FROM allocate_code_batch_channel($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)", *allocation_args
        )
        allocated_replay = await runtime.fetchrow(
            "SELECT * FROM allocate_code_batch_channel($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
            *(allocation_args[:2] + (uuid.uuid4(), uuid.uuid4()) + allocation_args[4:]),
        )
        assert allocated is not None and allocated_replay is not None
        assert allocated["allocation_id"] == allocated_replay["allocation_id"] == allocation_id
        assert allocated_replay["replayed"] is True
        assert (
            await owner.fetchval(
                "SELECT sum(quantity) FROM code_allocations WHERE tenant_id=$1 AND batch_id=$2 AND effective_to IS NULL AND status='active'",
                tenant_id,
                batch_id,
            )
            == 1
        )

        with pytest.raises(asyncpg.CheckViolationError):
            await runtime.fetchrow(
                "SELECT * FROM allocate_code_batch_channel($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                tenant_id,
                session_id,
                uuid.uuid4(),
                uuid.uuid4(),
                "allocation-over-capacity",
                batch_id,
                "store",
                store_id,
                baseline["code_batch"]["quantity"] + 1,
                "超额配货应失败",
            )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM channel_action_receipts WHERE tenant_id=$1 AND action='allocate_codes'",
                tenant_id,
            )
            == 1
        )

        reassign_id = uuid.uuid4()
        reassign_args = (
            tenant_id,
            session_id,
            uuid.uuid4(),
            reassign_id,
            "allocation-reassign",
            allocation_id,
            1,
            "region",
            region_id,
            2,
            "门店调整为区域统配",
        )
        reassigned = await runtime.fetchrow(
            "SELECT * FROM reassign_code_batch_channel($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            *reassign_args,
        )
        reassigned_replay = await runtime.fetchrow(
            "SELECT * FROM reassign_code_batch_channel($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            *(reassign_args[:2] + (uuid.uuid4(), uuid.uuid4()) + reassign_args[4:]),
        )
        assert reassigned is not None and reassigned_replay is not None
        assert reassigned["version"] == 2 and reassigned_replay["replayed"] is True
        assert (
            await owner.fetchval(
                "SELECT change_reason FROM code_allocations WHERE tenant_id=$1 AND id=$2",
                tenant_id,
                reassign_id,
            )
            == "门店调整为区域统配"
        )

        archive_id = uuid.uuid4()
        archive_args = (
            tenant_id,
            session_id,
            uuid.uuid4(),
            archive_id,
            "allocation-archive",
            reassign_id,
            2,
            "本次流向已结束",
        )
        archived = await runtime.fetchrow(
            "SELECT * FROM archive_code_batch_allocation($1,$2,$3,$4,$5,$6,$7,$8)", *archive_args
        )
        archived_replay = await runtime.fetchrow(
            "SELECT * FROM archive_code_batch_allocation($1,$2,$3,$4,$5,$6,$7,$8)",
            *(archive_args[:2] + (uuid.uuid4(), uuid.uuid4()) + archive_args[4:]),
        )
        assert archived is not None and archived_replay is not None
        assert archived["version"] == 3 and archived["status"] == "archived"
        assert archived_replay["replayed"] is True
        with pytest.raises(asyncpg.CheckViolationError):
            await runtime.fetchrow(
                "SELECT * FROM reassign_code_batch_channel($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                tenant_id,
                session_id,
                uuid.uuid4(),
                uuid.uuid4(),
                "allocation-reassign-tombstone",
                archive_id,
                3,
                "store",
                store_id,
                1,
                "墓碑不可重分配",
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await runtime.fetchrow(
                "SELECT * FROM archive_code_batch_allocation($1,$2,$3,$4,$5,$6,$7,$8)",
                tenant_id,
                session_id,
                uuid.uuid4(),
                uuid.uuid4(),
                "allocation-rearchive-tombstone",
                archive_id,
                3,
                "墓碑不可重复归档",
            )
        versions = await owner.fetch(
            "SELECT version,status,effective_to IS NULL AS is_current,change_reason "
            "FROM code_allocations WHERE tenant_id=$1 AND allocation_root_id=$2 ORDER BY version",
            tenant_id,
            allocation_id,
        )
        assert [(row["version"], row["status"], row["is_current"]) for row in versions] == [
            (1, "active", False),
            (2, "active", False),
            (3, "archived", True),
        ]
        assert [row["change_reason"] for row in versions] == [
            "首次门店配货",
            "门店调整为区域统配",
            "本次流向已结束",
        ]
        row_count_before_terminal_retries = await owner.fetchval(
            "SELECT count(*) FROM code_allocations WHERE tenant_id=$1 AND allocation_root_id=$2",
            tenant_id,
            allocation_id,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await runtime.fetchrow(
                "SELECT * FROM reassign_code_batch_channel($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                tenant_id,
                session_id,
                uuid.uuid4(),
                uuid.uuid4(),
                "archived-allocation-reassign",
                archive_id,
                3,
                "store",
                store_id,
                1,
                "归档后不得重分配",
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await runtime.fetchrow(
                "SELECT * FROM archive_code_batch_allocation($1,$2,$3,$4,$5,$6,$7,$8)",
                tenant_id,
                session_id,
                uuid.uuid4(),
                uuid.uuid4(),
                "archived-allocation-rearchive",
                archive_id,
                3,
                "归档后不得再次归档",
            )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM code_allocations WHERE tenant_id=$1 AND allocation_root_id=$2",
                tenant_id,
                allocation_id,
            )
            == row_count_before_terminal_retries
            == 3
        )
        # The archived v3 row is an immutable terminal tombstone.  It reserves
        # the root/version chain, but it is not a live allocation for capacity
        # or resolver/current-state reads.
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM code_allocations WHERE tenant_id=$1 AND allocation_root_id=$2 "
                "AND effective_to IS NULL AND status='active'",
                tenant_id,
                allocation_id,
            )
            == 0
        )
        assert (
            await owner.fetchval(
                "SELECT COALESCE(sum(quantity),0) FROM code_allocations "
                "WHERE tenant_id=$1 AND batch_id=$2 AND effective_to IS NULL AND status='active'",
                tenant_id,
                batch_id,
            )
            == 0
        )
        runtime_engine = create_async_engine(
            migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
        )
        runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with runtime_factory() as session:
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant, false)"), {"tenant": str(tenant_id)}
                )
                current_rows, current_total = await list_allocations(session, tenant_id, batch_id=batch_id)
                history_rows, history_total = await list_allocations(
                    session, tenant_id, batch_id=batch_id, include_history=True
                )
                assert current_total == 0 and current_rows == []
                assert history_total == 3
                assert [row.version for row in history_rows] == [3, 2, 1]
        finally:
            await runtime_engine.dispose()

        scope_id = uuid.uuid4()
        scope_args = (
            tenant_id,
            session_id,
            uuid.uuid4(),
            scope_id,
            "scope-set",
            account["id"],
            "store",
            store_id,
        )
        scope = await runtime.fetchrow("SELECT * FROM set_account_channel_scope($1,$2,$3,$4,$5,$6,$7,$8)", *scope_args)
        scope_replay = await runtime.fetchrow(
            "SELECT * FROM set_account_channel_scope($1,$2,$3,$4,$5,$6,$7,$8)",
            *(scope_args[:2] + (uuid.uuid4(), uuid.uuid4()) + scope_args[4:]),
        )
        assert scope is not None and scope_replay is not None
        assert scope["resource_id"] == scope_id and scope_replay["replayed"] is True
        visible_scope = await runtime.fetchrow(
            "SELECT * FROM get_my_channel_scope($1,$2,$3)", tenant_id, session_id, "store"
        )
        assert visible_scope is not None
        assert visible_scope["scope_id"] == scope_id and visible_scope["target_id"] == store_id
        deleted_scope = await runtime.fetchrow(
            "SELECT * FROM delete_account_channel_scope($1,$2,$3,$4,$5,$6)",
            tenant_id,
            session_id,
            uuid.uuid4(),
            scope_id,
            1,
            "scope-delete",
        )
        assert deleted_scope is not None and deleted_scope["status"] == "deleted"
        deleted_scope_replay = await runtime.fetchrow(
            "SELECT * FROM delete_account_channel_scope($1,$2,$3,$4,$5,$6)",
            tenant_id,
            session_id,
            uuid.uuid4(),
            scope_id,
            1,
            "scope-delete",
        )
        assert deleted_scope_replay is not None and deleted_scope_replay["replayed"] is True

        role_permission = await owner.fetchrow(
            "SELECT role_permission.tenant_id,role_permission.role_id,role_permission.permission_id "
            "FROM account_roles account_role "
            "JOIN role_permissions role_permission ON role_permission.tenant_id=account_role.tenant_id "
            "AND role_permission.role_id=account_role.role_id "
            "JOIN permissions permission ON permission.tenant_id=role_permission.tenant_id "
            "AND permission.id=role_permission.permission_id "
            "WHERE account_role.tenant_id=$1 AND account_role.account_id=$2 "
            "AND permission.code='channel:allocate' LIMIT 1",
            tenant_id,
            account["id"],
        )
        assert role_permission is not None
        await owner.execute(
            "DELETE FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3",
            role_permission["tenant_id"],
            role_permission["role_id"],
            role_permission["permission_id"],
        )
        before_denied = await owner.fetchval("SELECT count(*) FROM code_allocations WHERE tenant_id=$1", tenant_id)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.fetchrow(
                "SELECT * FROM allocate_code_batch_channel($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                tenant_id,
                session_id,
                uuid.uuid4(),
                uuid.uuid4(),
                "revoked-allocation",
                batch_id,
                "store",
                store_id,
                1,
                "撤权后不得分配",
            )
        assert (
            await owner.fetchval("SELECT count(*) FROM code_allocations WHERE tenant_id=$1", tenant_id) == before_denied
        )
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            role_permission["tenant_id"],
            role_permission["role_id"],
            role_permission["permission_id"],
        )

        concurrent_runtime = await _runtime(migrated_pg_url)
        try:
            await concurrent_runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
            capacity = baseline["code_batch"]["quantity"]

            async def allocate_full(conn: asyncpg.Connection, suffix: str) -> asyncpg.Record:
                return await conn.fetchrow(
                    "SELECT * FROM allocate_code_batch_channel($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                    tenant_id,
                    session_id,
                    uuid.uuid4(),
                    uuid.uuid4(),
                    f"concurrent-{suffix}",
                    batch_id,
                    "store",
                    store_id,
                    capacity,
                    f"并发容量竞争 {suffix}",
                )

            outcomes = await asyncio.gather(
                allocate_full(runtime, "a"), allocate_full(concurrent_runtime, "b"), return_exceptions=True
            )
            assert sum(isinstance(outcome, asyncpg.Record) for outcome in outcomes) == 1
            assert sum(isinstance(outcome, asyncpg.CheckViolationError) for outcome in outcomes) == 1
            replacement = next(outcome for outcome in outcomes if isinstance(outcome, asyncpg.Record))
            assert replacement["allocation_id"] == replacement["allocation_root_id"]
            assert replacement["allocation_root_id"] != allocation_id
            assert (
                await owner.fetchval(
                    "SELECT sum(quantity) FROM code_allocations WHERE tenant_id=$1 AND batch_id=$2 "
                    "AND effective_to IS NULL AND status='active'",
                    tenant_id,
                    batch_id,
                )
                == capacity
            )
        finally:
            await concurrent_runtime.close()

        archive_store_args = (
            tenant_id,
            session_id,
            uuid.uuid4(),
            store_id,
            2,
            "store-archive",
        )
        archived_store = await runtime.fetchrow(
            "SELECT * FROM archive_channel_store($1,$2,$3,$4,$5,$6)", *archive_store_args
        )
        replayed_store_archive = await runtime.fetchrow(
            "SELECT * FROM archive_channel_store($1,$2,$3,$4,$5,$6)",
            *(archive_store_args[:2] + (uuid.uuid4(),) + archive_store_args[3:]),
        )
        assert archived_store is not None and replayed_store_archive is not None
        assert archived_store["version"] == 3 and replayed_store_archive["replayed"] is True
    finally:
        await runtime.close()
        await owner.close()


async def test_region_update_api_preserves_omitted_values_clears_explicit_nulls_and_replays(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(baseline["baseline_tenant"]["id"])
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    account = await owner.fetchrow(
        "SELECT id,auth_version FROM accounts WHERE tenant_id=$1 AND email=$2",
        tenant_id,
        baseline["baseline_tenant"]["admin_email"],
    )
    assert account is not None
    session_id = uuid.uuid4()
    await owner.execute(
        "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,now()+interval '1 hour',now(),now())",
        session_id,
        tenant_id,
        account["id"],
        account["auth_version"],
        uuid.uuid4().hex,
    )
    await owner.execute(
        "UPDATE tenants SET enabled_features=(coalesce(enabled_features,'{}'::json)::jsonb"
        "||'{\"channel_portal\":true}'::jsonb)::json "
        "WHERE id=$1",
        tenant_id,
    )

    runtime_engine = create_async_engine(runtime_dsn.replace("postgresql://", "postgresql+asyncpg://"))
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)

    async def cache_is_not_revoked(self, key: str) -> bool:
        return False

    monkeypatch.setattr(AsyncRedisCache, "is_token_revoked", cache_is_not_revoked)
    rate_limiter._cache._mem_store.clear()  # type: ignore[attr-defined]
    token = create_access_token(
        str(tenant_id),
        str(account["id"]),
        "admin",
        "brand",
        extra={"sid": str(session_id), "auth_version": account["auth_version"]},
    )

    def headers(idempotency_key: uuid.UUID) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Idempotency-Key": str(idempotency_key)}

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            first_distributor_key = uuid.uuid4()
            first_distributor = await client.post(
                "/api/v1/channels/distributors",
                json={"name": "华东验收经销商"},
                headers=headers(first_distributor_key),
            )
            first_distributor_replay = await client.post(
                "/api/v1/channels/distributors",
                json={"name": "华东验收经销商"},
                headers=headers(first_distributor_key),
            )
            second_distributor = await client.post(
                "/api/v1/channels/distributors",
                json={"name": "华南验收经销商"},
                headers=headers(uuid.uuid4()),
            )
            assert first_distributor.status_code == second_distributor.status_code == 201
            assert first_distributor_replay.status_code == 201
            assert first_distributor_replay.json() == first_distributor.json()
            first_distributor_id = first_distributor.json()["id"]
            second_distributor_id = second_distributor.json()["id"]
            assert first_distributor.json()["code"] == f"DIST-{uuid.UUID(first_distributor_id).hex.upper()}"
            assert second_distributor.json()["code"] == f"DIST-{uuid.UUID(second_distributor_id).hex.upper()}"

            created = await client.post(
                "/api/v1/channels/regions",
                json={
                    "name": "苏南区域",
                    "province": "江苏",
                    "city": "苏州",
                    "coverage_type": "city",
                    "coverage_areas": [{"province": "江苏", "city": "苏州"}],
                    "distributor_id": first_distributor_id,
                },
                headers=headers(uuid.uuid4()),
            )
            assert created.status_code == 201, created.text
            region_id = created.json()["id"]
            assert created.json()["code"] == f"REG-{uuid.UUID(region_id).hex.upper()}"

            store = await client.post(
                "/api/v1/channels/stores",
                json={
                    "name": "苏州验收门店",
                    "region_id": region_id,
                    "distributor_id": first_distributor_id,
                },
                headers=headers(uuid.uuid4()),
            )
            assert store.status_code == 201, store.text
            assert store.json()["code"] == f"STORE-{uuid.UUID(store.json()['id']).hex.upper()}"

            replay_key = uuid.uuid4()
            rename_payload = {"expected_version": 1, "name": "苏南核心区域"}
            renamed = await client.patch(
                f"/api/v1/channels/regions/{region_id}", json=rename_payload, headers=headers(replay_key)
            )
            assert renamed.status_code == 200, renamed.text
            assert {
                "version": 2,
                "name": "苏南核心区域",
                "province": "江苏",
                "city": "苏州",
                "coverage_type": "city",
                "coverage_areas": [{"province": "江苏", "city": "苏州"}],
                "distributor_id": first_distributor_id,
            }.items() <= renamed.json().items()

            replayed = await client.patch(
                f"/api/v1/channels/regions/{region_id}", json=rename_payload, headers=headers(replay_key)
            )
            assert replayed.status_code == 200, replayed.text
            assert replayed.json() == renamed.json()

            stale = await client.patch(
                f"/api/v1/channels/regions/{region_id}",
                json={"expected_version": 1, "name": "过期写入"},
                headers=headers(uuid.uuid4()),
            )
            assert stale.status_code == 409, stale.text

            moved = await client.patch(
                f"/api/v1/channels/regions/{region_id}",
                json={"expected_version": 2, "distributor_id": second_distributor_id},
                headers=headers(uuid.uuid4()),
            )
            assert moved.status_code == 200, moved.text
            assert moved.json()["version"] == 3
            assert moved.json()["distributor_id"] == second_distributor_id
            assert moved.json()["province"] == "江苏"

            cleared = await client.patch(
                f"/api/v1/channels/regions/{region_id}",
                json={
                    "expected_version": 3,
                    "province": None,
                    "city": None,
                    "coverage_areas": None,
                    "distributor_id": None,
                },
                headers=headers(uuid.uuid4()),
            )
            assert cleared.status_code == 200, cleared.text
            assert cleared.json()["version"] == 4
            for field in ("province", "city", "distributor_id"):
                assert cleared.json()[field] is None
            # The management response intentionally renders an unset coverage collection as empty.
            assert cleared.json()["coverage_areas"] == []

        row = await owner.fetchrow(
            "SELECT name,province,city,coverage_type,coverage_areas,distributor_id,status,version "
            "FROM regions WHERE tenant_id=$1 AND id=$2",
            tenant_id,
            uuid.UUID(region_id),
        )
        assert row is not None
        assert dict(row) == {
            "name": "苏南核心区域",
            "province": None,
            "city": None,
            "coverage_type": "city",
            "coverage_areas": None,
            "distributor_id": None,
            "status": "active",
            "version": 4,
        }
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM platform_audit_log WHERE target_tenant_id=$1 AND action='update_region' "
                "AND resource=$2 AND operator_id=$3",
                str(tenant_id),
                f"region:{region_id}",
                str(account["id"]),
            )
            == 3
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM channel_action_receipts WHERE tenant_id=$1 AND action='update_region' "
                "AND resource_id=$2",
                tenant_id,
                uuid.UUID(region_id),
            )
            == 3
        )
    finally:
        app.dependency_overrides.clear()
        await owner.close()
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_fresh_login_admin_can_reach_channel_read_surface(
    migrated_pg_url: str,
) -> None:
    baseline = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(baseline["baseline_tenant"]["id"])
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    await owner.execute(
        "UPDATE tenants SET enabled_features=(coalesce(enabled_features,'{}'::json)::jsonb"
        "||'{\"channel_portal\":true}'::jsonb)::json WHERE id=$1",
        tenant_id,
    )
    try:
        runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
        control_url = migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
        callback_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_callback:yimatong_callback@")
        env = os.environ.copy()
        for lower, upper, value in (
            ("database_url", "DATABASE_URL", runtime_url),
            ("migration_database_url", "MIGRATION_DATABASE_URL", migrated_pg_url),
            ("control_database_url", "CONTROL_DATABASE_URL", control_url),
            ("callback_database_url", "CALLBACK_DATABASE_URL", callback_url),
        ):
            env[lower] = value
            env[upper] = value
        env["redis_url"] = "redis://localhost:6380/15"
        env["REDIS_URL"] = env["redis_url"]
        env["LOGIN_EMAIL"] = baseline["baseline_tenant"]["admin_email"]
        env["LOGIN_PASSWORD"] = baseline["baseline_tenant"]["admin_password"]
        env["LOGIN_TENANT_SLUG"] = baseline["baseline_tenant"]["slug"]
        env["LOGIN_TENANT_ID"] = str(tenant_id)
        env["EXPECTED_DATABASE"] = migrated_pg_url.rsplit("/", 1)[-1]
        env["OWNER_DSN"] = owner_dsn
        script = textwrap.dedent(
            """
            import asyncio
            import json
            import os
            import uuid

            import asyncpg
            from httpx import ASGITransport, AsyncClient
            from sqlalchemy import text

            from app.core import database
            from app.main import app
            from app.utils.crypto import EnvKeyProvider, init_crypto

            init_crypto(EnvKeyProvider())

            async def run():
                selected = {}
                for name, factory in (
                    ("runtime", database.async_session_factory),
                    ("control", database.control_session_factory),
                    ("callback", database.callback_session_factory),
                ):
                    async with factory() as session:
                        selected[name] = await session.scalar(text("SELECT current_database()"))
                async with AsyncClient(
                    transport=ASGITransport(app=app, raise_app_exceptions=True),
                    base_url="http://test",
                ) as client:
                    login = await client.post(
                        "/api/v1/auth/login",
                        json={
                            "email": os.environ["LOGIN_EMAIL"],
                            "password": os.environ["LOGIN_PASSWORD"],
                            "tenant_slug": os.environ["LOGIN_TENANT_SLUG"],
                        },
                    )
                    headers = {
                        "Authorization": f"Bearer {login.json().get('access_token', '')}",
                    }
                    me = await client.get("/api/v1/auth/me", headers=headers)
                    channels = await client.get("/api/v1/channels/overview", headers=headers)
                    create_key = str(uuid.uuid4())
                    create_headers = {**headers, "Idempotency-Key": create_key}
                    create_body = {"name": "PG phone replay distributor", "contact_phone": "13800138000"}
                    created = await client.post(
                        "/api/v1/channels/distributors", json=create_body, headers=create_headers
                    )
                    create_replay = await client.post(
                        "/api/v1/channels/distributors", json=create_body, headers=create_headers
                    )
                    create_conflict = await client.post(
                        "/api/v1/channels/distributors",
                        json={**create_body, "contact_phone": "13900139000"},
                        headers=create_headers,
                    )
                    distributor_id = created.json().get("id")
                    update_key = str(uuid.uuid4())
                    update_headers = {**headers, "Idempotency-Key": update_key}
                    update_body = {"expected_version": 1, "contact_phone": "13700137000"}
                    updated = await client.patch(
                        f"/api/v1/channels/distributors/{distributor_id}",
                        json=update_body,
                        headers=update_headers,
                    )
                    update_replay = await client.patch(
                        f"/api/v1/channels/distributors/{distributor_id}",
                        json=update_body,
                        headers=update_headers,
                    )
                    update_conflict = await client.patch(
                        f"/api/v1/channels/distributors/{distributor_id}",
                        json={"expected_version": 1, "contact_phone": "13600136000"},
                        headers=update_headers,
                    )
                owner = await asyncpg.connect(os.environ["OWNER_DSN"])
                try:
                    receipt_count = await owner.fetchval(
                        "SELECT count(*) FROM channel_action_receipts "
                        "WHERE tenant_id=$1 AND idempotency_key IN ($2,$3)",
                        uuid.UUID(os.environ["LOGIN_TENANT_ID"]), create_key, update_key,
                    )
                    audit_count = await owner.fetchval(
                        "SELECT count(*) FROM platform_audit_log "
                        "WHERE target_tenant_id=$1 AND resource=$2",
                        os.environ["LOGIN_TENANT_ID"], f"distributor:{distributor_id}",
                    )
                finally:
                    await owner.close()
                print(json.dumps({
                    "databases": selected,
                    "login": login.status_code,
                    "me": me.status_code,
                    "channels": channels.status_code,
                    "channels_body": channels.text,
                    "created": [created.status_code, create_replay.status_code, create_conflict.status_code],
                    "created_body": created.text,
                    "created_same": created.json() == create_replay.json(),
                    "updated": [updated.status_code, update_replay.status_code, update_conflict.status_code],
                    "updated_same": updated.json() == update_replay.json(),
                    "updated_version": updated.json().get("version"),
                    "updated_mask": updated.json().get("contact_phone_masked"),
                    "receipt_count": receipt_count,
                    "audit_count": audit_count,
                }))

            asyncio.run(run())
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).parents[2],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        evidence = json.loads(result.stdout.strip().splitlines()[-1])
        assert set(evidence["databases"].values()) == {env["EXPECTED_DATABASE"]}
        assert evidence["login"] == 200, evidence
        assert evidence["me"] == 200, evidence
        assert evidence["channels"] == 200, evidence
        assert evidence["created"] == [201, 201, 409], evidence["created_body"]
        assert evidence["created_same"] is True, evidence
        assert evidence["updated"] == [200, 200, 409], evidence
        assert evidence["updated_same"] is True, evidence
        assert evidence["updated_version"] == 2, evidence
        assert evidence["updated_mask"] == "137****7000", evidence
        assert evidence["receipt_count"] == 2, evidence
        assert evidence["audit_count"] == 2, evidence
    finally:
        await owner.close()


async def test_official_seed_channel_portal_identities_survive_rich_reconciliation_and_are_subject_bound(
    migrated_pg_url: str,
) -> None:
    """Prove the shipped demo credentials reach only their own channel portal."""

    await asyncio.to_thread(_run_seed_command, migrated_pg_url, "all")
    await asyncio.to_thread(_run_seed_command, migrated_pg_url, "generate", "--target", "demo", rich=True)

    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    viewer_id = uuid.uuid4()
    viewer_email = f"portal-viewer-{viewer_id.hex[:8]}@example.test"
    viewer_password = "Viewer1234"

    async def portal_snapshot() -> dict[str, tuple[str, int, str, str]]:
        rows = await owner.fetch(
            """
            SELECT account.email,account.id,account.auth_version,role.name AS role_name,
                   scope.scope_type,scope.target_id,
                   CASE scope.scope_type
                     WHEN 'distributor' THEN distributor.code
                     WHEN 'store' THEN store.code
                   END AS target_code
            FROM accounts account
            JOIN account_roles ar ON ar.tenant_id=account.tenant_id AND ar.account_id=account.id
            JOIN roles role ON role.tenant_id=ar.tenant_id AND role.id=ar.role_id
            JOIN account_channel_scopes scope
              ON scope.tenant_id=account.tenant_id AND scope.account_id=account.id
            LEFT JOIN distributors distributor
              ON scope.scope_type='distributor' AND distributor.tenant_id=scope.tenant_id
             AND distributor.id=scope.target_id
            LEFT JOIN stores store
              ON scope.scope_type='store' AND store.tenant_id=scope.tenant_id AND store.id=scope.target_id
            JOIN tenants tenant ON tenant.id=account.tenant_id
            WHERE tenant.slug='demo' AND account.email IN ('dist@demo.com','store@demo.com')
            ORDER BY account.email
            """
        )
        return {
            row["email"]: (
                str(row["id"]),
                row["auth_version"],
                row["role_name"],
                f"{row['scope_type']}:{row['target_code']}",
            )
            for row in rows
        }

    try:
        rich_snapshot = await portal_snapshot()
        assert rich_snapshot.keys() == {"dist@demo.com", "store@demo.com"}
        assert rich_snapshot["dist@demo.com"][2:] == ("distributor", "distributor:DEMO-DIST-EAST")
        assert rich_snapshot["store@demo.com"][2:] == ("store_guide", "store:DEMO-STORE-NJDL")

        # Re-run both supported reconciliation entrypoints. Neither may replace
        # the channel principals, demote their exact roles, or move their scope.
        await asyncio.to_thread(_run_seed_command, migrated_pg_url, "all")
        reconciled_snapshot = await portal_snapshot()
        assert reconciled_snapshot.keys() == rich_snapshot.keys()
        assert {email: (snapshot[0], snapshot[2], snapshot[3]) for email, snapshot in reconciled_snapshot.items()} == {
            email: (snapshot[0], snapshot[2], snapshot[3]) for email, snapshot in rich_snapshot.items()
        }
        # The first official run after rich data may intentionally reconcile
        # account organization/name and rotate auth_version. A second identical
        # run must be a no-op for identity and authorization state.
        await asyncio.to_thread(_run_seed_command, migrated_pg_url, "all")
        await asyncio.to_thread(
            _run_seed_command,
            migrated_pg_url,
            "baseline",
            "build",
            "--target",
            "baseline-base",
        )
        await asyncio.to_thread(
            _run_seed_command,
            migrated_pg_url,
            "baseline",
            "build",
            "--target",
            "baseline-base",
        )
        assert await portal_snapshot() == reconciled_snapshot

        tenant_id = await owner.fetchval("SELECT id FROM tenants WHERE slug='demo'")
        organization_id = await owner.fetchval(
            "SELECT organization_id FROM accounts WHERE tenant_id=$1 AND email='admin@demo.com'",
            tenant_id,
        )
        assert tenant_id is not None and organization_id is not None
        assert await owner.fetchval("SELECT count(*) FROM auth_sessions WHERE tenant_id=$1", tenant_id) == 0
        assert (
            await owner.fetchval(
                """
                SELECT count(*) FROM role_permissions rp
                JOIN roles role ON role.tenant_id=rp.tenant_id AND role.id=rp.role_id
                WHERE role.tenant_id=$1 AND role.name IN ('distributor','store_guide')
                """,
                tenant_id,
            )
            == 0
        )

        # A no-role account resolves to the canonical viewer principal. It is
        # temporary test data and is removed in the finally block below.
        await owner.execute(
            """
            INSERT INTO accounts(
              id,tenant_id,organization_id,email,hashed_password,name,is_active,
              auth_version,must_change_password,failed_login_attempts,created_at,updated_at
            ) VALUES($1,$2,$3,$4,$5,'Portal viewer',true,0,false,0,now(),now())
            """,
            viewer_id,
            tenant_id,
            organization_id,
            viewer_email,
            hash_password(viewer_password),
        )

        environment = _seed_environment(migrated_pg_url)
        environment.update(
            {
                "DEMO_TENANT_ID": str(tenant_id),
                "OWNER_DSN": owner_dsn,
                "VIEWER_EMAIL": viewer_email,
                "VIEWER_PASSWORD": viewer_password,
                "redis_url": "redis://localhost:6380/15",
                "REDIS_URL": "redis://localhost:6380/15",
            }
        )
        script = textwrap.dedent(
            """
            import asyncio
            import json
            import os
            import uuid

            from httpx import ASGITransport, AsyncClient
            from sqlalchemy import text

            from app.core import database
            from app.main import app
            from app.utils.crypto import EnvKeyProvider, init_crypto
            from app.utils.security import decode_token

            init_crypto(EnvKeyProvider())

            USERS = {
                "distributor": ("dist@demo.com", "Dist123456"),
                "store_guide": ("store@demo.com", "Store123456"),
                "viewer": (os.environ["VIEWER_EMAIL"], os.environ["VIEWER_PASSWORD"]),
            }

            async def run():
                tenant_id = uuid.UUID(os.environ["DEMO_TENANT_ID"])
                evidence = {}
                async with AsyncClient(
                    transport=ASGITransport(app=app, raise_app_exceptions=False),
                    base_url="http://test",
                ) as client:
                    for expected_role, (email, password) in USERS.items():
                        login = await client.post(
                            "/api/v1/auth/login",
                            json={"email": email, "password": password, "tenant_slug": "demo"},
                        )
                        token = login.json().get("access_token", "")
                        payload = decode_token(token) if token else {}
                        headers = {"Authorization": f"Bearer {token}"}
                        distributor = await client.get(
                            "/api/v1/channels/portal/distributor/summary", headers=headers
                        )
                        store = await client.get("/api/v1/channels/portal/store/summary", headers=headers)
                        overview = await client.get("/api/v1/channels/overview", headers=headers)
                        generic_write = await client.post(
                            "/api/v1/channels/distributors",
                            json={"name": "forbidden portal write"},
                            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                        )
                        direct_scope = None
                        if expected_role in {"distributor", "store_guide"}:
                            scope_type = "distributor" if expected_role == "distributor" else "store"
                            async with database.async_session_factory() as session, session.begin():
                                await session.execute(
                                    text("SELECT set_config('app.tenant_id',:tenant,true)"),
                                    {"tenant": str(tenant_id)},
                                )
                                direct_scope = (
                                    await session.execute(
                                        text(
                                            "SELECT scope_type,target_id,target_name FROM "
                                            "public.get_my_channel_scope(:tenant,:sid,:scope_type)"
                                        ),
                                        {
                                            "tenant": tenant_id,
                                            "sid": uuid.UUID(payload["sid"]),
                                            "scope_type": scope_type,
                                        },
                                    )
                                ).mappings().one()
                                direct_scope = {
                                    "scope_type": direct_scope["scope_type"],
                                    "target_id": str(direct_scope["target_id"]),
                                    "target_name": direct_scope["target_name"],
                                }
                        evidence[expected_role] = {
                            "login": login.status_code,
                            "jwt_role": payload.get("role"),
                            "sid": payload.get("sid"),
                            "distributor": distributor.status_code,
                            "distributor_scope": distributor.json().get("scope") if distributor.status_code == 200 else None,
                            "store": store.status_code,
                            "store_scope": store.json().get("scope") if store.status_code == 200 else None,
                            "overview": overview.status_code,
                            "generic_write": generic_write.status_code,
                            "direct_scope": direct_scope,
                        }
                print(json.dumps(evidence, ensure_ascii=False))

            asyncio.run(run())
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=BACKEND_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        evidence = json.loads(result.stdout.strip().splitlines()[-1])

        distributor = evidence["distributor"]
        assert distributor["login"] == 200 and distributor["jwt_role"] == "distributor"
        assert distributor["sid"] and distributor["distributor"] == 200
        assert distributor["distributor_scope"]["type"] == "distributor"
        assert distributor["direct_scope"]["scope_type"] == "distributor"
        assert distributor["store"] == 403
        assert distributor["overview"] == distributor["generic_write"] == 403

        store = evidence["store_guide"]
        assert store["login"] == 200 and store["jwt_role"] == "store_guide"
        assert store["sid"] and store["store"] == 200
        assert store["store_scope"]["type"] == "store"
        assert store["direct_scope"]["scope_type"] == "store"
        assert store["distributor"] == 403
        assert store["overview"] == store["generic_write"] == 403

        viewer = evidence["viewer"]
        assert viewer["login"] == 200 and viewer["jwt_role"] == "viewer"
        assert viewer["distributor"] == viewer["store"] == 403
        assert viewer["overview"] == viewer["generic_write"] == 403
    finally:
        await owner.execute("DELETE FROM auth_sessions WHERE account_id=$1", viewer_id)
        await owner.execute("DELETE FROM accounts WHERE id=$1", viewer_id)
        await owner.close()
