import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import seed_demo


class DiversionObservationSession:
    """Resolve seed queries by selected model and exact bound tenant/subject."""

    def __init__(self, scans, code_items):
        self.scans = scans
        self.code_items = code_items

    async def scalar(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        values = set(statement.compile().params.values())
        if entity is seed_demo.ScanEvent:
            return next(
                (scan for scan in self.scans if scan.tenant_id in values and scan.id in values),
                None,
            )
        if entity is seed_demo.CodeItem:
            return next(
                (item for item in self.code_items if item.tenant_id in values and item.public_id in values),
                None,
            )
        raise AssertionError(f"unexpected seed query entity: {entity}")


@pytest.mark.anyio
async def test_diversion_seed_replays_exact_observations_through_authority(monkeypatch):
    tenant_id = uuid.uuid4()
    code_items = [
        SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, public_id=f"DEMO-CODE-{index}")
        for index in range(len(seed_demo.DIVERSION_CLUES_DATA))
    ]
    distributors = [SimpleNamespace(id=uuid.uuid4()) for _ in code_items]
    regions = [SimpleNamespace(id=uuid.uuid4()) for _ in code_items]
    scans = [
        SimpleNamespace(
            id=seed_demo._diversion_seed_uuid(tenant_id, "scan", index),
            tenant_id=tenant_id,
            public_id=code_item.public_id,
            scan_time=datetime(2026, 1, 1, 8 + index, tzinfo=UTC),
            ip_hash=f"ip-{index}",
        )
        for index, code_item in enumerate(code_items)
    ]

    record_observation = AsyncMock(return_value={"replayed": False})
    monkeypatch.setattr(seed_demo.diversion_authority, "record_observation", record_observation)

    for _ in range(2):
        session = DiversionObservationSession(scans, code_items)
        await seed_demo._ensure_diversion_observations(session, tenant_id, code_items, distributors, regions)

    assert record_observation.await_count == len(code_items) * 2
    first = record_observation.await_args_list[0]
    replay = record_observation.await_args_list[len(code_items)]
    assert first.kwargs == replay.kwargs
    assert first.kwargs["observation_id"] == seed_demo._diversion_seed_uuid(tenant_id, "observation", 0)
    assert first.kwargs["idempotency_key"] == seed_demo._diversion_seed_idem(tenant_id, 0)
    assert first.kwargs["scan_event_id"] == scans[0].id
    assert first.kwargs["public_id"] == code_items[0].public_id
    assert first.kwargs["code_item_id"] == code_items[0].id
    assert first.kwargs["rule_name"] == "cross_region_ip"


@pytest.mark.anyio
@pytest.mark.parametrize("missing", ["scan", "subject"])
async def test_diversion_observation_seed_fails_closed_for_missing_or_wrong_subject(monkeypatch, missing):
    tenant_id = uuid.uuid4()
    code_items = [
        SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, public_id=f"DEMO-CODE-{index}")
        for index in range(len(seed_demo.DIVERSION_CLUES_DATA))
    ]
    scans = [
        SimpleNamespace(
            id=seed_demo._diversion_seed_uuid(tenant_id, "scan", index),
            tenant_id=tenant_id,
            public_id=code_item.public_id,
            scan_time=datetime(2026, 1, 1, 8 + index, tzinfo=UTC),
            ip_hash=f"ip-{index}",
        )
        for index, code_item in enumerate(code_items)
    ]
    if missing == "scan":
        scans = scans[1:]
        expected = "scan fact is unavailable"
    else:
        scans[0].public_id = "FOREIGN-SUBJECT"
        expected = "scan subject is unavailable"
    record_observation = AsyncMock()
    monkeypatch.setattr(seed_demo.diversion_authority, "record_observation", record_observation)

    with pytest.raises(RuntimeError, match=expected):
        await seed_demo._ensure_diversion_observations(
            DiversionObservationSession(scans, code_items),
            tenant_id,
            code_items,
            [SimpleNamespace(id=uuid.uuid4())],
            [SimpleNamespace(id=uuid.uuid4())],
        )

    record_observation.assert_not_awaited()


@pytest.mark.anyio
async def test_diversion_scan_fact_seed_recovers_only_missing_facts():
    tenant_id = uuid.uuid4()
    code_items = [
        SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            public_id=f"DEMO-CODE-{index}",
            status=seed_demo.CodeItemStatus.activated,
        )
        for index in range(2)
    ]
    existing = SimpleNamespace(tenant_id=tenant_id, public_id=code_items[0].public_id)

    class Session:
        def __init__(self):
            self.added = []
            self.flushed = False

        async def get(self, _model, resource_id):
            if resource_id == seed_demo._diversion_seed_uuid(tenant_id, "scan", 0):
                return existing
            return None

        async def scalar(self, statement):
            values = set(statement.compile().params.values())
            return next(
                (item.id for item in code_items if item.tenant_id in values and item.public_id in values),
                None,
            )

        def add(self, resource):
            self.added.append(resource)

        async def flush(self):
            self.flushed = True

    session = Session()
    await seed_demo._ensure_diversion_scan_facts(session, tenant_id, code_items)

    assert session.flushed is True
    assert len(session.added) == 1
    created = session.added[0]
    assert created.id == seed_demo._diversion_seed_uuid(tenant_id, "scan", 1)
    assert created.public_id == code_items[1].public_id
    assert created.is_valid_visit is True
    assert created.location_source == "ip_inference"
