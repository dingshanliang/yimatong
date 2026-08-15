from __future__ import annotations

import asyncio

import asyncpg
import pytest

from tests.test_acceptance import conftest as acceptance_conftest
from tests.test_acceptance.conftest import (
    AcceptanceDatabaseCleanupError,
    AcceptanceDatabaseLease,
    AcceptanceDatabaseOwnershipError,
    _assert_same_local_cluster,
    _create_owned_database,
    _drop_database_with_retry,
    _resolve_acceptance_database,
    _validate_local_acceptance_endpoints,
)

TEST_DATABASE = "yimatong_acceptance_cleanup_test"
TEST_ADMIN_DSN = "postgresql://admin:test@localhost:5433/postgres"
TEST_TARGET_DSN = f"postgresql+asyncpg://admin:test@localhost:5433/{TEST_DATABASE}"
TEST_MARKER = "yimatong-acceptance-owner:test-token"


class _FakeConnection:
    def __init__(
        self,
        *,
        exists: bool = True,
        marker: str | None = TEST_MARKER,
        drop_error: Exception | None = None,
        still_exists: bool = False,
        system_identifier: str = "cluster-a",
        current_database: str = TEST_DATABASE,
        create_error: Exception | None = None,
        comment_error: Exception | None = None,
        migration_heads: tuple[str, ...] = ("current-head",),
    ) -> None:
        self.exists = exists
        self.marker = marker
        self.drop_error = drop_error
        self.still_exists = still_exists
        self.system_identifier = system_identifier
        self.current_database = current_database
        self.create_error = create_error
        self.comment_error = comment_error
        self.migration_heads = migration_heads
        self.closed = False
        self.terminated = False
        self.executed: list[str] = []

    async def execute(self, sql: str) -> None:
        self.executed.append(sql)
        if sql == f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)':
            if self.drop_error is not None:
                raise self.drop_error
            if not self.still_exists:
                self.exists = False
            return
        if sql == f'CREATE DATABASE "{TEST_DATABASE}" OWNER yimatong':
            if self.create_error is not None:
                raise self.create_error
            self.exists = True
            return
        if sql.startswith(f'COMMENT ON DATABASE "{TEST_DATABASE}" IS '):
            if self.comment_error is not None:
                raise self.comment_error
            self.marker = TEST_MARKER

    async def fetchrow(self, sql: str, database_name: str):
        assert sql == "SELECT shobj_description(oid, 'pg_database') AS marker FROM pg_database WHERE datname = $1"
        assert database_name == TEST_DATABASE
        return {"marker": self.marker} if self.exists else None

    async def fetchval(self, sql: str, *args: object):
        if sql == "SELECT system_identifier::text FROM pg_control_system()":
            return self.system_identifier
        if sql == "SELECT current_database()":
            return self.current_database
        if sql == "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)":
            assert args == (TEST_DATABASE,)
            return self.exists
        raise AssertionError(f"unexpected SQL: {sql}")

    async def fetch(self, sql: str):
        assert sql == "SELECT version_num FROM alembic_version ORDER BY 1"
        return [{"version_num": head} for head in self.migration_heads]

    async def close(self) -> None:
        self.closed = True

    def terminate(self) -> None:
        self.terminated = True


def _connection_factory(connections: list[_FakeConnection], seen_dsns: list[str] | None = None):
    remaining = iter(connections)

    async def _connect(dsn: str) -> _FakeConnection:
        if seen_dsns is not None:
            seen_dsns.append(dsn)
        return next(remaining)

    return _connect


def _run_cleanup(
    connections: list[_FakeConnection],
    *,
    marker: str = TEST_MARKER,
    allow_unmarked_created: bool = False,
) -> None:
    asyncio.run(
        _drop_database_with_retry(
            TEST_DATABASE,
            TEST_ADMIN_DSN,
            expected_owner_marker=marker,
            allow_unmarked_created=allow_unmarked_created,
            total_timeout=1.0,
            attempt_timeout=0.2,
            retry_backoff=0.0,
            max_attempts=len(connections),
            connect=_connection_factory(connections),
        )
    )


def _lease() -> AcceptanceDatabaseLease:
    return AcceptanceDatabaseLease(
        database_name=TEST_DATABASE,
        database_dsn=TEST_TARGET_DSN,
        owner_token="test-token",
    )


def _snapshot_timeout_result(title: str) -> object:
    return acceptance_conftest.subprocess.CompletedProcess(
        args=["alembic", "upgrade", "head"],
        returncode=1,
        stdout=f"Running upgrade previous -> current, {title}\n",
        stderr=(
            "asyncpg.exceptions.QueryCanceledError: canceling statement due to statement timeout\n"
            "[SQL: SELECT pg_sleep(10) WHERE EXISTS (SELECT 1 FROM pg_stat_activity)]\n"
        ),
    )


