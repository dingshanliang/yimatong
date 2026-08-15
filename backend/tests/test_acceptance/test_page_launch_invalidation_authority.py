"""Real PostgreSQL API contract for page-bound launch invalidation."""

from __future__ import annotations

import json
import uuid

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from uuid6 import uuid7

from app.core import database
from app.main import app
from app.middleware.rate_limit import rate_limiter
from app.services.redis_cache import AsyncRedisCache
from app.utils.security import create_access_token
from tests.test_acceptance.test_launch_release_authority import (
    _launch_release,
    _seed_ready_graph,
)

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def test_page_api_mutations_invalidate_only_their_authoritative_launch_scope(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    graph = await _seed_ready_graph(owner, "page-launch-invalidation")
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
        str(graph["tenant"]),
        str(graph["account"]),
        "admin",
        "brand",
        extra={"sid": str(graph["session"]), "auth_version": 0},
    )
    headers = {"Authorization": f"Bearer {token}"}
    release_id = uuid7()

    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            draft = await client.post(
                f"/api/v1/page-templates/{graph['template']}/versions",
                json={"config_json": {"modules": [], "marker": "unrelated-draft"}},
                headers=headers,
            )
            assert draft.status_code == 201, draft.text
            draft_id = uuid.UUID(draft.json()["id"])

            await _launch_release(runtime, graph, release_id, "page-api-scope")

            updated = await client.patch(
                f"/api/v1/page-versions/{draft_id}",
                json={"config_json": {"modules": [], "marker": "updated-draft"}},
                headers=headers,
            )
            assert updated.status_code == 200, updated.text
            assert await owner.fetchval("SELECT status FROM launch_releases WHERE id=$1", release_id) == "live"

            published = await client.post(f"/api/v1/page-versions/{draft_id}/publish", headers=headers)
            assert published.status_code == 200, published.text
            assert await owner.fetchval("SELECT status FROM launch_releases WHERE id=$1", release_id) == "live"

            unrelated_archive = await client.post(f"/api/v1/page-versions/{draft_id}/archive", headers=headers)
            assert unrelated_archive.status_code == 200, unrelated_archive.text
            assert await owner.fetchval("SELECT status FROM launch_releases WHERE id=$1", release_id) == "live"

            # Construct an exact pinned legacy published fact after the unrelated
            # version has been archived, so the partial published UQ remains valid.
            await owner.execute(
                "UPDATE page_versions SET status='published',published_at=now(),updated_at=now() WHERE id=$1",
                graph["version"],
            )
            exact_archive = await client.post(
                f"/api/v1/page-versions/{graph['version']}/archive",
                headers=headers,
            )
            assert exact_archive.status_code == 200, exact_archive.text
            assert await owner.fetchval("SELECT status FROM launch_releases WHERE id=$1", release_id) == "invalidated"
            assert (
                await owner.fetchval("SELECT invalidation_reason FROM launch_releases WHERE id=$1", release_id)
                == "page_changed"
            )

            await owner.execute(
                "UPDATE launch_releases SET status='live',invalidated_at=NULL,invalidation_reason=NULL,"
                "failure_reason=NULL WHERE id=$1",
                release_id,
            )
            created = await client.post(
                f"/api/v1/page-templates/{graph['template']}/versions",
                json={"config_json": {"modules": [], "marker": "new-version"}},
                headers=headers,
            )
            assert created.status_code == 201, created.text
            assert await owner.fetchval("SELECT status FROM launch_releases WHERE id=$1", release_id) == "invalidated"
            create_audit = await owner.fetchval(
                "SELECT details FROM platform_audit_log WHERE resource=$1 AND action='page_version_created' "
                "ORDER BY timestamp DESC LIMIT 1",
                f"page_version:{created.json()['id']}",
            )
            if isinstance(create_audit, str):
                create_audit = json.loads(create_audit)
            assert create_audit["launch_invalidation"] == {
                "page_action": "create",
                "release_ids": [str(release_id)],
                "release_count": 1,
            }

            await owner.execute(
                "UPDATE launch_releases SET status='suspended',invalidated_at=NULL,invalidation_reason=NULL,"
                "failure_reason=NULL WHERE id=$1",
                release_id,
            )
            rolled_back = await client.post(
                f"/api/v1/page-templates/{graph['template']}/versions/{graph['version']}/rollback",
                headers=headers,
            )
            assert rolled_back.status_code == 201, rolled_back.text
            assert await owner.fetchval("SELECT status FROM launch_releases WHERE id=$1", release_id) == "invalidated"

        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime.execute("UPDATE launch_releases SET status='live' WHERE id=$1", release_id)
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime.fetchval(
                    "SELECT invalidate_launch_releases_for_page_mutation($1,$2,$3,'create',$4,now())",
                    graph["tenant"],
                    graph["template"],
                    graph["version"],
                    uuid7(),
                )
    finally:
        app.dependency_overrides.clear()
        await runtime.close()
        await owner.close()
        await runtime_engine.dispose()
        await owner_engine.dispose()
