"""Real PostgreSQL proof for protected OpenID rollout and batch recall authority."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import date, timedelta

import asyncpg
import pytest

from app.services.connectors.secrets import decrypt_secrets, encrypt_secrets
from app.utils.crypto import decrypt_wechat_openid, hash_wechat_openid
from tests.test_acceptance.test_code_batch_delivery_contract import _seed_catalog
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic, _assert_sqlstate

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

_PARENT = "u6a3d4e5f6a7"


async def test_openid_and_connector_secret_roundtrip(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", _PARENT)
    owner = await asyncpg.connect(owner_dsn)
    ids = await _seed_catalog(owner, "protected-identity")
    consumer_id = uuid.uuid4()
    connector_id = uuid.uuid4()
    openid = f"openid-{uuid.uuid4().hex}"
    original_secrets = {"preexisting": "kept"}
    moved = {
        "oa_appsecret": "oa-secret",
        "cert_private_key": "private-key",
        "api_v3_key": "v3-secret",
        "api_key": "api-key",
        "api_secret": "api-secret",
        "callback_secret": "callback-secret",
        "mch_key": "merchant-secret",
        "secret": "wecom-secret",
    }
    public_config = {"oa_appid": "public-app", "cert_public_cert": "public-certificate"}
    try:
        await owner.execute(
            "INSERT INTO consumer_profiles(id,tenant_id,wechat_openid,member_level,total_points,created_at,updated_at) "
            "VALUES($1,$2,$3,'normal',0,now(),now())",
            consumer_id,
            ids["tenant"],
            openid,
        )
        await owner.execute(
            "INSERT INTO connectors(id,tenant_id,name,connector_type,config,secrets_encrypted,enabled,created_at,updated_at) "
            "VALUES($1,$2,'legacy connector','generic_http',$3::jsonb,$4,true,now(),now())",
            connector_id,
            ids["tenant"],
            json.dumps({**public_config, **moved}),
            encrypt_secrets(original_secrets),
        )
    finally:
        await owner.close()

    await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name='consumer_profiles' AND column_name='wechat_openid')"
        )
        protected = await owner.fetchrow(
            "SELECT wechat_openid_hash,wechat_openid_ciphertext,wechat_openid_nonce,wechat_openid_key_id "
            "FROM consumer_profiles WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            consumer_id,
        )
        assert protected["wechat_openid_hash"] == hash_wechat_openid(ids["tenant"], openid)
        assert (
            decrypt_wechat_openid(
                ids["tenant"],
                consumer_id,
                protected["wechat_openid_ciphertext"],
                protected["wechat_openid_nonce"],
                protected["wechat_openid_key_id"],
            )
            == openid
        )
        connector = await owner.fetchrow(
            "SELECT config,secrets_encrypted FROM connectors WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            connector_id,
        )
        assert json.loads(connector["config"]) == public_config
        assert decrypt_secrets(connector["secrets_encrypted"]) == {**original_secrets, **moved}
        assert await owner.fetchval(
            "SELECT idx.indisvalid AND idx.indisunique FROM pg_index AS idx "
            "WHERE idx.indexrelid='public.uq_consumer_profiles_tenant_wechat_openid_hash'::regclass"
        )
        assert await owner.fetchval(
            "SELECT convalidated FROM pg_constraint WHERE conname='ck_connectors_config_has_no_plaintext_secrets'"
        )
    finally:
        await owner.close()

    await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", _PARENT)
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval(
            "SELECT wechat_openid=$3 FROM consumer_profiles WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            consumer_id,
            openid,
        )
        connector = await owner.fetchrow(
            "SELECT config,secrets_encrypted FROM connectors WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            connector_id,
        )
        assert json.loads(connector["config"]) == {**public_config, **moved}
        assert decrypt_secrets(connector["secrets_encrypted"]) == original_secrets
    finally:
        await owner.close()
    await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")


async def test_openid_backfill_missing_keys_fails_closed_and_retries(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", _PARENT)
    owner = await asyncpg.connect(owner_dsn)
    ids = await _seed_catalog(owner, "openid-key-retry")
    consumer_id = uuid.uuid4()
    openid = f"never-log-{uuid.uuid4().hex}"
    try:
        await owner.execute(
            "INSERT INTO consumer_profiles(id,tenant_id,wechat_openid,member_level,total_points,created_at,updated_at) "
            "VALUES($1,$2,$3,'normal',0,now(),now())",
            consumer_id,
            ids["tenant"],
            openid,
        )
    finally:
        await owner.close()

    await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "u6b1d2e3f4a5")
    master_key = os.environ["AES_MASTER_KEY_V1"]
    pepper = os.environ["HMAC_PEPPER"]
    monkeypatch.delenv("AES_MASTER_KEY_V1")
    monkeypatch.delenv("HMAC_PEPPER")
    failed = await asyncio.to_thread(
        _alembic,
        migrated_pg_url,
        "upgrade",
        "head",
        succeeds=False,
    )
    output = f"{failed.stdout}\n{failed.stderr}"
    assert "No AES_MASTER_KEY_Vn configured" in output
    assert openid not in output
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u6b1d2e3f4a5"
        assert await owner.fetchval(
            "SELECT wechat_openid=$3 AND wechat_openid_hash IS NULL FROM consumer_profiles "
            "WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            consumer_id,
            openid,
        )
    finally:
        await owner.close()
    monkeypatch.setenv("AES_MASTER_KEY_V1", master_key)
    monkeypatch.setenv("HMAC_PEPPER", pepper)
    await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")


async def test_recall_function_is_permission_bound_and_runtime_tables_are_read_only(
    migrated_pg_url: str,
) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    ids = await _seed_catalog(owner, "recall-authority")
    session_id = uuid.uuid4()
    permission_ids = {
        code: uuid.uuid4() for code in ("code:manage", "product:create", "product:update", "product:delete")
    }
    audit_id = uuid.uuid4()
    try:
        await owner.execute(
            "INSERT INTO auth_sessions(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
            session_id,
            ids["account"],
            ids["tenant"],
            uuid.uuid4().hex,
        )
        await _assert_sqlstate(
            _runtime_recall(runtime, ids, session_id, audit_id, "permission missing"),
            "42501",
        )
        for code, permission_id in permission_ids.items():
            await owner.execute(
                "INSERT INTO permissions(id,tenant_id,code,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
                permission_id,
                ids["tenant"],
                code,
            )
            await owner.execute(
                "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
                ids["tenant"],
                ids["admin_role"],
                permission_id,
            )
        managed_batch_id = uuid.uuid4()
        create_audit_id = uuid.uuid4()
        update_audit_id = uuid.uuid4()
        delete_audit_id = uuid.uuid4()
        await owner.execute(
            "DELETE FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3",
            ids["tenant"],
            ids["admin_role"],
            permission_ids["product:create"],
        )
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            await _assert_sqlstate(
                runtime.fetchrow(
                    "SELECT * FROM create_production_batch($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                    ids["tenant"],
                    session_id,
                    create_audit_id,
                    managed_batch_id,
                    ids["product"],
                    ids["sku"],
                    f"MANAGED-{managed_batch_id.hex[:8]}",
                    date.today(),
                    date.today() + timedelta(days=30),
                    "origin-a",
                ),
                "42501",
            )
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM production_batches WHERE id=$1)", managed_batch_id)
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            ids["tenant"],
            ids["admin_role"],
            permission_ids["product:create"],
        )
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            created = await runtime.fetchrow(
                "SELECT * FROM create_production_batch($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                ids["tenant"],
                session_id,
                create_audit_id,
                managed_batch_id,
                ids["product"],
                ids["sku"],
                f"MANAGED-{managed_batch_id.hex[:8]}",
                date.today(),
                date.today() + timedelta(days=30),
                "origin-a",
            )
        assert created["actor_id"] == ids["account"]
        await owner.execute(
            "DELETE FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3",
            ids["tenant"],
            ids["admin_role"],
            permission_ids["product:update"],
        )
        await _assert_sqlstate(_runtime_update_batch(runtime, ids["tenant"], session_id, managed_batch_id), "42501")
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            ids["tenant"],
            ids["admin_role"],
            permission_ids["product:update"],
        )
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            updated = await runtime.fetchrow(
                "SELECT * FROM update_production_batch($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                ids["tenant"],
                session_id,
                update_audit_id,
                managed_batch_id,
                f"UPDATED-{managed_batch_id.hex[:8]}",
                None,
                None,
                None,
                True,
            )
        assert updated["actor_id"] == ids["account"]
        await owner.execute(
            "DELETE FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3",
            ids["tenant"],
            ids["admin_role"],
            permission_ids["product:delete"],
        )
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            await _assert_sqlstate(
                runtime.fetchrow(
                    "SELECT * FROM delete_production_batch($1,$2,$3,$4)",
                    ids["tenant"],
                    session_id,
                    delete_audit_id,
                    managed_batch_id,
                ),
                "42501",
            )
        assert await owner.fetchval("SELECT EXISTS(SELECT 1 FROM production_batches WHERE id=$1)", managed_batch_id)
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            ids["tenant"],
            ids["admin_role"],
            permission_ids["product:delete"],
        )
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            deleted = await runtime.fetchrow(
                "SELECT * FROM delete_production_batch($1,$2,$3,$4)",
                ids["tenant"],
                session_id,
                delete_audit_id,
                managed_batch_id,
            )
        assert deleted["actor_id"] == ids["account"]
        audits = await owner.fetch(
            "SELECT action,operator_id,target_tenant_id FROM platform_audit_log "
            "WHERE id=ANY($1::uuid[]) ORDER BY action",
            [create_audit_id, update_audit_id, delete_audit_id],
        )
        assert {row["action"] for row in audits} == {
            "production_batch_created",
            "production_batch_updated",
            "production_batch_deleted",
        }
        assert all(row["operator_id"] == str(ids["account"]) for row in audits)
        assert all(row["target_tenant_id"] == str(ids["tenant"]) for row in audits)
        import_batch_id = uuid.uuid4()
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            await runtime.execute(
                "INSERT INTO production_batches(id,tenant_id,product_id,sku_id,batch_code,production_date,"
                "expiry_date,origin,status,external_id,source_system,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,'import-a','active',$8,'catalog_import',now(),now())",
                import_batch_id,
                ids["tenant"],
                ids["product"],
                ids["sku"],
                f"IMPORT-{import_batch_id.hex[:8]}",
                date.today(),
                date.today() + timedelta(days=30),
                f"external-{import_batch_id.hex[:8]}",
            )
            assert await runtime.fetchval(
                "SELECT id=$1 FROM production_batches WHERE tenant_id=$2 AND id=$1 FOR UPDATE",
                import_batch_id,
                ids["tenant"],
            )
            await runtime.execute(
                "UPDATE production_batches SET batch_code=$1,origin='import-b',updated_at=now() "
                "WHERE tenant_id=$2 AND id=$3",
                f"IMPORT-UP-{import_batch_id.hex[:8]}",
                ids["tenant"],
                import_batch_id,
            )
            await runtime.execute(
                "DELETE FROM production_batches WHERE tenant_id=$1 AND id=$2",
                ids["tenant"],
                import_batch_id,
            )
        result = await _runtime_recall(runtime, ids, session_id, audit_id, "quality recall")
        assert result["prior_status"] == "active"
        assert result["current_status"] == "recalled"
        assert result["actor_id"] == ids["account"]
        assert await owner.fetchval(
            "SELECT count(*)=1 FROM platform_audit_log WHERE id=$1 AND target_tenant_id=$2",
            audit_id,
            str(ids["tenant"]),
        )
        assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app','production_batches','UPDATE')")
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(ids["tenant"]))
        await _assert_sqlstate(
            runtime.execute(
                "UPDATE production_batches SET status=status WHERE tenant_id=$1 AND id=$2",
                ids["tenant"],
                ids["production_batch"],
            ),
            "42501",
        )
    finally:
        await owner.execute(
            "DELETE FROM platform_audit_log WHERE target_tenant_id=$1",
            str(ids["tenant"]),
        )
        await owner.execute(
            "DELETE FROM production_batches WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            ids["production_batch"],
        )
        await owner.execute("DELETE FROM auth_sessions WHERE id=$1", session_id)
        await owner.execute(
            "DELETE FROM role_permissions WHERE permission_id=ANY($1::uuid[])", list(permission_ids.values())
        )
        await owner.execute("DELETE FROM permissions WHERE id=ANY($1::uuid[])", list(permission_ids.values()))
        await runtime.close()
        await owner.close()


async def test_production_batch_mutations_require_live_acting_products_scope(
    migrated_pg_url: str,
) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    client = await _seed_catalog(owner, "batch-client")
    agency = await _seed_catalog(owner, "batch-agency")
    session_id = uuid.uuid4()
    authorization_id = uuid.uuid4()
    create_permission_id = uuid.uuid4()
    update_permission_id = uuid.uuid4()
    batch_id = uuid.uuid4()
    try:
        await owner.execute("UPDATE tenants SET tenant_type='agency' WHERE id=$1", agency["tenant"])
        await owner.execute(
            "INSERT INTO auth_sessions(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,"
            "created_at,updated_at) VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
            session_id,
            agency["account"],
            agency["tenant"],
            uuid.uuid4().hex,
        )
        await owner.execute(
            "INSERT INTO agency_authorizations(id,agency_tenant_id,client_tenant_id,scope,status,granted_at,"
            "created_at,updated_at) VALUES($1,$2,$3,'[\"products\"]'::json,'active',now(),now(),now())",
            authorization_id,
            agency["tenant"],
            client["tenant"],
        )
        await owner.execute(
            "INSERT INTO permissions(id,tenant_id,code,created_at,updated_at) "
            "VALUES($1,$2,'product:create',now(),now())",
            create_permission_id,
            agency["tenant"],
        )
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            agency["tenant"],
            agency["admin_role"],
            create_permission_id,
        )
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(client["tenant"]))
            created = await runtime.fetchrow(
                "SELECT * FROM create_production_batch($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                client["tenant"],
                session_id,
                uuid.uuid4(),
                batch_id,
                client["product"],
                client["sku"],
                f"ACTING-{batch_id.hex[:8]}",
                date.today(),
                date.today() + timedelta(days=30),
                None,
            )
        assert created["actor_id"] == agency["account"]
        before = await owner.fetchrow(
            "SELECT batch_code,origin FROM production_batches WHERE tenant_id=$1 AND id=$2",
            client["tenant"],
            batch_id,
        )
        await _assert_sqlstate(
            _runtime_update_batch(runtime, client["tenant"], session_id, batch_id),
            "42501",
        )
        assert tuple(
            await owner.fetchrow(
                "SELECT batch_code,origin FROM production_batches WHERE tenant_id=$1 AND id=$2",
                client["tenant"],
                batch_id,
            )
        ) == tuple(before)
        await owner.execute(
            "INSERT INTO permissions(id,tenant_id,code,created_at,updated_at) "
            "VALUES($1,$2,'product:update',now(),now())",
            update_permission_id,
            agency["tenant"],
        )
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            agency["tenant"],
            agency["admin_role"],
            update_permission_id,
        )
        await owner.execute("DELETE FROM agency_authorizations WHERE id=$1", authorization_id)
        await owner.execute(
            "INSERT INTO agency_authorizations(id,agency_tenant_id,client_tenant_id,scope,status,granted_at,"
            "created_at,updated_at) VALUES($1,$2,$3,'[\"codes\"]'::json,'active',now(),now(),now())",
            uuid.uuid4(),
            agency["tenant"],
            client["tenant"],
        )
        await _assert_sqlstate(
            _runtime_update_batch(runtime, client["tenant"], session_id, batch_id),
            "42501",
        )
        after = await owner.fetchrow(
            "SELECT batch_code,origin FROM production_batches WHERE tenant_id=$1 AND id=$2",
            client["tenant"],
            batch_id,
        )
        assert tuple(after) == tuple(before)
        assert await owner.fetchval(
            "SELECT operator_id=$2 AND target_tenant_id=$3 FROM platform_audit_log "
            "WHERE action='production_batch_created' AND resource=$1",
            f"production_batch:{batch_id}",
            str(agency["account"]),
            str(client["tenant"]),
        )
    finally:
        await runtime.close()
        await owner.close()


async def test_admin_production_batch_crud_uses_runtime_authority_http(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import database
    from app.main import app
    from app.utils.security import create_access_token

    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    ids = await _seed_catalog(owner, "batch-http")
    session_id = uuid.uuid4()
    for code in ("product:create", "product:update", "product:delete"):
        permission_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO permissions(id,tenant_id,code,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
            permission_id,
            ids["tenant"],
            code,
        )
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            ids["tenant"],
            ids["admin_role"],
            permission_id,
        )
    await owner.execute(
        "INSERT INTO auth_sessions(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,"
        "created_at,updated_at) VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
        session_id,
        ids["account"],
        ids["tenant"],
        uuid.uuid4().hex,
    )
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    runtime_engine = create_async_engine(runtime_url)
    owner_engine = create_async_engine(migrated_pg_url)
    monkeypatch.setattr(database, "async_session_factory", async_sessionmaker(runtime_engine, expire_on_commit=False))
    monkeypatch.setattr(database, "control_session_factory", async_sessionmaker(owner_engine, expire_on_commit=False))
    monkeypatch.setattr(database, "_is_pg", True)

    async def no_cache_invalidation(product_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr("app.services.resolver_response.invalidate_product_cache", no_cache_invalidation)
    token = create_access_token(
        str(ids["tenant"]),
        str(ids["account"]),
        "admin",
        "brand",
        extra={"sid": str(session_id), "auth_version": 0},
    )
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            created = await client.post(
                "/api/v1/production-batches",
                headers=headers,
                json={
                    "product_id": str(ids["product"]),
                    "sku_id": str(ids["sku"]),
                    "batch_code": f"HTTP-{uuid.uuid4().hex[:8]}",
                    "production_date": date.today().isoformat(),
                    "expiry_date": (date.today() + timedelta(days=30)).isoformat(),
                    "origin": "http-a",
                },
            )
            assert created.status_code == 201, created.text
            batch_id = uuid.UUID(created.json()["id"])
            updated = await client.patch(
                f"/api/v1/production-batches/{batch_id}",
                headers=headers,
                json={"origin": "http-b"},
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["origin"] == "http-b"
            deleted = await client.delete(f"/api/v1/production-batches/{batch_id}", headers=headers)
            assert deleted.status_code == 204, deleted.text
        actions = await owner.fetch(
            "SELECT action,operator_id,target_tenant_id FROM platform_audit_log WHERE resource=$1 ORDER BY action",
            f"production_batch:{batch_id}",
        )
        assert {row["action"] for row in actions} == {
            "production_batch_created",
            "production_batch_updated",
            "production_batch_deleted",
        }
        assert all(row["operator_id"] == str(ids["account"]) for row in actions)
        assert all(row["target_tenant_id"] == str(ids["tenant"]) for row in actions)
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()
        await owner.close()


async def _runtime_update_batch(
    runtime: asyncpg.Connection,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    batch_id: uuid.UUID,
) -> asyncpg.Record:
    async with runtime.transaction():
        await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))
        return await runtime.fetchrow(
            "SELECT * FROM update_production_batch($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            tenant_id,
            session_id,
            uuid.uuid4(),
            batch_id,
            "SHOULD-NOT-WRITE",
            None,
            None,
            None,
            False,
        )


async def _runtime_recall(
    runtime: asyncpg.Connection,
    ids: dict[str, uuid.UUID],
    session_id: uuid.UUID,
    audit_id: uuid.UUID,
    reason: str,
) -> asyncpg.Record:
    async with runtime.transaction():
        await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
        return await runtime.fetchrow(
            "SELECT * FROM recall_production_batch($1,$2,$3,$4,$5)",
            ids["tenant"],
            session_id,
            audit_id,
            ids["production_batch"],
            reason,
        )