def test_run_migrations_resumes_two_exact_snapshot_timeouts_then_verifies_ownership(monkeypatch) -> None:
    results = iter(
        [
            _snapshot_timeout_result("build WeCom contact tenant identity index online"),
            _snapshot_timeout_result("build WeCom member-scoped contact indexes online"),
            acceptance_conftest.subprocess.CompletedProcess(["alembic"], 0, "", ""),
        ]
    )
    calls: list[object] = []
    migration_envs: list[dict[str, str]] = []

    def run(*args, **kwargs):
        migration_envs.append(kwargs["env"])
        return next(results)

    monkeypatch.setenv("database_url", "postgresql+asyncpg://wrong/global")
    monkeypatch.setenv("migration_database_url", "postgresql+asyncpg://wrong/global")
    monkeypatch.setenv("control_database_url", "postgresql+asyncpg://wrong/global")
    monkeypatch.setattr(acceptance_conftest.subprocess, "run", run)
    monkeypatch.setattr(acceptance_conftest, "_assert_migration_head_and_ownership", calls.append)

    lease = _lease()
    acceptance_conftest.run_owned_migrations_with_snapshot_retry(lease)

    assert calls == [lease]
    assert len(migration_envs) == 3
    assert all(
        env[variable] == lease.database_dsn
        for env in migration_envs
        for variable in ("database_url", "migration_database_url", "control_database_url")
    )


def test_migration_verification_requires_dynamic_sole_head_and_matching_owner(monkeypatch) -> None:
    lease = _lease()
    connection = _FakeConnection()
    seen_dsns: list[str] = []

    class _Script:
        @staticmethod
        def get_heads() -> tuple[str, ...]:
            return ("current-head",)

    monkeypatch.setattr(acceptance_conftest.ScriptDirectory, "from_config", lambda _config: _Script())
    monkeypatch.setattr(
        acceptance_conftest.asyncpg,
        "connect",
        _connection_factory([connection], seen_dsns),
    )

    acceptance_conftest._assert_migration_head_and_ownership(lease)

    assert seen_dsns == [lease.database_dsn.replace("postgresql+asyncpg://", "postgresql://")]
    assert connection.closed


@pytest.mark.parametrize(
    ("migration_heads", "marker", "error", "message"),
    [
        (("stale-head",), TEST_MARKER, AssertionError, "head mismatch"),
        (("current-head",), "yimatong-acceptance-owner:foreign", AcceptanceDatabaseOwnershipError, "marker changed"),
    ],
)
def test_migration_verification_rejects_head_or_owner_drift(
    monkeypatch,
    migration_heads: tuple[str, ...],
    marker: str,
    error: type[Exception],
    message: str,
) -> None:
    class _Script:
        @staticmethod
        def get_heads() -> tuple[str, ...]:
            return ("current-head",)

    connection = _FakeConnection(migration_heads=migration_heads, marker=marker)
    monkeypatch.setattr(acceptance_conftest.ScriptDirectory, "from_config", lambda _config: _Script())
    monkeypatch.setattr(
        acceptance_conftest.asyncpg,
        "connect",
        _connection_factory([connection]),
    )

    with pytest.raises(error, match=message):
        acceptance_conftest._assert_migration_head_and_ownership(_lease())

    assert connection.closed


def test_run_migrations_exact_snapshot_timeout_exhaustion_propagates(monkeypatch) -> None:
    result = _snapshot_timeout_result("build WeCom contact tenant identity index online")
    calls = 0

    def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return result

    monkeypatch.setattr(acceptance_conftest.subprocess, "run", run)
    monkeypatch.setattr(
        acceptance_conftest,
        "_assert_migration_head_and_ownership",
        lambda _lease: pytest.fail("ownership assertion must not run after failed migration"),
    )

    with pytest.raises(pytest.fail.Exception, match="alembic upgrade head failed"):
        acceptance_conftest.run_owned_migrations_with_snapshot_retry(_lease())
    assert calls == 3


def test_run_migrations_nonmatching_failure_is_not_retried(monkeypatch) -> None:
    calls = 0

    def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return acceptance_conftest.subprocess.CompletedProcess(
            ["alembic"], 1, "", "RuntimeError: unrelated migration failure"
        )

    monkeypatch.setattr(acceptance_conftest.subprocess, "run", run)

    with pytest.raises(pytest.fail.Exception, match="unrelated migration failure"):
        acceptance_conftest.run_owned_migrations_with_snapshot_retry(_lease())
    assert calls == 1


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


def test_cleanup_refuses_foreign_owner_marker_without_drop() -> None:
    connection = _FakeConnection(marker="yimatong-acceptance-owner:foreign")

    with pytest.raises(AcceptanceDatabaseOwnershipError, match="owned by another run"):
        _run_cleanup([connection])

    assert not any(sql.startswith("DROP DATABASE") for sql in connection.executed)


