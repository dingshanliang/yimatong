"""Focused PostgreSQL retry lifecycle evidence for retrospective materialization."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import DBAPIError

from app.services import retrospective


class _PgFailure(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def _db_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("materialize_due_retrospective", {}, _PgFailure(sqlstate), False)


class _ControlSession:
    """Small transaction lifecycle double matching ``async with AsyncSession``."""

    def __init__(self, materialize_result: bool | DBAPIError) -> None:
        self.materialize_result = materialize_result
        self.scalar_calls: list[dict] = []
        self.commit_count = 0
        self.rolled_back = False
        self.staged_write = False
        self.persisted_write = False
        self.exit_exception: BaseException | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        self.exit_exception = exc
        if exc is not None:
            # AsyncSession.close() rolls back an active transaction on context exit.
            self.rolled_back = True
            self.staged_write = False
        return False

    async def execute(self, statement, parameters=None):
        return None

    async def scalar(self, statement, parameters):
        self.scalar_calls.append(dict(parameters))
        if len(self.scalar_calls) == 1:
            return "d" * 64
        self.staged_write = True
        if isinstance(self.materialize_result, DBAPIError):
            raise self.materialize_result
        return self.materialize_result

    async def commit(self) -> None:
        self.commit_count += 1
        self.persisted_write = self.staged_write
        self.staged_write = False


class _ControlSessionFactory:
    def __init__(self, outcomes: list[bool | DBAPIError]) -> None:
        self.outcomes = outcomes
        self.sessions: list[_ControlSession] = []

    def __call__(self) -> _ControlSession:
        session = _ControlSession(self.outcomes[len(self.sessions)])
        self.sessions.append(session)
        return session


def _materialize_parameters(session: _ControlSession) -> dict:
    assert len(session.scalar_calls) == 2
    return session.scalar_calls[1]


async def _invoke(monkeypatch, outcomes: list[bool | DBAPIError]):
    factory = _ControlSessionFactory(outcomes)
    result = await _invoke_with_factory(monkeypatch, factory)
    return result, factory


@pytest.mark.asyncio
async def test_generate_one_busy_then_success_reuses_stable_ids_and_digest(monkeypatch):
    busy = _db_error("55P03")

    created, factory = await _invoke(monkeypatch, [busy, True])

    assert created is True
    assert len(factory.sessions) == 2
    failed, successful = factory.sessions
    assert failed.rolled_back and failed.commit_count == 0 and not failed.persisted_write
    assert failed.exit_exception is busy
    assert successful.commit_count == 1 and successful.persisted_write and not successful.rolled_back
    first_parameters = _materialize_parameters(failed)
    second_parameters = _materialize_parameters(successful)
    assert first_parameters["retrospective_id"] == second_parameters["retrospective_id"]
    assert first_parameters["ops_task_id"] == second_parameters["ops_task_id"]
    assert first_parameters["retrospective_id"].version == 7
    assert first_parameters["ops_task_id"].version == 7
    assert first_parameters["snapshot_digest"] == second_parameters["snapshot_digest"] == "d" * 64


@pytest.mark.asyncio
async def test_generate_one_non_busy_error_propagates_without_second_session(monkeypatch):
    denied = _db_error("42501")
    factory = _ControlSessionFactory([denied])

    with pytest.raises(DBAPIError) as raised:
        await _invoke_with_factory(monkeypatch, factory)

    assert raised.value is denied
    assert len(factory.sessions) == 1
    assert factory.sessions[0].rolled_back
    assert factory.sessions[0].commit_count == 0
    assert not factory.sessions[0].persisted_write


@pytest.mark.asyncio
async def test_generate_one_three_busy_attempts_propagate_final_error_without_partial_write(monkeypatch):
    busy_errors = [_db_error("55P03") for _ in range(3)]
    factory = _ControlSessionFactory(busy_errors)

    with pytest.raises(DBAPIError) as raised:
        await _invoke_with_factory(monkeypatch, factory)

    assert raised.value is busy_errors[-1]
    assert len(factory.sessions) == 3
    assert all(session.rolled_back for session in factory.sessions)
    assert all(session.commit_count == 0 for session in factory.sessions)
    assert all(not session.staged_write and not session.persisted_write for session in factory.sessions)


async def _invoke_with_factory(monkeypatch, factory: _ControlSessionFactory):
    """Invoke with a caller-owned factory so failure-path sessions remain inspectable."""
    db = AsyncMock()
    existing = MagicMock()
    existing.scalar_one_or_none.return_value = None
    db.execute.return_value = existing
    monkeypatch.setattr(retrospective, "_session_uses_postgresql", lambda _db: True)
    monkeypatch.setattr(retrospective, "build_scorecard", AsyncMock(return_value={"window_days": 7}))
    monkeypatch.setattr(retrospective, "_carryover_actions_from_previous", AsyncMock(return_value=[]))
    monkeypatch.setattr(retrospective, "_resolve_agency_owner", AsyncMock(return_value=None))
    monkeypatch.setattr(retrospective.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr("app.core.database.control_session_factory", factory)
    return await retrospective._generate_one(
        db,
        uuid.uuid4(),
        7,
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 8, tzinfo=UTC),
        date(2026, 7, 15),
    )
