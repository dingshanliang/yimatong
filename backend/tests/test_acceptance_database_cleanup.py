from __future__ import annotations

import asyncio

import pytest

from tests.test_acceptance import conftest as acceptance_conftest
from tests.test_acceptance.conftest import (
    AcceptanceDatabaseCleanupError,
    _drop_database_with_retry,
    _resolve_acceptance_database,
)

TEST_DATABASE = "yimatong_acceptance_cleanup_test"
TEST_ADMIN_DSN = "postgresql://admin:test@localhost/postgres"


class _FakeConnection:
    def __init__(self, *, drop_error: Exception | None = None, still_exists: bool = False) -> None:
        self.drop_error = drop_error
        self.still_exists = still_exists
        self.closed = False
        self.terminated = False

    async def execute(self, sql: str) -> None:
        assert sql == f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)'
        if self.drop_error is not None:
            raise self.drop_error

    async def fetchval(self, sql: str, database_name: str) -> bool:
        assert sql == "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)"
        assert database_name == TEST_DATABASE
        return self.still_exists

    async def close(self) -> None:
        self.closed = True

    def terminate(self) -> None:
        self.terminated = True


def _connection_factory(connections: list[_FakeConnection]):
    remaining = iter(connections)

    async def _connect(dsn: str) -> _FakeConnection:
        assert dsn == TEST_ADMIN_DSN
        return next(remaining)

    return _connect


def _run_cleanup(connections: list[_FakeConnection]) -> None:
    asyncio.run(
        _drop_database_with_retry(
            TEST_DATABASE,
            TEST_ADMIN_DSN,
            total_timeout=1.0,
            attempt_timeout=0.2,
            retry_backoff=0.0,
            max_attempts=len(connections),
            connect=_connection_factory(connections),
        )
    )


def test_cleanup_retries_transient_drop_failure_then_confirms_absence() -> None:
    connections = [
        _FakeConnection(drop_error=RuntimeError("database is being accessed")),
        _FakeConnection(still_exists=False),
    ]

    _run_cleanup(connections)

    assert all(connection.closed for connection in connections)


def test_cleanup_raises_when_every_drop_attempt_fails() -> None:
    connections = [_FakeConnection(drop_error=RuntimeError("drop failed")) for _ in range(3)]

    with pytest.raises(AcceptanceDatabaseCleanupError, match="failed to remove dedicated acceptance database"):
        _run_cleanup(connections)

    assert all(connection.closed for connection in connections)


def test_cleanup_raises_when_drop_returns_but_database_never_disappears() -> None:
    connections = [_FakeConnection(still_exists=True) for _ in range(3)]

    with pytest.raises(AcceptanceDatabaseCleanupError) as exc_info:
        _run_cleanup(connections)

    assert "still exists after DROP DATABASE" in str(exc_info.value.__cause__)
    assert all(connection.closed for connection in connections)


def test_cleanup_retries_until_pg_database_confirms_final_absence() -> None:
    connections = [_FakeConnection(still_exists=True), _FakeConnection(still_exists=False)]

    _run_cleanup(connections)

    assert all(connection.closed for connection in connections)


def test_cleanup_rejects_non_dedicated_database_before_connecting() -> None:
    connect_called = False

    async def _connect(_dsn: str) -> _FakeConnection:
        nonlocal connect_called
        connect_called = True
        return _FakeConnection()

    with pytest.raises(RuntimeError, match="unique dedicated name"):
        asyncio.run(
            _drop_database_with_retry(
                "yimatong",
                TEST_ADMIN_DSN,
                total_timeout=1.0,
                attempt_timeout=0.2,
                retry_backoff=0.0,
                max_attempts=1,
                connect=_connect,
            )
        )

    assert not connect_called


def test_local_defaults_generate_a_unique_database_and_matching_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACCEPTANCE_DB_NAME", raising=False)
    monkeypatch.delenv("ACCEPTANCE_PG_DSN", raising=False)

    first_name, first_dsn = _resolve_acceptance_database()
    second_name, second_dsn = _resolve_acceptance_database()

    assert first_name.startswith("yimatong_acceptance_")
    assert second_name.startswith("yimatong_acceptance_")
    assert first_name != second_name
    assert first_dsn.endswith(f"/{first_name}")
    assert second_dsn.endswith(f"/{second_name}")


def test_session_teardown_propagates_cleanup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_cleanup() -> None:
        raise AcceptanceDatabaseCleanupError("database remains")

    monkeypatch.setattr(acceptance_conftest, "_drop_db", _fail_cleanup)
    fixture_generator = acceptance_conftest._cleanup_db.__wrapped__("unused-dsn")
    next(fixture_generator)

    with pytest.raises(AcceptanceDatabaseCleanupError, match="database remains"):
        next(fixture_generator)