def test_cleanup_refuses_missing_owner_marker_without_drop() -> None:
    connection = _FakeConnection(marker=None)

    with pytest.raises(AcceptanceDatabaseOwnershipError, match="no matching run owner marker"):
        _run_cleanup([connection])

    assert not any(sql.startswith("DROP DATABASE") for sql in connection.executed)


def test_cleanup_allows_unmarked_database_created_by_current_run() -> None:
    connection = _FakeConnection(marker=None)

    _run_cleanup([connection], allow_unmarked_created=True)

    assert any(sql.startswith("DROP DATABASE") for sql in connection.executed)


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
                expected_owner_marker=TEST_MARKER,
                total_timeout=1.0,
                attempt_timeout=0.2,
                retry_backoff=0.0,
                max_attempts=1,
                connect=_connect,
            )
        )

    assert not connect_called


@pytest.mark.parametrize(
    ("admin_dsn", "target_dsn"),
    [
        ("postgresql://admin:test@example.com:5433/postgres", TEST_TARGET_DSN),
        ("postgresql://admin:test@localhost:5432/postgres", TEST_TARGET_DSN),
        (TEST_ADMIN_DSN, f"postgresql+asyncpg://admin:test@localhost:5432/{TEST_DATABASE}"),
        (f"postgresql://admin:test@localhost:5433/{TEST_DATABASE}", TEST_TARGET_DSN),
        (TEST_ADMIN_DSN + "?host=example.com", TEST_TARGET_DSN),
    ],
)
def test_rejects_unapproved_acceptance_endpoints(admin_dsn: str, target_dsn: str) -> None:
    with pytest.raises(RuntimeError):
        _validate_local_acceptance_endpoints(admin_dsn, target_dsn, TEST_DATABASE)


def test_same_local_cluster_identity_is_required() -> None:
    connections = [
        _FakeConnection(system_identifier="cluster-a"),
        _FakeConnection(system_identifier="cluster-b"),
    ]

    with pytest.raises(AcceptanceDatabaseOwnershipError, match="different PostgreSQL clusters"):
        asyncio.run(
            _assert_same_local_cluster(
                TEST_ADMIN_DSN,
                TEST_TARGET_DSN,
                TEST_DATABASE,
                connect=_connection_factory(connections),
            )
        )

    assert all(connection.closed for connection in connections)


def test_create_refuses_preexisting_database_without_drop() -> None:
    lease = _lease()
    connections = [
        _FakeConnection(system_identifier="cluster-a"),
        _FakeConnection(system_identifier="cluster-a"),
        _FakeConnection(exists=True, marker="foreign"),
    ]

    with pytest.raises(AcceptanceDatabaseOwnershipError, match="already exists"):
        asyncio.run(
            _create_owned_database(
                lease,
                TEST_ADMIN_DSN,
                connect=_connection_factory(connections),
            )
        )

    assert not lease.created
    assert not any(sql.startswith("DROP DATABASE") for connection in connections for sql in connection.executed)


def test_concurrent_create_loser_never_claims_or_drops_winner() -> None:
    lease = _lease()
    connections = [
        _FakeConnection(system_identifier="cluster-a"),
        _FakeConnection(system_identifier="cluster-a"),
        _FakeConnection(
            exists=False,
            marker=None,
            create_error=asyncpg.DuplicateDatabaseError("duplicate database"),
        ),
    ]

    with pytest.raises(AcceptanceDatabaseOwnershipError, match="concurrently claimed"):
        asyncio.run(
            _create_owned_database(
                lease,
                TEST_ADMIN_DSN,
                connect=_connection_factory(connections),
            )
        )

    assert not lease.created
    assert not any(sql.startswith("DROP DATABASE") for connection in connections for sql in connection.executed)


def test_create_records_marker_and_verifies_target_identity() -> None:
    lease = _lease()
    seen_dsns: list[str] = []
    connections = [
        _FakeConnection(system_identifier="cluster-a"),
        _FakeConnection(system_identifier="cluster-a"),
        _FakeConnection(exists=False, marker=None),
        _FakeConnection(system_identifier="cluster-a", current_database=TEST_DATABASE),
    ]

    asyncio.run(
        _create_owned_database(
            lease,
            TEST_ADMIN_DSN,
            connect=_connection_factory(connections, seen_dsns),
        )
    )

    assert lease.created and lease.marker_written
    assert lease.admin_system_identifier == "cluster-a"
    assert seen_dsns[0] == TEST_ADMIN_DSN
    assert seen_dsns[1] == TEST_ADMIN_DSN
    assert seen_dsns[-1] == TEST_TARGET_DSN.replace("postgresql+asyncpg://", "postgresql://")


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


