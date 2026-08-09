"""Real PostgreSQL proofs for tenant opening and scoped bootstrap controls."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import asyncpg
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.platform_opening import PlatformTenantOpening
from app.models.product import Brand
from app.models.tenant import Account, Tenant
from app.modules.brand_tenant_initialization import BrandTenantInitialization, InitializeBrandTenant, PlatformOpening
from app.modules.initial_admin_activation import InitialAdminActivation
from app.services.auth import hash_reset_token

BACKEND_DIR = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.acceptance, pytest.mark.anyio]


def _control_url(database_url: str) -> str:
    return database_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")


def _runtime_url(database_url: str) -> str:
    return database_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")


async def _set_bypass(db: AsyncSession) -> None:
    await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))


def _token_from_url(url: str) -> str:
    return parse_qs(urlsplit(url).query)["token"][0]


class CoordinatedResetCache:
    def __init__(self) -> None:
        self.value: dict | None = None
        self.publish_count = 0
        self.first_publish_entered = asyncio.Event()
        self.release_first_publish = asyncio.Event()
        self._counter_lock = asyncio.Lock()

    async def set_shared(self, key: str, value: dict, ttl: int) -> None:
        assert key.startswith("reset:") and ttl > 0
        async with self._counter_lock:
            self.publish_count += 1
            publish_number = self.publish_count
        if publish_number == 1:
            self.first_publish_entered.set()
            await self.release_first_publish.wait()
        self.value = value

    async def consume_shared(self, key: str, expected: dict) -> bool:
        if self.value != expected:
            return False
        self.value = None
        return True


async def test_concurrent_activation_reissue_preserves_the_last_returned_link(migrated_pg_url: str) -> None:
    engine = create_async_engine(_control_url(migrated_pg_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cache = CoordinatedResetCache()
    unique = uuid.uuid4().hex[:10]

    try:
        async with factory() as db:
            await _set_bypass(db)
            receipt = await BrandTenantInitialization(db).initialize(
                InitializeBrandTenant(
                    name=f"Activation {unique}",
                    admin_name="Initial Admin",
                    admin_email=f"activation-{unique}@example.com",
                    opening=PlatformOpening(operator_id="platform-admin"),
                )
            )
            db.add(
                PlatformTenantOpening(
                    idempotency_key=f"activation-{unique}",
                    request_hash="a" * 64,
                    tenant_id=receipt.tenant_id,
                    initial_admin_id=receipt.initial_admin_id,
                    initial_admin_state="pending_activation",
                )
            )
            await db.commit()

        async def issue() -> str:
            async with factory() as db:
                await _set_bypass(db)
                ticket = await InitialAdminActivation(db, cache).issue_or_reissue(
                    tenant_id=receipt.tenant_id,
                    operator_id="platform-admin",
                    initial_admin_id=receipt.initial_admin_id,
                )
                return _token_from_url(ticket.url)

        first_task = asyncio.create_task(issue())
        await asyncio.wait_for(cache.first_publish_entered.wait(), timeout=5)
        second_task = asyncio.create_task(issue())
        await asyncio.sleep(0.15)
        assert cache.publish_count == 1
        assert not second_task.done()

        cache.release_first_publish.set()
        first_token, second_token = await asyncio.gather(first_task, second_task)
        assert cache.value is not None
        assert cache.value["token_hash"] == hash_reset_token(second_token)
        assert cache.value["token_hash"] != hash_reset_token(first_token)
    finally:
        await engine.dispose()


async def test_concurrent_same_name_openings_allocate_distinct_slugs(
    migrated_pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(_control_url(migrated_pg_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    unique = uuid.uuid4().hex[:10]
    name = f"Concurrent Brand {unique}"
    base_slug = name.lower().replace(" ", "-")
    first_check_entered = asyncio.Event()
    release_first_check = asyncio.Event()
    original_exists = BrandTenantInitialization._tenant_key_exists
    first_check = True

    async def hold_first_slug_check(self: BrandTenantInitialization, tenant_key: str) -> bool:
        nonlocal first_check
        if tenant_key == base_slug and first_check:
            first_check = False
            first_check_entered.set()
            await release_first_check.wait()
        return await original_exists(self, tenant_key)

    monkeypatch.setattr(BrandTenantInitialization, "_tenant_key_exists", hold_first_slug_check)

    async def initialize(suffix: str) -> str:
        async with factory() as db:
            await _set_bypass(db)
            receipt = await BrandTenantInitialization(db).initialize(
                InitializeBrandTenant(
                    name=name,
                    admin_name=f"Admin {suffix}",
                    admin_email=f"slug-{unique}-{suffix}@example.com",
                    opening=PlatformOpening(operator_id="platform-admin"),
                )
            )
            await db.commit()
            return receipt.tenant_key

    try:
        first_task = asyncio.create_task(initialize("one"))
        await asyncio.wait_for(first_check_entered.wait(), timeout=5)
        second_task = asyncio.create_task(initialize("two"))
        await asyncio.sleep(0.15)
        assert not second_task.done()
        release_first_check.set()
        first_slug, second_slug = await asyncio.gather(first_task, second_task)
        assert first_slug == base_slug
        assert second_slug.startswith(f"{base_slug}-")
        assert first_slug != second_slug
    finally:
        await engine.dispose()


async def test_seed_cli_uses_control_only_for_bootstrap_then_tenant_scoped_runtime(migrated_pg_url: str) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    unique = uuid.uuid4().hex[:10]
    target_slug = f"seed-{unique}"
    other_id = uuid.uuid4()

    try:
        async with owner_factory() as db:
            await _set_bypass(db)
            db.add(Tenant(id=other_id, name="Untouched tenant", slug=f"untouched-{unique}"))
            await db.commit()

        env = os.environ.copy()
        env.update(
            {
                "environment": "development",
                "database_url": _runtime_url(migrated_pg_url),
                "control_database_url": _control_url(migrated_pg_url),
                "migration_database_url": migrated_pg_url,
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.cli",
                "all",
                "--name",
                f"Seed Tenant {unique}",
                "--slug",
                target_slug,
                "--admin-email",
                f"seed-{unique}@example.com",
            ],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, f"seed failed:\n{result.stdout}\n{result.stderr}"

        clean_demo = subprocess.run(
            [sys.executable, "-m", "app.cli", "demo", "--clean"],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert clean_demo.returncode == 0, f"clean demo failed:\n{clean_demo.stdout}\n{clean_demo.stderr}"

        async with owner_factory() as db:
            await _set_bypass(db)
            target = (await db.execute(select(Tenant).where(Tenant.slug == target_slug))).scalar_one()
            assert await db.scalar(select(func.count()).select_from(Brand).where(Brand.tenant_id == target.id)) == 1
            assert await db.scalar(select(func.count()).select_from(Account).where(Account.tenant_id == target.id)) >= 5
            other = await db.get(Tenant, other_id)
            assert other is not None and other.name == "Untouched tenant"
            demo = (await db.execute(select(Tenant).where(Tenant.slug == "demo"))).scalar_one()
            assert demo.id not in {target.id, other_id}

        runtime_dsn = _runtime_url(migrated_pg_url).replace("postgresql+asyncpg://", "postgresql://")
        runtime = await asyncpg.connect(runtime_dsn)
        try:
            assert await runtime.fetchval("SELECT count(*) FROM tenants") == 0
            await runtime.execute("SELECT set_config('app.tenant_id', $1, false)", str(target.id))
            assert await runtime.fetchval("SELECT count(*) FROM brands") == 1
        finally:
            await runtime.close()
    finally:
        await owner_engine.dispose()
