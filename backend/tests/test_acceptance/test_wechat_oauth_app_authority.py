"""Real PostgreSQL ASGI contract for WeChat OAuth consent and callback authority."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from uuid6 import uuid7

from app.api.v1 import wechat_oauth
from app.core.database import get_db_for_consumer
from app.main import app
from app.services import wechat_oauth_authority
from app.services.scan_token import create_scan_token, verify_scan_token
from app.utils.client_ip import compute_ip_hash
from tests.test_acceptance.test_consumer_consent_authority import _owner_dsn, _runtime_dsn, _seed_scan

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


class _Redis:
    def __init__(self, *, fail_state_write: bool = False):
        self.values: dict[str, str] = {}
        self.fail_state_write = fail_state_write
        self.callback_count = 0

    async def eval(self, _script, _count, pending_key, _rate_key, _ttl, _limit, state):
        existing = self.values.get(pending_key)
        if existing:
            return [1, existing]
        self.values[pending_key] = state
        return [0, state]

    async def setex(self, key, _ttl, value):
        if self.fail_state_write:
            raise RuntimeError("state unavailable")
        self.values[key] = value

    async def exists(self, key):
        return int(key in self.values)

    async def get(self, key):
        return self.values.get(key)

    async def incr(self, _key):
        self.callback_count += 1
        return self.callback_count

    async def expire(self, _key, _ttl):
        return True

    async def set(self, key, value, *, ex, nx):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)


class _WeChatResponse:
    def __init__(self, *, ok: bool):
        self.status_code = 200 if ok else 503
        self._ok = ok

    def json(self):
        return {"openid": "openid-asgi-authoritative"} if self._ok else {}


class _WeChatClient:
    def __init__(self, *, ok: bool):
        self.ok = ok

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _url, *, params):
        assert params["grant_type"] == "authorization_code"
        return _WeChatResponse(ok=self.ok)


@asynccontextmanager
async def _oauth_client(migrated_pg_url: str, monkeypatch, redis: _Redis, *, wechat_ok: bool = True):
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    callback_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_callback:yimatong_callback@")
    runtime_engine = create_async_engine(runtime_url)
    callback_engine = create_async_engine(callback_url)
    runtime_factory = async_sessionmaker(runtime_engine, expire_on_commit=False)
    callback_factory = async_sessionmaker(callback_engine, expire_on_commit=False)

    async def runtime_db():
        async with runtime_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db_for_consumer] = runtime_db
    monkeypatch.setattr(wechat_oauth, "get_redis_pool", _async_value(redis))
    monkeypatch.setattr(wechat_oauth, "get_client_ip", lambda _request: "203.0.113.91")
    monkeypatch.setattr(wechat_oauth, "_cash_connector", _async_value(object()))
    monkeypatch.setattr(wechat_oauth, "_oauth_credentials", lambda _connector: ("wx-app", "wx-secret"))
    monkeypatch.setattr(wechat_oauth.httpx, "AsyncClient", lambda **_kwargs: _WeChatClient(ok=wechat_ok))
    monkeypatch.setattr(wechat_oauth_authority, "callback_session_factory", callback_factory)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await callback_engine.dispose()
        await runtime_engine.dispose()


def _async_value(value):
    async def result(*_args, **_kwargs):
        return value

    return result


async def _seed_oauth(owner: asyncpg.Connection, label: str) -> tuple[dict, uuid.UUID, str]:
    graph = await _seed_scan(owner, label)
    benefit_id = uuid7()
    await owner.execute(
        "INSERT INTO benefits(id,tenant_id,name,benefit_type,config_json,stock_total,stock_used,"
        "per_person_limit,status) VALUES($1,$2,'OAuth cash','cash_red_packet','{}'::jsonb,10,0,1,'active')",
        benefit_id,
        graph["tenant"],
    )
    token = create_scan_token(
        str(graph["public_id"]),
        compute_ip_hash("203.0.113.91"),
        tenant_id=str(graph["tenant"]),
        scan_event_id=str(graph["scan_event_id"]),
        scan_time=graph["scan_time"].isoformat(),
        visitor_id=str(graph["visitor_id"]),
    )
    return graph, benefit_id, token


async def test_oauth_asgi_persists_current_consent_then_callback_role_binding(migrated_pg_url, monkeypatch):
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    graph, benefit_id, token = await _seed_oauth(owner, "oauth-asgi-success")
    redis = _Redis()
    try:
        async with _oauth_client(migrated_pg_url, monkeypatch, redis) as client:
            started = await client.post(
                "/api/v1/wechat/auth-url",
                json={"benefit_id": str(benefit_id), "scan_token": token, "consent_granted": True},
            )
            assert started.status_code == 200, started.text
            state = parse_qs(urlsplit(started.json()["auth_url"]).query)["state"][0]
            stored = json.loads(redis.values[f"wechat-oauth:v1:{state}"])
            consent_id = uuid.UUID(stored["consent_id"])
            consent = await owner.fetchrow(
                "SELECT status,purpose,policy_id,policy_digest,consumer_id FROM consent_records "
                "WHERE tenant_id=$1 AND id=$2",
                graph["tenant"],
                consent_id,
            )
            assert consent and consent["status"] == "granted" and consent["purpose"] == "wechat_cash_payout"

            callback = await client.get(
                "/api/v1/wechat/oauth-callback", params={"code": "verified-code", "state": state}
            )
            assert callback.status_code == 303, callback.text
            rebound = verify_scan_token(parse_qs(urlsplit(callback.headers["location"]).fragment)["scan_token"][0])
            assert rebound and uuid.UUID(rebound["consumer_id"])
            consumer_id = uuid.UUID(rebound["consumer_id"])
            assert await owner.fetchval(
                "SELECT consumer_id=$1 FROM anonymous_visitors WHERE tenant_id=$2 AND visitor_id=$3",
                consumer_id,
                graph["tenant"],
                graph["visitor_id"],
            )
            assert await owner.fetchval(
                "SELECT consumer_id=$1 FROM consent_records WHERE tenant_id=$2 AND id=$3",
                consumer_id,
                graph["tenant"],
                consent_id,
            )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM consumer_consent_actions WHERE tenant_id=$1 AND action='wechat_bind'",
                    graph["tenant"],
                )
                == 1
            )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM platform_audit_log WHERE target_tenant_id=$1 "
                    "AND action='consumer_wechat_bound'",
                    str(graph["tenant"]),
                )
                == 1
            )
    finally:
        await owner.close()


async def test_oauth_asgi_failures_leave_no_partial_binding_and_are_retryable(migrated_pg_url, monkeypatch):
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    graph, benefit_id, token = await _seed_oauth(owner, "oauth-asgi-failures")
    try:
        failing_redis = _Redis(fail_state_write=True)
        async with _oauth_client(migrated_pg_url, monkeypatch, failing_redis) as client:
            failed_state = await client.post(
                "/api/v1/wechat/auth-url",
                json={"benefit_id": str(benefit_id), "scan_token": token, "consent_granted": True},
            )
            assert failed_state.status_code == 500
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM consent_records WHERE tenant_id=$1 AND purpose='wechat_cash_payout'",
                graph["tenant"],
            )
            == 0
        )

        redis = _Redis()
        async with _oauth_client(migrated_pg_url, monkeypatch, redis, wechat_ok=False) as client:
            started = await client.post(
                "/api/v1/wechat/auth-url",
                json={"benefit_id": str(benefit_id), "scan_token": token, "consent_granted": True},
            )
            state = parse_qs(urlsplit(started.json()["auth_url"]).query)["state"][0]
            exchange_failed = await client.get(
                "/api/v1/wechat/oauth-callback", params={"code": "failed-code", "state": state}
            )
            assert exchange_failed.status_code == 502
            assert f"wechat-oauth:v1:{state}" in redis.values
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM consumer_consent_actions WHERE tenant_id=$1 AND action='wechat_bind'",
                graph["tenant"],
            )
            == 0
        )

        async with _oauth_client(migrated_pg_url, monkeypatch, redis) as client:
            await owner.execute(
                "UPDATE benefits SET status='inactive' WHERE tenant_id=$1 AND id=$2", graph["tenant"], benefit_id
            )
            bind_failed = await client.get(
                "/api/v1/wechat/oauth-callback", params={"code": "verified-code", "state": state}
            )
            assert bind_failed.status_code == 403
            assert f"wechat-oauth:v1:{state}" in redis.values
            await owner.execute(
                "UPDATE benefits SET status='active' WHERE tenant_id=$1 AND id=$2", graph["tenant"], benefit_id
            )
            retried = await client.get(
                "/api/v1/wechat/oauth-callback", params={"code": "verified-code", "state": state}
            )
            assert retried.status_code == 303, retried.text
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM consumer_consent_actions WHERE tenant_id=$1 AND action='wechat_bind'",
                graph["tenant"],
            )
            == 1
        )

        runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
        try:
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await runtime.execute(
                        "UPDATE consent_records SET consumer_id=$1 WHERE tenant_id=$2 AND public_id=$3",
                        uuid7(),
                        graph["tenant"],
                        graph["public_id"],
                    )
        finally:
            await runtime.close()
    finally:
        await owner.close()