def _patch_successful_lifecycle(monkeypatch: pytest.MonkeyPatch) -> list[AcceptanceDatabaseLease]:
    leases: list[AcceptanceDatabaseLease] = []
    monkeypatch.setattr(acceptance_conftest, "_validate_local_acceptance_endpoints", lambda *_args: None)
    monkeypatch.setattr(acceptance_conftest, "_pg_available", lambda: True)

    async def _create(lease: AcceptanceDatabaseLease, _admin_dsn: str) -> None:
        lease.created = True
        lease.marker_written = True
        leases.append(lease)

    monkeypatch.setattr(acceptance_conftest, "_create_owned_database", _create)
    monkeypatch.setattr(acceptance_conftest, "_prepare_runtime_role", lambda: None)
    monkeypatch.setattr(acceptance_conftest, "run_owned_migrations_with_snapshot_retry", lambda _lease: None)
    monkeypatch.setattr(acceptance_conftest, "_provision_test_principals", lambda: None)
    monkeypatch.setattr(acceptance_conftest, "_assert_target_database", lambda _lease: None)
    return leases


@pytest.mark.parametrize(
    "failure_point",
    [
        "_prepare_runtime_role",
        "run_owned_migrations_with_snapshot_retry",
        "_provision_test_principals",
        "_assert_target_database",
    ],
)
def test_every_post_create_setup_failure_triggers_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    _patch_successful_lifecycle(monkeypatch)
    cleanup_calls: list[AcceptanceDatabaseLease] = []

    def _fail(*_args) -> None:
        raise RuntimeError(f"{failure_point} failed")

    monkeypatch.setattr(acceptance_conftest, failure_point, _fail)
    monkeypatch.setattr(acceptance_conftest, "_drop_owned_database", cleanup_calls.append)
    fixture_generator = acceptance_conftest.migrated_pg_url.__wrapped__()

    with pytest.raises(RuntimeError, match=failure_point):
        next(fixture_generator)

    assert len(cleanup_calls) == 1


def test_marker_write_failure_after_create_still_triggers_owner_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_successful_lifecycle(monkeypatch)
    cleanup_calls: list[AcceptanceDatabaseLease] = []

    async def _fail_after_create(lease: AcceptanceDatabaseLease, _admin_dsn: str) -> None:
        lease.created = True
        raise RuntimeError("marker write failed")

    monkeypatch.setattr(acceptance_conftest, "_create_owned_database", _fail_after_create)
    monkeypatch.setattr(acceptance_conftest, "_drop_owned_database", cleanup_calls.append)
    fixture_generator = acceptance_conftest.migrated_pg_url.__wrapped__()

    with pytest.raises(RuntimeError, match="marker write failed"):
        next(fixture_generator)

    assert len(cleanup_calls) == 1
    assert cleanup_calls[0].created and not cleanup_calls[0].marker_written


def test_setup_failure_remains_primary_when_cleanup_also_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_successful_lifecycle(monkeypatch)
    monkeypatch.setattr(
        acceptance_conftest,
        "_prepare_runtime_role",
        lambda: (_ for _ in ()).throw(ValueError("primary setup failure")),
    )
    monkeypatch.setattr(
        acceptance_conftest,
        "_drop_owned_database",
        lambda _lease: (_ for _ in ()).throw(AcceptanceDatabaseCleanupError("cleanup failure")),
    )
    fixture_generator = acceptance_conftest.migrated_pg_url.__wrapped__()

    with pytest.raises(ValueError, match="primary setup failure") as exc_info:
        next(fixture_generator)

    assert any("cleanup failure" in note for note in exc_info.value.__notes__)


def test_thrown_test_failure_remains_primary_when_cleanup_also_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_successful_lifecycle(monkeypatch)
    monkeypatch.setattr(
        acceptance_conftest,
        "_drop_owned_database",
        lambda _lease: (_ for _ in ()).throw(AcceptanceDatabaseCleanupError("cleanup failure")),
    )
    fixture_generator = acceptance_conftest.migrated_pg_url.__wrapped__()
    next(fixture_generator)

    with pytest.raises(AssertionError, match="test failure") as exc_info:
        fixture_generator.throw(AssertionError("test failure"))

    assert any("cleanup failure" in note for note in exc_info.value.__notes__)


def test_normal_teardown_propagates_cleanup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_successful_lifecycle(monkeypatch)
    monkeypatch.setattr(
        acceptance_conftest,
        "_drop_owned_database",
        lambda _lease: (_ for _ in ()).throw(AcceptanceDatabaseCleanupError("database remains")),
    )
    fixture_generator = acceptance_conftest.migrated_pg_url.__wrapped__()
    next(fixture_generator)

    with pytest.raises(AcceptanceDatabaseCleanupError, match="database remains"):
        next(fixture_generator)
