"""PostgreSQL authority gates for immutable tenant webhook delivery snapshots."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from uuid6 import uuid7

from app.core import database
from app.services import webhook_dispatcher, webhook_sender
from app.tasks import worker
from tests.test_acceptance.test_code_batch_delivery_contract import _seed_catalog

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


async def test_runtime_cannot_tamper_or_cross_tenant_update_delivery_snapshot(
    migrated_pg_url: str, monkeypatch
) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    tenant_id, other_tenant_id = uuid7(), uuid7()
    endpoint_id, event_id, delivery_id = uuid7(), uuid7(), uuid7()
    occurred_at = datetime.now(UTC)
    payload = {
        "id": str(event_id),
        "type": "scan.created",
        "timestamp": occurred_at.isoformat(),
        "tenant_id": str(tenant_id),
        "data": {"scan_event_id": str(uuid7())},
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    ciphertext, nonce, key_id = b"x" * 32, b"n" * 12, "test-key-v1"
    try:
        for current, suffix in ((tenant_id, "a"), (other_tenant_id, "b")):
            await owner.execute(
                "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
                "VALUES($1,$2,$3,'active','free','brand',now(),now())",
                current,
                f"Webhook {suffix}",
                f"webhook-u8d-{current.hex[-12:]}",
            )
        await owner.execute(
            "INSERT INTO webhook_endpoints(id,tenant_id,url,events,secret_ciphertext,secret_nonce,secret_key_id,"
            "enabled,batch_mode,batch_size,config_version) VALUES($1,$2,'https://example.invalid/hook',"
            '\'["scan.created","campaign.active"]\'::jsonb,$3,$4,$5,true,false,100,1)',
            endpoint_id,
            tenant_id,
            ciphertext,
            nonce,
            key_id,
        )
        await owner.execute(
            "INSERT INTO webhook_domain_events(id,tenant_id,event_type,payload,payload_digest,occurred_at,expanded_at) "
            "VALUES($1,$2,'scan.created',$3::jsonb,$4,$5,now())",
            event_id,
            tenant_id,
            json.dumps(payload),
            digest,
            occurred_at,
        )
        with pytest.raises(asyncpg.PostgresError) as legacy_exc:
            await owner.execute(
                "INSERT INTO webhook_deliveries(id,tenant_id,endpoint_id,event_id,event_type,payload,status,retry_count) "
                "VALUES($1,$2,$3,$4,'scan.created','{}'::jsonb,'pending',0)",
                uuid7(),
                tenant_id,
                endpoint_id,
                str(uuid7()),
            )
        assert legacy_exc.value.sqlstate == "55000"
        await owner.execute(
            "INSERT INTO webhook_deliveries(id,tenant_id,endpoint_id,event_id,domain_event_id,event_type,payload,"
            "payload_digest,endpoint_url,endpoint_secret_ciphertext,endpoint_secret_nonce,endpoint_secret_key_id,"
            "endpoint_config_version,status,retry_count,attempt_count) VALUES($1,$2,$3,$4,$5,'scan.created',"
            "$6::jsonb,$7,'https://example.invalid/hook',$8,$9,$10,1,'pending',0,0)",
            delivery_id,
            tenant_id,
            endpoint_id,
            str(event_id),
            event_id,
            json.dumps(payload),
            digest,
            ciphertext,
            nonce,
            key_id,
        )
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))
            with pytest.raises(asyncpg.PostgresError) as exc:
                await runtime.execute(
                    "UPDATE webhook_deliveries SET payload='{}'::jsonb WHERE tenant_id=$1 AND id=$2",
                    tenant_id,
                    delivery_id,
                )
            assert exc.value.sqlstate == "55000"
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(other_tenant_id))
            assert (
                await runtime.execute(
                    "UPDATE webhook_deliveries SET status='failed' WHERE tenant_id=$1 AND id=$2",
                    tenant_id,
                    delivery_id,
                )
                == "UPDATE 0"
            )
        runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
        control_url = migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
        runtime_engine = create_async_engine(runtime_url)
        control_engine = create_async_engine(control_url)
        monkeypatch.setattr(
            database,
            "async_session_factory",
            async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False),
        )
        monkeypatch.setattr(
            database,
            "control_session_factory",
            async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False),
        )
        async with database.async_session_factory() as session:
            await database.set_session_tenant_context(session, tenant_id)
            campaign_event = await webhook_dispatcher.record_domain_event(
                session,
                tenant_id,
                "campaign.active",
                {"campaign_id": str(uuid7()), "status": "active"},
            )
            await session.commit()
        await asyncio.gather(
            webhook_dispatcher.expand_committed_events(),
            webhook_dispatcher.expand_committed_events(),
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM webhook_deliveries WHERE tenant_id=$1 AND domain_event_id=$2 AND endpoint_id=$3",
                tenant_id,
                campaign_event.id,
                endpoint_id,
            )
            == 1
        )
        persisted_delivery = await owner.fetchrow(
            "SELECT event_id,payload FROM webhook_deliveries "
            "WHERE tenant_id=$1 AND domain_event_id=$2 AND endpoint_id=$3",
            tenant_id,
            campaign_event.id,
            endpoint_id,
        )
        assert persisted_delivery is not None
        persisted_envelope = persisted_delivery["payload"]
        if isinstance(persisted_envelope, str):
            persisted_envelope = json.loads(persisted_envelope)
        captured_attempts: list[tuple[bytes, dict[str, str]]] = []

        async def resolve_public(_hostname: str) -> list[str]:
            return ["93.184.216.34"]

        async def capture_post(
            _parsed,
            _addresses,
            *,
            raw_body: bytes,
            headers: dict[str, str],
        ) -> tuple[int, str]:
            captured_attempts.append((raw_body, dict(headers)))
            return 204, ""

        monkeypatch.setattr(webhook_sender, "resolve_public_webhook_addresses", resolve_public)
        monkeypatch.setattr(webhook_sender, "_post_pinned", capture_post)
        for _ in range(2):
            assert await webhook_sender.deliver(
                "https://example.invalid/hook", "stable-test-secret", persisted_envelope
            ) == (204, "")
        assert captured_attempts[0] == captured_attempts[1]
        raw_body, headers = captured_attempts[0]
        assert headers["X-Ymt-Delivery"] == persisted_delivery["event_id"]
        assert headers["X-Ymt-Timestamp"] == str(int(campaign_event.occurred_at.timestamp()))
        assert headers["X-Ymt-Signature"] == webhook_sender.compute_signature(
            "stable-test-secret", raw_body, headers["X-Ymt-Timestamp"]
        )
        await owner.execute("ALTER TABLE webhook_deliveries DISABLE TRIGGER USER")
        try:
            await owner.execute(
                "UPDATE webhook_deliveries SET payload_digest=$1 WHERE tenant_id=$2 AND id=$3",
                "0" * 64,
                tenant_id,
                delivery_id,
            )
        finally:
            await owner.execute("ALTER TABLE webhook_deliveries ENABLE TRIGGER USER")

        async def must_not_send(**_kwargs):
            raise AssertionError("malformed delivery reached the network sender")

        monkeypatch.setattr(worker, "deliver", must_not_send)
        await worker.process_single_delivery(str(delivery_id))
        assert await owner.fetchval("SELECT status FROM webhook_deliveries WHERE id=$1", delivery_id) == "failed"
        await owner.execute("ALTER TABLE webhook_deliveries DISABLE TRIGGER USER")
        try:
            await owner.execute(
                "UPDATE webhook_deliveries SET payload_digest=$1,endpoint_secret_key_id='missing-key',"
                "status='pending',lease_token=NULL,lease_expires_at=NULL,last_response_body=NULL WHERE id=$2",
                digest,
                delivery_id,
            )
        finally:
            await owner.execute("ALTER TABLE webhook_deliveries ENABLE TRIGGER USER")
        await worker.process_single_delivery(str(delivery_id))
        credential_failure = await owner.fetchrow(
            "SELECT status,lease_token,lease_expires_at,last_response_body FROM webhook_deliveries WHERE id=$1",
            delivery_id,
        )
        assert credential_failure == ("failed", None, None, "Delivery credential unavailable")
        await runtime_engine.dispose()
        await control_engine.dispose()
    finally:
        await runtime.close()
        await owner.close()


async def test_u8d_populated_legacy_delivery_roundtrip(migrated_pg_url: str, monkeypatch) -> None:
    """A pre-U08D pending row becomes an immutable, repeatable delivery."""
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    try:
        await owner.execute("TRUNCATE webhook_deliveries,webhook_domain_events,webhook_endpoints")
    finally:
        await owner.close()
    env = os.environ.copy()
    env["database_url"] = migrated_pg_url
    env["migration_database_url"] = migrated_pg_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "u8c4d5e6f7a8"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    tenant_id, endpoint_id, delivery_id, legacy_event_id = uuid7(), uuid7(), uuid7(), uuid7()
    legacy_created_at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    owner = await asyncpg.connect(owner_dsn)
    try:
        await owner.execute(
            "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
            "VALUES($1,'Legacy webhook',$2,'active','free','brand',now(),now())",
            tenant_id,
            f"legacy-webhook-{tenant_id.hex[-10:]}",
        )
        await owner.execute(
            "INSERT INTO webhook_endpoints(id,tenant_id,url,events,secret,enabled,batch_mode,batch_size) "
            "VALUES($1,$2,'https://example.invalid/legacy','[\"campaign.active\"]'::json,'legacy-secret',"
            "true,false,100)",
            endpoint_id,
            tenant_id,
        )
        await owner.execute(
            "INSERT INTO webhook_deliveries(id,tenant_id,endpoint_id,event_id,event_type,payload,status,retry_count,"
            "created_at,updated_at) VALUES($1,$2,$3,$4,'campaign.active',$5::json,'pending',0,$6,$6)",
            delivery_id,
            tenant_id,
            endpoint_id,
            str(legacy_event_id),
            json.dumps({"campaign_id": str(uuid7()), "status": "active"}),
            legacy_created_at,
        )
    finally:
        await owner.close()
    for command in (("upgrade", "head"),):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *command],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    owner = await asyncpg.connect(owner_dsn)
    try:
        row = await owner.fetchrow(
            "SELECT payload,payload_digest,endpoint_url,endpoint_secret_ciphertext,legacy_payload_wrapped "
            "FROM webhook_deliveries WHERE id=$1",
            delivery_id,
        )
        assert row is not None and row["legacy_payload_wrapped"] is True
        envelope = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        assert envelope["id"] == str(legacy_event_id)
        assert envelope["type"] == "campaign.active"
        assert envelope["tenant_id"] == str(tenant_id)
        assert envelope["data"]["status"] == "active"
        assert hashlib.sha256(_canonical(envelope)).hexdigest() == row["payload_digest"]
        assert row["endpoint_url"] == "https://example.invalid/legacy"
        assert row["endpoint_secret_ciphertext"] is not None
    finally:
        await owner.close()
    from app.utils.crypto import EnvKeyProvider, encrypt_bytes, init_crypto

    init_crypto(EnvKeyProvider())
    rotated_ciphertext, rotated_nonce, rotated_key_id = encrypt_bytes(
        b"rotated-secret", aad=f"webhook-endpoint:{tenant_id}:{endpoint_id}".encode()
    )
    owner = await asyncpg.connect(owner_dsn)
    try:
        await owner.execute(
            "UPDATE webhook_endpoints SET url='https://example.invalid/rotated',enabled=false,"
            "secret_ciphertext=$1,secret_nonce=$2,secret_key_id=$3,config_version=config_version+1 WHERE id=$4",
            rotated_ciphertext,
            rotated_nonce,
            rotated_key_id,
            endpoint_id,
        )
    finally:
        await owner.close()
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    control_url = migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
    runtime_engine = create_async_engine(runtime_url)
    control_engine = create_async_engine(control_url)
    monkeypatch.setattr(
        database,
        "async_session_factory",
        async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False),
    )
    monkeypatch.setattr(
        database,
        "control_session_factory",
        async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False),
    )
    worker_attempts: list[tuple[str, str, dict]] = []

    async def capture_worker(*, url: str, secret: str, envelope: dict):
        worker_attempts.append((url, secret, envelope))
        return 204, ""

    monkeypatch.setattr(worker, "deliver", capture_worker)
    await worker.process_single_delivery(str(delivery_id))
    owner = await asyncpg.connect(owner_dsn)
    try:
        await owner.execute(
            "UPDATE webhook_deliveries SET status='pending',next_retry_at=NULL WHERE id=$1",
            delivery_id,
        )
    finally:
        await owner.close()
    await worker.process_single_delivery(str(delivery_id))
    assert worker_attempts[0] == worker_attempts[1]
    assert worker_attempts[0][0:2] == ("https://example.invalid/legacy", "legacy-secret")
    await runtime_engine.dispose()
    await control_engine.dispose()
    attempts: list[tuple[bytes, dict[str, str]]] = []

    async def resolve_public(_hostname: str) -> list[str]:
        return ["93.184.216.34"]

    async def capture(_parsed, _addresses, *, raw_body: bytes, headers: dict[str, str]):
        attempts.append((raw_body, dict(headers)))
        return 204, ""

    monkeypatch.setattr(webhook_sender, "resolve_public_webhook_addresses", resolve_public)
    monkeypatch.setattr(webhook_sender, "_post_pinned", capture)
    assert await webhook_sender.deliver("https://example.invalid/legacy", "legacy-secret", envelope) == (204, "")
    assert await webhook_sender.deliver("https://example.invalid/legacy", "legacy-secret", envelope) == (204, "")
    assert attempts[0] == attempts[1]
    for command in (("downgrade", "u8c4d5e6f7a8"), ("upgrade", "head")):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *command],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stdout + result.stderr


async def test_campaign_authority_records_one_atomic_domain_event_before_expansion(
    migrated_pg_url: str, monkeypatch
) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    campaign_id, endpoint_id, benefit_id, session_id = uuid7(), uuid7(), uuid7(), uuid7()
    try:
        ids = await _seed_catalog(owner, f"u8d-campaign-{campaign_id.hex[-8:]}")
        permission_id = await owner.fetchval(
            "SELECT id FROM permissions WHERE tenant_id=$1 AND code='campaign:manage'", ids["tenant"]
        )
        if permission_id is None:
            permission_id = uuid7()
            await owner.execute(
                "INSERT INTO permissions(id,tenant_id,code,created_at,updated_at) "
                "VALUES($1,$2,'campaign:manage',now(),now())",
                permission_id,
                ids["tenant"],
            )
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
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
            uuid7().hex,
        )
        await owner.execute(
            "INSERT INTO campaigns(id,tenant_id,name,campaign_type,status,product_id,start_at,end_at,rules_json,"
            "created_at,updated_at) VALUES($1,$2,'U08D atomic campaign','scan','draft',$3,"
            "now()-interval '1 hour',now()+interval '1 day','{}',now(),now())",
            campaign_id,
            ids["tenant"],
            ids["product"],
        )
        await owner.execute(
            "INSERT INTO benefits(id,tenant_id,campaign_id,name,benefit_type,config_json,stock_total,stock_used,"
            "per_person_limit,status,created_at,updated_at) VALUES($1,$2,$3,'U08D benefit','platform_coupon','{}',"
            "10,0,1,'active',now(),now())",
            benefit_id,
            ids["tenant"],
            campaign_id,
        )
        await owner.execute(
            "INSERT INTO webhook_endpoints(id,tenant_id,url,events,secret_ciphertext,secret_nonce,secret_key_id,"
            "config_version,enabled,batch_mode,batch_size,created_at,updated_at) VALUES($1,$2,"
            "'https://example.invalid/campaign-atomic','[\"campaign.active\"]',$3,$4,'test-key-v1',1,true,false,"
            "100,now(),now())",
            endpoint_id,
            ids["tenant"],
            b"x" * 32,
            b"n" * 12,
        )

        rolled_back_event_id = uuid7()
        tx = runtime.transaction()
        await tx.start()
        await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
        rolled_back = await runtime.fetchrow(
            "SELECT * FROM transition_campaign($1,$2,$3,$4,'active')",
            ids["tenant"],
            session_id,
            rolled_back_event_id,
            campaign_id,
        )
        assert rolled_back is not None and rolled_back["current_status"] == "active"
        await tx.rollback()
        assert await owner.fetchval("SELECT status FROM campaigns WHERE id=$1", campaign_id) == "draft"
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM webhook_domain_events WHERE id=$1)", rolled_back_event_id
        )

        event_id = uuid7()
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            transitioned = await runtime.fetchrow(
                "SELECT * FROM transition_campaign($1,$2,$3,$4,'active')",
                ids["tenant"],
                session_id,
                event_id,
                campaign_id,
            )
            assert transitioned is not None and transitioned["current_status"] == "active"
        event = await owner.fetchrow(
            "SELECT id,event_type,payload,payload_digest,occurred_at FROM webhook_domain_events "
            "WHERE tenant_id=$1 AND payload#>>'{data,campaign_id}'=$2",
            ids["tenant"],
            str(campaign_id),
        )
        assert event is not None and event["id"] == event_id and event["event_type"] == "campaign.active"
        envelope = event["payload"] if isinstance(event["payload"], dict) else json.loads(event["payload"])
        assert envelope["data"] == {"campaign_id": str(campaign_id), "status": "active"}
        assert envelope["id"] == str(event_id) and envelope["tenant_id"] == str(ids["tenant"])
        assert hashlib.sha256(_canonical(envelope)).hexdigest() == event["payload_digest"]
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM webhook_deliveries WHERE tenant_id=$1 AND endpoint_id=$2",
                ids["tenant"],
                endpoint_id,
            )
            == 0
        )

        runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
        # Use the leased database owner for the controlled bootstrap scan. A
        # preceding round-trip test deliberately recreates this table after
        # fixture-level acceptance_control grants were installed.
        control_url = migrated_pg_url
        runtime_engine = create_async_engine(runtime_url)
        control_engine = create_async_engine(control_url)
        monkeypatch.setattr(
            database,
            "async_session_factory",
            async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False),
        )
        monkeypatch.setattr(
            database,
            "control_session_factory",
            async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False),
        )
        assert await webhook_dispatcher.expand_committed_events() == 1
        assert await webhook_dispatcher.expand_committed_events() == 0
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM webhook_deliveries WHERE tenant_id=$1 AND endpoint_id=$2 "
                "AND domain_event_id=$3 AND event_type='campaign.active'",
                ids["tenant"],
                endpoint_id,
                event_id,
            )
            == 1
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM webhook_deliveries WHERE tenant_id=$1 AND endpoint_id=$2 "
            "AND domain_event_id IS NULL)",
            ids["tenant"],
            endpoint_id,
        )
        await runtime_engine.dispose()
        await control_engine.dispose()
    finally:
        await runtime.close()
        await owner.close()
