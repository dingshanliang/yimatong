"""Real PostgreSQL proof for refresh-family migration and replay convergence."""

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.middleware.tenant import TenantScopeMiddleware
from app.models.auth_security import AuthSession
from app.models.tenant import Account, Organization, Tenant
from app.services import auth as auth_service
from app.services.auth import AuthError
from app.utils.security import AUTH_SESSION_CACHE_PREFIX, decode_token, hash_password

BACKEND_DIR = Path(__file__).resolve().parents[2]
PARENT_REVISION = "fec8dda0b399"


def _alembic(database_url: str, command: str, target: str | None = None) -> None:
    env = os.environ.copy()
    env.update(
        {
            "database_url": database_url,
            "migration_database_url": database_url,
            "control_database_url": database_url,
        }
    )
    arguments = [sys.executable, "-m", "alembic"]
    if command == "check":
        arguments.extend(["-x", "baseline_legacy_timestamp_nullability=true"])
    arguments.append(command)
    if target is not None:
        arguments.append(target)
    result = subprocess.run(
        arguments,
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{command} {target or ''} failed:\n{result.stdout}\n{result.stderr}"


class RecordingSecurityCache:
    def __init__(self) -> None:
        self.revoked: set[str] = set()

    async def revoke_token(self, jti: str, ttl: int) -> None:
        assert ttl > 0
        self.revoked.add(jti)


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_refresh_family_roundtrip_and_concurrent_replay_revokes_descendant(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """One replay winner cannot keep using the newly minted descendant family."""

    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    _alembic(migrated_pg_url, "upgrade", "head")
    _alembic(migrated_pg_url, "check")

    engine = create_async_engine(migrated_pg_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    session_id = uuid.uuid4()
    cache = RecordingSecurityCache()

    async def verify_without_redis(token: str) -> dict | None:
        payload = decode_token(token)
        return payload if payload.get("type") == "refresh" else None

    monkeypatch.setattr(auth_service, "verify_refresh_token", verify_without_redis)

    try:
        async with factory() as db:
            tenant = Tenant(id=tenant_id, name="Refresh family", slug=f"refresh-{tenant_id.hex[:8]}")
            db.add(tenant)
            await db.flush()
            organization = Organization(tenant_id=tenant_id, name="Refresh family")
            db.add(organization)
            await db.flush()
            account = Account(
                id=account_id,
                tenant_id=tenant_id,
                organization_id=organization.id,
                email=f"refresh-{account_id.hex[:8]}@example.com",
                hashed_password=hash_password("Password1"),
                name="Refresh family",
                roles=[],
            )
            db.add(account)
            await db.flush()
            token_pair = auth_service._build_token_pair(account, "brand", session_id)
            refresh_payload = decode_token(token_pair["refresh_token"])
            db.add(
                AuthSession(
                    id=session_id,
                    account_id=account_id,
                    tenant_id=tenant_id,
                    auth_version=account.auth_version,
                    current_refresh_jti=refresh_payload["jti"],
                    expires_at=datetime.fromtimestamp(refresh_payload["exp"], tz=UTC),
                )
            )
            await db.commit()

        other_tenant_id = uuid.uuid4()
        async with factory() as db:
            db.add(Tenant(id=other_tenant_id, name="Other workspace", slug=f"other-{other_tenant_id.hex[:8]}"))
            await db.commit()
            db.add(
                AuthSession(
                    id=uuid.uuid4(),
                    account_id=account_id,
                    tenant_id=other_tenant_id,
                    auth_version=0,
                    current_refresh_jti=str(uuid.uuid4()),
                    expires_at=datetime.fromtimestamp(refresh_payload["exp"], tz=UTC),
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

        async def rotate() -> dict:
            async with factory() as db:
                return await auth_service.refresh_access_token(db, token_pair["refresh_token"], cache)

        results = await asyncio.gather(rotate(), rotate(), return_exceptions=True)
        successes = [result for result in results if isinstance(result, dict)]
        failures = [result for result in results if isinstance(result, AuthError)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].code == 401

        descendant = successes[0]
        async with factory() as db:
            with pytest.raises(AuthError, match="刷新会话已撤销") as rejected:
                await auth_service.refresh_access_token(db, descendant["refresh_token"], cache)
            assert rejected.value.code == 401

        async with factory() as db:
            family = (await db.execute(select(AuthSession).where(AuthSession.id == session_id))).scalar_one()
            assert family.revoked_at is not None
            assert f"{AUTH_SESSION_CACHE_PREFIX}{session_id}" in cache.revoked

            runtime_privileges = (
                await db.execute(
                    text("SELECT has_table_privilege('yimatong_app', 'auth_sessions', 'SELECT,INSERT,UPDATE,DELETE')")
                )
            ).scalar_one()
            assert runtime_privileges is False

        monkeypatch.setattr("app.core.database._is_pg", True)
        monkeypatch.setattr("app.core.database.control_session_factory", factory)
        middleware = TenantScopeMiddleware(lambda scope, receive, send: None)
        assert not await middleware._load_auth_session_access(
            session_id=str(session_id),
            account_id=str(account_id),
            tenant_id=str(tenant_id),
            token_auth_version=0,
        )

        # Parent-version code has no AuthSession table. Downgrade must therefore
        # invalidate every token in the represented family via Account.auth_version
        # before discarding the authoritative family/revocation state.
        family_tokens = [
            token_pair["access_token"],
            token_pair["refresh_token"],
            descendant["access_token"],
            descendant["refresh_token"],
        ]
        family_token_versions = {decode_token(token)["auth_version"] for token in family_tokens}
        assert family_token_versions == {0}

        # Hold an uncommitted family insert while downgrade starts. The migration
        # must wait at its first ACCESS EXCLUSIVE lock, then include the newly
        # committed row in the account-version invalidation scan.
        raced_account_id = uuid.uuid4()
        raced_session_id = uuid.uuid4()
        async with factory() as db:
            raced_account = Account(
                id=raced_account_id,
                tenant_id=tenant_id,
                organization_id=organization.id,
                email=f"refresh-race-{raced_account_id.hex[:8]}@example.com",
                hashed_password=hash_password("Password1"),
                name="Refresh downgrade race",
                roles=[],
            )
            db.add(raced_account)
            await db.commit()
            raced_pair = auth_service._build_token_pair(raced_account, "brand", raced_session_id)
            raced_refresh_payload = decode_token(raced_pair["refresh_token"])

        downgrade_task: asyncio.Task[None] | None = None
        blocked_on_family_lock = False
        async with factory() as inserter:
            inserter.add(
                AuthSession(
                    id=raced_session_id,
                    account_id=raced_account_id,
                    tenant_id=tenant_id,
                    auth_version=0,
                    current_refresh_jti=raced_refresh_payload["jti"],
                    expires_at=datetime.fromtimestamp(raced_refresh_payload["exp"], tz=UTC),
                )
            )
            await inserter.flush()
            downgrade_task = asyncio.create_task(
                asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", PARENT_REVISION)
            )
            try:
                for _ in range(80):
                    async with factory() as probe:
                        blocked_on_family_lock = bool(
                            await probe.scalar(
                                text(
                                    """
                                    SELECT EXISTS (
                                        SELECT 1
                                        FROM pg_stat_activity
                                        WHERE datname = current_database()
                                          AND pid <> pg_backend_pid()
                                          AND query LIKE 'LOCK TABLE public.auth_sessions IN ACCESS EXCLUSIVE MODE%'
                                          AND wait_event_type = 'Lock'
                                    )
                                    """
                                )
                            )
                        )
                    if blocked_on_family_lock:
                        break
                    await asyncio.sleep(0.05)
            finally:
                await inserter.commit()

        assert downgrade_task is not None
        await downgrade_task
        assert blocked_on_family_lock is True
        async with factory() as db:
            downgraded_auth_version = await db.scalar(
                text("SELECT auth_version FROM accounts WHERE id = :account_id"),
                {"account_id": account_id},
            )
            raced_auth_version = await db.scalar(
                text("SELECT auth_version FROM accounts WHERE id = :account_id"),
                {"account_id": raced_account_id},
            )
            auth_sessions_removed = await db.scalar(text("SELECT to_regclass('public.auth_sessions') IS NULL"))
        assert downgraded_auth_version == 1
        assert raced_auth_version == 1
        assert auth_sessions_removed is True
        assert all(decode_token(token)["auth_version"] != downgraded_auth_version for token in family_tokens)
        assert decode_token(raced_pair["access_token"])["auth_version"] != raced_auth_version
        assert decode_token(raced_pair["refresh_token"])["auth_version"] != raced_auth_version

        _alembic(migrated_pg_url, "upgrade", "head")
        _alembic(migrated_pg_url, "check")
    finally:
        await engine.dispose()
