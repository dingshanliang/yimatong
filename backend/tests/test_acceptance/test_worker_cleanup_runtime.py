"""Real PostgreSQL race and batch-boundary gate for delivery cleanup."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import database
from app.tasks.worker import cleanup_old_deliveries

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def test_cleanup_rechecks_terminal_state_and_remains_bounded(migrated_pg_url: str, monkeypatch) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
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

    tenant_id = uuid.uuid4()
    endpoint_id = uuid.uuid4()
    delivered_ids = [uuid.uuid4() for _ in range(101)]
    failed_id = uuid.uuid4()
    pending_id = uuid.uuid4()
    old = datetime.now(UTC) - timedelta(days=120)
    owner = await asyncpg.connect(owner_dsn)
    try:
        await owner.execute(
            "INSERT INTO tenants (id,name,slug,status,plan,tenant_type,created_at,updated_at) "
            "VALUES ($1,'cleanup tenant',$2,'active','free','brand',now(),now())",
            tenant_id,
            f"cleanup-{tenant_id.hex[:8]}",
        )
        await owner.execute(
            "INSERT INTO webhook_endpoints (id,tenant_id,url,events,secret_ciphertext,secret_nonce,secret_key_id,"
            "enabled,batch_mode,batch_size,config_version) "
            "VALUES ($1,$2,'https://example.invalid','[]'::json,$3,$4,'test-key-v1',true,false,100,1)",
            endpoint_id,
            tenant_id,
            b"x" * 32,
            b"n" * 12,
        )
        rows = [
            (delivery_id, tenant_id, endpoint_id, str(delivery_id), "delivered", old) for delivery_id in delivered_ids
        ]
        rows.extend(
            [
                (failed_id, tenant_id, endpoint_id, str(failed_id), "failed", old),
                (pending_id, tenant_id, endpoint_id, str(pending_id), "pending", old),
            ]
        )
        # These rows model deliveries that existed before the U08D finalize
        # boundary. New legacy inserts are rejected after cutover, while
        # migrated legacy rows remain retry/cleanup compatible.
        await owner.execute("ALTER TABLE webhook_deliveries DISABLE TRIGGER USER")
        try:
            await owner.executemany(
                "INSERT INTO webhook_deliveries "
                "(id,tenant_id,endpoint_id,event_id,event_type,payload,status,retry_count,created_at,updated_at) "
                "VALUES ($1,$2,$3,$4,'test','{}'::json,$5,0,$6,$6)",
                rows,
            )
        finally:
            await owner.execute("ALTER TABLE webhook_deliveries ENABLE TRIGGER USER")

        original_bootstrap = database.bootstrap_tenant_keys
        calls = 0

        async def racing_bootstrap(session, statement, parameters=None):
            nonlocal calls
            keys = await original_bootstrap(session, statement, parameters)
            calls += 1
            if calls == 2:
                await owner.execute(
                    "UPDATE webhook_deliveries SET status='retrying',updated_at=now() WHERE id=$1",
                    delivered_ids[0],
                )
            return keys

        monkeypatch.setattr(database, "bootstrap_tenant_keys", racing_bootstrap)
        assert await cleanup_old_deliveries() == 100

        counts = dict(
            await owner.fetch(
                "SELECT status,count(*) AS count FROM webhook_deliveries WHERE tenant_id=$1 GROUP BY status",
                tenant_id,
            )
        )
        assert counts == {"archived": 100, "delivered": 1, "pending": 1, "retrying": 1}
    finally:
        await owner.close()
        await runtime_engine.dispose()
        await control_engine.dispose()
