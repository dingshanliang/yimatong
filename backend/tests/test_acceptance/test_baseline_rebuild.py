"""yimatong-zgb1.1 验收门禁 — 从干净环境建立可重复扫码的基准租户。

最高层验收 seam：在真实 PostgreSQL 上证明
1. 干净 DB 完成全部迁移；
2. 基准租户 + 对照租户 + 完整首条扫码旅程业务数据可重复创建；
3. 连续两次重建无重复/状态漂移；
4. 真实 RLS 阻止对照租户读到基准租户任何业务行（SQLite 测不到）；
5. 基准品牌管理员经真实 API 具备生成码的默认权限；
6. 首扫原子性在持久化层成立（并发只产生 1 条 first_scan 事件）；
7. 重复扫码记录现状被证据暴露（不修复，留给后续票）。

权威资料：docs/01_product/BASELINE_ACCEPTANCE_MATRIX.md
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncGenerator

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.cli.baseline import (
    BASELINE_ADMIN_EMAIL,
    BASELINE_ADMIN_PASSWORD,
    BASELINE_TENANT_SLUG,
    CONTROL_TENANT_SLUG,
)
from app.core.database import get_db, get_db_with_bypass
from app.main import app
from tests.test_acceptance.conftest import seed_baseline
from tests.test_acceptance.verifier import (
    verify_baseline_presence,
    verify_duplicate_scan_recording,
    verify_first_scan_atomicity,
    verify_isolation_rls,
    verify_no_duplicate_on_rerun,
)

# 验收测试：需要真实 infra PG；默认不在普通 pytest 运行中执行
pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def _set_rls_context(
    conn: asyncpg.Connection,
    *,
    tenant_id: uuid.UUID | None = None,
    bypass: bool = False,
) -> None:
    """Set a complete RLS context so matrix cases cannot inherit prior state."""
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id) if tenant_id else "")
    await conn.execute("SELECT set_config('app.bypass_rls', $1, true)", "true" if bypass else "false")


async def _assert_write_denied(conn: asyncpg.Connection, query: str, *args: object) -> None:
    """Contain an expected write-privilege or WITH CHECK failure in a savepoint."""
    savepoint = conn.transaction()
    await savepoint.start()
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await conn.execute(query, *args)
    await savepoint.rollback()


# ── 门禁 1：干净 DB 完成迁移 ──────────────────────────────────────────────
# （migrated_pg_url fixture 本身就跑了 alembic upgrade head，若失败 fixture 会 fail）


class TestCleanEnvRebuild:
    """§4 干净环境重建门禁。"""

    async def test_clean_db_migrations_succeed(self, migrated_pg_url: str):
        """迁移已在 fixture 中执行；这里显式断言关键表存在。"""
        conn = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            row = await conn.fetchrow(
                "SELECT count(*) AS c FROM information_schema.tables "
                "WHERE table_name IN ('tenants','brands','products','code_items','scan_events',"
                "'pilot_milestones','retrospectives','pilot_milestone_corrections')"
            )
            assert row["c"] == 8, f"expected 8 core tables, got {row['c']}"
            # RLS 已启用（PG 信息架构字段名为 row_security，类型 yes/no）
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE relname = 'code_items'")
            assert rls is True, f"RLS not enabled on code_items (relrowsecurity={rls})"
            pilot_states = await conn.fetch(
                "SELECT cls.relname,cls.relrowsecurity,cls.relforcerowsecurity,"
                "string_agg(COALESCE(pg_get_expr(pol.polqual,pol.polrelid),'') || ' ' || "
                "COALESCE(pg_get_expr(pol.polwithcheck,pol.polrelid),''),' ') AS expressions "
                "FROM pg_class cls JOIN pg_namespace ns ON ns.oid=cls.relnamespace "
                "LEFT JOIN pg_policy pol ON pol.polrelid=cls.oid "
                "WHERE ns.nspname='public' AND cls.relname=ANY($1::text[]) "
                "GROUP BY cls.relname,cls.relrowsecurity,cls.relforcerowsecurity ORDER BY cls.relname",
                ["pilot_milestones", "retrospectives", "pilot_milestone_corrections"],
            )
            assert len(pilot_states) == 3
            assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in pilot_states)
            assert all("has_parameter_privilege" in row["expressions"] for row in pilot_states)
        finally:
            await conn.close()

    async def test_invite_registration_receipt_rejects_partial_completion(self, control_pg_conn):
        """幂等回执的 tenant id 与 slug 必须作为同一个完成状态写入。"""
        savepoint = control_pg_conn.transaction()
        await savepoint.start()
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await control_pg_conn.execute(
                "INSERT INTO invite_registration_receipts "
                "(id, idempotency_key_hash, request_hash, tenant_slug) "
                "VALUES ($1, $2, $3, $4)",
                uuid.uuid4(),
                "a" * 64,
                "b" * 64,
                "partial-result",
            )
        await savepoint.rollback()

    async def test_baseline_seed_creates_required_entities(self, bypass_session, migrated_pg_url):
        """门禁 AC：基准租户具备首条扫码旅程默认权限与业务数据。"""
        first_summary = await seed_baseline(migrated_pg_url)
        # seed_baseline 提交后，bypass_session 需要新事务才能看到新数据
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        evidence = await verify_baseline_presence(bypass_session)
        assert evidence["status"] == "passed", evidence["failure_reason"]
        assert evidence["db_assertions"]["baseline_tenant"]["slug"] == BASELINE_TENANT_SLUG
        assert evidence["db_assertions"]["control_tenant"]["slug"] == CONTROL_TENANT_SLUG
        assert evidence["db_assertions"]["code_items"]["count"] == 20
        assert evidence["db_assertions"]["live_launch_release"]["count"] == 1
        assert first_summary["launch_release"]["status"] == "live"

    async def test_baseline_seed_idempotent(self, bypass_session, migrated_pg_url):
        """门禁 AC：同一初始化流程连续运行两次不产生重复记录或状态漂移。"""
        first_summary = await seed_baseline(migrated_pg_url)
        second_summary = await seed_baseline(migrated_pg_url)
        seed_tenant_ids = {
            uuid.UUID(first_summary["baseline_tenant"]["id"]),
            uuid.UUID(first_summary["control_tenant"]["id"]),
        }
        assert seed_tenant_ids == {
            uuid.UUID(second_summary["baseline_tenant"]["id"]),
            uuid.UUID(second_summary["control_tenant"]["id"]),
        }
        assert first_summary["launch_release"] == second_summary["launch_release"]
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        evidence = await verify_no_duplicate_on_rerun(bypass_session)
        assert evidence["status"] == "passed", evidence["failure_reason"]
        # 码数仍为 20（无重复生成）
        presence = await verify_baseline_presence(bypass_session)
        assert presence["db_assertions"]["code_items"]["count"] == 20
        seed_export_ids = list(
            (
                await bypass_session.execute(
                    text(
                        "SELECT id FROM export_logs "
                        "WHERE tenant_id IN (:baseline_tenant_id,:control_tenant_id) "
                        "AND authority_version=2 AND scope_snapshot->>'source'='trusted_seed' "
                        "ORDER BY tenant_id"
                    ),
                    {
                        "baseline_tenant_id": first_summary["baseline_tenant"]["id"],
                        "control_tenant_id": first_summary["control_tenant"]["id"],
                    },
                )
            ).scalars()
        )
        assert len(seed_export_ids) == 2
        assert all(export_id.version == 7 for export_id in seed_export_ids)


# ── 门禁：多租户隔离（真实 PG RLS，SQLite 测不到）─────────────────────────


class TestTenantIsolation:
    """§3 / §11 多租户隔离共同门禁。"""

    async def test_control_tenant_cannot_read_baseline_data(self, bypass_session, migrated_pg_url, asyncpg_conn):
        """对照租户无法读取基准租户数据（真实 RLS）。"""
        await seed_baseline(migrated_pg_url)
        await bypass_session.rollback()

        # 先用 bypass 会话拿到两个租户的 id
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        iso = await verify_isolation_rls(bypass_session)
        assert iso["status"] == "passed", iso["failure_reason"]
        base_id = iso["db_assertions"]["base_tenant_id"]
        ctrl_id = iso["db_assertions"]["control_tenant_id"]
        assert base_id and ctrl_id and base_id != ctrl_id

        # 在对照租户上下文里查基准租户的 brands —— 应为 0
        # asyncpg 不支持 SET LOCAL 参数化，严格校验 UUID 后用 f-string（与 app.core.database 一致）
        uuid.UUID(ctrl_id)  # 严格校验，防注入
        await asyncpg_conn.execute(f"SET LOCAL app.tenant_id = '{ctrl_id}'")
        rows = await asyncpg_conn.fetch("SELECT count(*)::int AS c FROM brands WHERE tenant_id = $1", base_id)
        assert rows[0]["c"] == 0, "control tenant should NOT see baseline brands (RLS leak)"

        # 同名品牌碰撞：对照租户只能看到自己的 BRAND-BASE
        rows = await asyncpg_conn.fetch("SELECT tenant_id::text AS t FROM brands WHERE name = 'BRAND-BASE'")
        visible_tenants = {r["t"] for r in rows}
        assert visible_tenants == {ctrl_id}, f"control tenant should only see its own BRAND-BASE, got {visible_tenants}"

        # 反向：基准租户上下文看不到对照租户的码
        uuid.UUID(base_id)
        await asyncpg_conn.execute(f"SET LOCAL app.tenant_id = '{base_id}'")
        rows = await asyncpg_conn.fetch("SELECT count(*)::int AS c FROM code_items WHERE tenant_id = $1", ctrl_id)
        assert rows[0]["c"] == 0, "baseline tenant should NOT see control tenant code_items"

    async def test_no_context_sees_nothing(self, asyncpg_conn, migrated_pg_url):
        """§11：不设置 tenant_id 且不 bypass 的会话不应看到任何业务行（迁移 0011 加固）。"""
        await seed_baseline(migrated_pg_url)
        # 既不设 app.tenant_id 也不设 app.bypass_rls
        rows = await asyncpg_conn.fetch("SELECT count(*)::int AS c FROM brands")
        assert rows[0]["c"] == 0, "session without tenant_id+bypass should see zero rows"

    async def test_ops_tasks_rls_crud_matrix(self, bypass_session, migrated_pg_url, asyncpg_conn, control_pg_conn):
        """ops_tasks 只允许行所属租户读写；第三方与无上下文均 fail closed。"""
        await seed_baseline(migrated_pg_url)
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        iso = await verify_isolation_rls(bypass_session)
        owner_id = uuid.UUID(iso["db_assertions"]["base_tenant_id"])
        counterparty_id = uuid.UUID(iso["db_assertions"]["control_tenant_id"])
        third_id = uuid.uuid4()
        owner_task_id = uuid.uuid4()
        bypass_task_id = uuid.uuid4()
        await bypass_session.execute(
            text(
                "INSERT INTO tenants (id, name, slug, status, plan, tenant_type, created_at, updated_at) "
                "VALUES (:id, 'Ops Third Tenant', :slug, 'active', 'free', 'brand', now(), now())"
            ),
            {"id": third_id, "slug": f"ops-third-{third_id.hex[:8]}"},
        )
        await bypass_session.execute(
            text(
                "INSERT INTO ops_tasks (id, tenant_id, title, status, priority, created_at, updated_at) "
                "VALUES (:id, :tenant_id, 'owner task', 'pending', 'medium', now(), now())"
            ),
            {"id": owner_task_id, "tenant_id": owner_id},
        )
        await bypass_session.commit()

        await _set_rls_context(asyncpg_conn, tenant_id=owner_id)
        assert await asyncpg_conn.fetchval("SELECT count(*) FROM ops_tasks WHERE id = $1", owner_task_id) == 1
        own_insert_id = uuid.uuid4()
        await asyncpg_conn.execute(
            "INSERT INTO ops_tasks (id, tenant_id, title, status, priority, created_at, updated_at) "
            "VALUES ($1, $2, 'own task', 'pending', 'medium', now(), now())",
            own_insert_id,
            owner_id,
        )
        assert (
            await asyncpg_conn.execute(
                "UPDATE ops_tasks SET status = 'in_progress', updated_at = now() WHERE id = $1", own_insert_id
            )
            == "UPDATE 1"
        )
        assert await asyncpg_conn.execute("DELETE FROM ops_tasks WHERE id = $1", own_insert_id) == "DELETE 1"

        foreign_insert_sql = (
            "INSERT INTO ops_tasks (id, tenant_id, title, status, priority, created_at, updated_at) "
            "VALUES ($1, $2, 'foreign task', 'pending', 'medium', now(), now())"
        )
        for foreign_tenant_id in (counterparty_id, third_id):
            await _set_rls_context(asyncpg_conn, tenant_id=foreign_tenant_id)
            assert await asyncpg_conn.fetchval("SELECT count(*) FROM ops_tasks WHERE id = $1", owner_task_id) == 0
            await _assert_write_denied(asyncpg_conn, foreign_insert_sql, uuid.uuid4(), owner_id)
            assert (
                await asyncpg_conn.execute(
                    "UPDATE ops_tasks SET status = 'completed', updated_at = now() WHERE id = $1", owner_task_id
                )
                == "UPDATE 0"
            )
            assert await asyncpg_conn.execute("DELETE FROM ops_tasks WHERE id = $1", owner_task_id) == "DELETE 0"

        await _set_rls_context(asyncpg_conn)
        assert await asyncpg_conn.fetchval("SELECT count(*) FROM ops_tasks WHERE id = $1", owner_task_id) == 0
        await _assert_write_denied(asyncpg_conn, foreign_insert_sql, uuid.uuid4(), owner_id)
        assert (
            await asyncpg_conn.execute(
                "UPDATE ops_tasks SET status = 'completed', updated_at = now() WHERE id = $1", owner_task_id
            )
            == "UPDATE 0"
        )
        assert await asyncpg_conn.execute("DELETE FROM ops_tasks WHERE id = $1", owner_task_id) == "DELETE 0"

        assert await control_pg_conn.fetchval("SELECT count(*) FROM ops_tasks WHERE id = $1", owner_task_id) == 1
        await control_pg_conn.execute(foreign_insert_sql, bypass_task_id, owner_id)
        assert (
            await control_pg_conn.execute(
                "UPDATE ops_tasks SET status = 'completed', updated_at = now() WHERE id = $1", bypass_task_id
            )
            == "UPDATE 1"
        )
        assert await control_pg_conn.execute("DELETE FROM ops_tasks WHERE id = $1", bypass_task_id) == "DELETE 1"

    async def test_database_rejects_cross_tenant_organization_parent(
        self, bypass_session, migrated_pg_url, asyncpg_conn
    ):
        await seed_baseline(migrated_pg_url)
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        iso = await verify_isolation_rls(bypass_session)
        base_id = uuid.UUID(iso["db_assertions"]["base_tenant_id"])
        ctrl_id = uuid.UUID(iso["db_assertions"]["control_tenant_id"])
        parent_id = uuid.uuid4()
        await bypass_session.execute(
            text(
                "INSERT INTO organizations (id, tenant_id, name, created_at, updated_at) "
                "VALUES (:id, :tenant_id, 'Base Parent', now(), now())"
            ),
            {"id": parent_id, "tenant_id": base_id},
        )
        await bypass_session.commit()

        await asyncpg_conn.execute(f"SET LOCAL app.tenant_id = '{ctrl_id}'")
        savepoint = asyncpg_conn.transaction()
        await savepoint.start()
        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
            await asyncpg_conn.execute(
                "INSERT INTO organizations (id, tenant_id, name, parent_id, created_at, updated_at) "
                "VALUES ($1, $2, 'Cross Tenant Child', $3, now(), now())",
                uuid.uuid4(),
                ctrl_id,
                parent_id,
            )
        await savepoint.rollback()

    async def test_agency_authorization_rls_crud_matrix(self, bypass_session, migrated_pg_url, runtime_pg_conn):
        """授权双方可读，但 runtime 只能通过窄函数追加或转换授权状态。"""
        await seed_baseline(migrated_pg_url)
        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        iso = await verify_isolation_rls(bypass_session)
        client_id = uuid.UUID(iso["db_assertions"]["base_tenant_id"])
        third_id = uuid.UUID(iso["db_assertions"]["control_tenant_id"])
        agency_id = uuid.uuid4()
        authorization_id = uuid.uuid4()
        await bypass_session.execute(
            text(
                "INSERT INTO tenants (id, name, slug, status, plan, tenant_type, created_at, updated_at) "
                "VALUES (:id, 'Acceptance Agency', :slug, 'active', 'free', 'agency', now(), now())"
            ),
            {"id": agency_id, "slug": f"acceptance-agency-{agency_id.hex[:8]}"},
        )
        await bypass_session.execute(
            text(
                "INSERT INTO agency_authorizations "
                "(id, agency_tenant_id, client_tenant_id, scope, status, granted_at, created_at, updated_at) "
                "VALUES (:id, :agency_id, :client_id, CAST(:scope AS json), 'active', now(), now(), now())"
            ),
            {"id": authorization_id, "agency_id": agency_id, "client_id": client_id, "scope": '["pages"]'},
        )
        await bypass_session.commit()

        insert_sql = (
            "INSERT INTO agency_authorizations "
            "(id, agency_tenant_id, client_tenant_id, scope, status, granted_at, created_at, updated_at) "
            "VALUES ($1, $2, $3, '[\"pages\"]'::json, 'revoked', now(), now(), now())"
        )

        await _set_rls_context(runtime_pg_conn, tenant_id=client_id)
        assert (
            await runtime_pg_conn.fetchval("SELECT count(*) FROM agency_authorizations WHERE id = $1", authorization_id)
            == 1
        )
        await _assert_write_denied(runtime_pg_conn, insert_sql, uuid.uuid4(), agency_id, client_id)
        await _assert_write_denied(
            runtime_pg_conn,
            "UPDATE agency_authorizations SET scope = '[\"analytics\"]'::json, updated_at = now() WHERE id = $1",
            authorization_id,
        )
        await _assert_write_denied(
            runtime_pg_conn,
            "DELETE FROM agency_authorizations WHERE id = $1",
            authorization_id,
        )

        await _set_rls_context(runtime_pg_conn, tenant_id=agency_id)
        assert (
            await runtime_pg_conn.fetchval("SELECT count(*) FROM agency_authorizations WHERE id = $1", authorization_id)
            == 1
        )
        await _assert_write_denied(runtime_pg_conn, insert_sql, uuid.uuid4(), agency_id, client_id)
        await _assert_write_denied(
            runtime_pg_conn,
            "UPDATE agency_authorizations SET scope = '[\"analytics\"]'::json, updated_at = now() WHERE id = $1",
            authorization_id,
        )
        await _assert_write_denied(
            runtime_pg_conn,
            "DELETE FROM agency_authorizations WHERE id = $1",
            authorization_id,
        )

        for hidden_tenant_id in (third_id, None):
            await _set_rls_context(runtime_pg_conn, tenant_id=hidden_tenant_id)
            assert (
                await runtime_pg_conn.fetchval(
                    "SELECT count(*) FROM agency_authorizations WHERE id = $1", authorization_id
                )
                == 0
            )
            await _assert_write_denied(runtime_pg_conn, insert_sql, uuid.uuid4(), agency_id, client_id)
            await _assert_write_denied(
                runtime_pg_conn,
                "UPDATE agency_authorizations SET scope = '[\"analytics\"]'::json, updated_at = now() WHERE id = $1",
                authorization_id,
            )
            await _assert_write_denied(
                runtime_pg_conn,
                "DELETE FROM agency_authorizations WHERE id = $1",
                authorization_id,
            )

        await _set_rls_context(runtime_pg_conn, bypass=True)
        # A NOBYPASSRLS runtime principal cannot promote itself by setting the
        # custom GUC string.  Only the independently configured control role,
        # which owns the parameter SET privilege, may enter this branch.
        assert (
            await runtime_pg_conn.fetchval("SELECT count(*) FROM agency_authorizations WHERE id = $1", authorization_id)
            == 0
        )
        await _assert_write_denied(runtime_pg_conn, insert_sql, uuid.uuid4(), agency_id, client_id)
        await _assert_write_denied(
            runtime_pg_conn,
            "UPDATE agency_authorizations SET scope = '[\"analytics\"]'::json, updated_at = now() WHERE id = $1",
            authorization_id,
        )
        await _assert_write_denied(
            runtime_pg_conn,
            "DELETE FROM agency_authorizations WHERE id = $1",
            authorization_id,
        )


# ── 门禁：默认权限经真实 API 可用 ──────────────────────────────────────────


class TestDefaultPermissionsViaApi:
    """§5 角色与权限：品牌管理员经 API 具备生成码所需默认权限。"""

    @pytest.fixture
    async def client(self, migrated_pg_url: str) -> AsyncGenerator[AsyncClient, None]:
        """针对 testcontainer PG 的 ASGI client。"""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(migrated_pg_url)

        async def override_get_db():
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                # 默认不带 tenant_id/bypass：由中间件经 context var 注入
                yield session
                await session.commit()

        async def override_get_bypass():
            from sqlalchemy import text as _text

            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                await session.execute(_text("SET LOCAL app.bypass_rls = 'true'"))
                yield session
                await session.commit()

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_db_with_bypass] = override_get_bypass
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        app.dependency_overrides.clear()
        await engine.dispose()

    async def _login_baseline_admin(self, client: AsyncClient) -> str:
        """用基准 admin 登录拿 token（走真实 /auth/login + 中间件）。"""
        r = await client.post(
            "/api/v1/auth/login",
            json={
                "email": BASELINE_ADMIN_EMAIL,
                "password": BASELINE_ADMIN_PASSWORD,
                "tenant_slug": BASELINE_TENANT_SLUG,
            },
        )
        assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
        return r.json()["access_token"]

    async def test_baseline_admin_can_list_own_products(self, client, migrated_pg_url):
        """品牌管理员经 API 能看到自己的产品（默认权限 + 租户作用域）。"""
        await seed_baseline(migrated_pg_url)
        token = await self._login_baseline_admin(client)
        r = await client.get("/api/v1/products", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        items = r.json().get("items") if isinstance(r.json(), dict) else r.json()
        names = {it.get("name") for it in items}
        assert "PRODUCT-BASE" in names, f"baseline admin should see PRODUCT-BASE, got {names}"


# ── 门禁：首扫原子性 + 重复扫码记录现状（证据）──────────────────────────────


class TestScanRecordingEvidence:
    """§9 溯源门禁相关：首扫原子性 + 重复扫码现状证据。"""

    async def test_first_scan_atomicity(self, bypass_session, migrated_pg_url, asyncpg_conn):
        """同一码并发 8 次写入，DB 中 is_first_scan=true 恰好 1 条。

        直接调用 record_scan_event 模拟并发解析，验证
        backend/app/services/scan_event.py:26-33 的原子 UPDATE 不变式。
        """
        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        assert public_id, "baseline must produce a scannable public_id"
        await bypass_session.rollback()

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.services.scan_event import record_scan_event

        engine = create_async_engine(migrated_pg_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        # 并发 8 次记录扫码（不同会话模拟并发请求）。权威函数即使面对 owner 连接也要求
        # 显式租户上下文，与生产 resolver 的 fail-closed 边界一致。
        async with factory() as db:
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            before = int(
                (
                    await db.execute(
                        text("SELECT count(*) FROM scan_events WHERE public_id = :pid"),
                        {"pid": public_id},
                    )
                ).scalar()
                or 0
            )

        async def _one_scan():
            async with factory() as db:
                await db.execute(
                    text("SELECT set_config('app.tenant_id',:tenant_id,true)"),
                    {"tenant_id": tenant_id},
                )
                await record_scan_event(
                    db,
                    uuid.UUID(tenant_id),
                    public_id=public_id,
                    ip_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                )
                await db.commit()

        await asyncio.gather(*[_one_scan() for _ in range(8)])
        await engine.dispose()

        # 用独立会话断言。total 断言用增量而非绝对值，避免被同会话其他 resolve 测试污染。
        async with factory() as db:
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            evidence = await verify_first_scan_atomicity(db, public_id)
            after_total = evidence["db_assertions"]["total_scan_events"]
        evidence["db_assertions"]["total_scan_events_delta"] = after_total - before
        assert evidence["status"] == "passed", evidence["failure_reason"]
        assert evidence["db_assertions"]["first_scan_events"] == 1  # 全局唯一首扫
        assert evidence["db_assertions"]["total_scan_events_delta"] == 8  # 本测试新增 8 条

    async def test_duplicate_scan_recording_exposed(self, bypass_session, migrated_pg_url):
        """证据：当前 resolver 对激活码每次请求都新增 scan_events（无去重）。

        本票只暴露现状，不修复（修复留给后续票）。
        """
        summary = await seed_baseline(migrated_pg_url)
        public_id = summary["first_public_id"]
        tenant_id = summary["baseline_tenant"]["id"]
        await bypass_session.rollback()

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.services.scan_event import record_scan_event

        engine = create_async_engine(migrated_pg_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        # 连续 2 次（同 IP 同 UA 也应记录两次 —— 这是当前行为）
        async with factory() as db:
            await db.execute(
                text("SELECT set_config('app.tenant_id',:tenant_id,true)"),
                {"tenant_id": tenant_id},
            )
            await record_scan_event(
                db,
                uuid.UUID(tenant_id),
                public_id=public_id,
                ip_hash=hashlib.sha256(b"same-ip").hexdigest(),
                user_agent="same-ua",
            )
            await db.commit()
        async with factory() as db:
            await db.execute(
                text("SELECT set_config('app.tenant_id',:tenant_id,true)"),
                {"tenant_id": tenant_id},
            )
            await record_scan_event(
                db,
                uuid.UUID(tenant_id),
                public_id=public_id,
                ip_hash=hashlib.sha256(b"same-ip").hexdigest(),
                user_agent="same-ua",
            )
            await db.commit()

        async with factory() as db:
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            evidence = await verify_duplicate_scan_recording(db, public_id, expected_min_total=2)
        await engine.dispose()
        assert evidence["status"] == "passed", evidence["failure_reason"]
        assert evidence["db_assertions"]["total_scan_events"] >= 2
