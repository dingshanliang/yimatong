import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import seed_demo


def test_demo_code_generation_idempotency_key_is_canonical_and_stable() -> None:
    tenant_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    production_batch_id = uuid.UUID("22222222-2222-4222-8222-222222222222")

    first = seed_demo._demo_code_generation_idempotency_key(tenant_id, production_batch_id)
    second = seed_demo._demo_code_generation_idempotency_key(tenant_id, production_batch_id)

    assert str(uuid.UUID(first)) == first
    assert first == second


@pytest.mark.anyio
async def test_demo_code_batch_uses_authoritative_generation_and_delivery_chain(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    product_id = uuid.uuid4()
    sku_id = uuid.uuid4()
    production_batch_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    code_batch_id = uuid.uuid4()
    create = AsyncMock(return_value={"id": str(code_batch_id)})
    export = AsyncMock()
    printing = AsyncMock()
    delivered = AsyncMock()
    activate = AsyncMock()
    monkeypatch.setattr(seed_demo, "create_code_batch", create)
    monkeypatch.setattr(seed_demo, "generate_code_csv", export)
    monkeypatch.setattr(seed_demo, "mark_printing", printing)
    monkeypatch.setattr(seed_demo, "mark_delivered", delivered)
    monkeypatch.setattr(seed_demo, "activate_batch", activate)
    db = SimpleNamespace()

    result = await seed_demo._create_and_deliver_demo_code_batch(
        db,
        tenant_id=tenant_id,
        product_id=product_id,
        sku_id=sku_id,
        production_batch_id=production_batch_id,
        quantity=12,
        created_by=actor_id,
    )

    assert result == code_batch_id
    create.assert_awaited_once_with(
        db,
        tenant_id,
        product_id,
        sku_id,
        production_batch_id,
        12,
        actor_id,
        idempotency_key=seed_demo._demo_code_generation_idempotency_key(tenant_id, production_batch_id),
    )
    export.assert_awaited_once_with(db, tenant_id, code_batch_id, actor_id)
    printing.assert_awaited_once_with(db, tenant_id, code_batch_id, actor_id=str(actor_id))
    delivered.assert_awaited_once_with(
        db,
        tenant_id,
        code_batch_id,
        actor_id=str(actor_id),
        reason="Demo seed delivery",
        recipient="Demo operations",
        confirm="deliver",
    )
    activate.assert_awaited_once_with(db, tenant_id, code_batch_id, actor_id=str(actor_id))


class _ExecResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value


class _ScalarsResult:
    def __init__(self, value):
        self._value = value

    def __iter__(self):
        return iter(self._value)


def _demo_brand_records(production_batch_id: uuid.UUID) -> list[dict]:
    return [
        {
            "products": [
                {
                    "product": SimpleNamespace(name="演示产品"),
                    "skus": [
                        {"sku": SimpleNamespace(id=uuid.uuid4()), "production_batch": production_batch_id}
                    ],
                }
            ]
        }
    ]


async def _run_status_samples(items: list, monkeypatch) -> tuple[AsyncMock, AsyncMock]:
    """复用已有码批次跑 _ensure_code_batches 的打样阶段，返回 revoke/freeze mock。"""
    tenant_id = uuid.uuid4()
    code_batch = SimpleNamespace(id=uuid.uuid4())
    production_batch = SimpleNamespace(id=uuid.uuid4())
    revoke = AsyncMock()
    freeze = AsyncMock()
    monkeypatch.setattr(seed_demo, "revoke_code_item", revoke)
    monkeypatch.setattr(seed_demo, "freeze_code_item", freeze)

    exec_results = iter([_ExecResult(code_batch), _ExecResult(items)])
    scalars_results = iter([_ScalarsResult(items)])
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=lambda *a, **k: next(exec_results)),
        scalars=AsyncMock(side_effect=lambda *a, **k: next(scalars_results)),
    )
    returned = await seed_demo._ensure_code_batches(
        db, tenant_id, uuid.uuid4(), _demo_brand_records(production_batch)
    )
    assert [item.id for item in returned] == [item.id for item in items]
    return revoke, freeze


@pytest.mark.anyio
async def test_demo_code_status_samples_skip_non_contract_states_on_reseed(monkeypatch) -> None:
    """复用旧批次重跑：吊销切片的终态码与冻结切片的 revoked/frozen 码都必须跳过。"""
    items = [
        SimpleNamespace(id=uuid.uuid4(), public_id=f"PUB{i}", status=seed_demo.CodeItemStatus.activated)
        for i in range(12)
    ]
    # revoke_count=1、freeze_count=1：吊销切片 [0] 已 revoked，冻结切片 [1] 已 revoked
    items[0].status = seed_demo.CodeItemStatus.revoked
    items[1].status = seed_demo.CodeItemStatus.revoked

    revoke, freeze = await _run_status_samples(items, monkeypatch)

    revoke.assert_not_awaited()
    freeze.assert_not_awaited()


@pytest.mark.anyio
async def test_demo_code_status_samples_void_and_freeze_fresh_items(monkeypatch) -> None:
    """全新批次：吊销/冻结样本正常生成。"""
    items = [
        SimpleNamespace(id=uuid.uuid4(), public_id=f"PUB{i}", status=seed_demo.CodeItemStatus.activated)
        for i in range(12)
    ]

    revoke, freeze = await _run_status_samples(items, monkeypatch)

    revoke.assert_awaited_once()
    assert revoke.await_args.args[2] == items[0].id
    freeze.assert_awaited_once()
    assert freeze.await_args.args[2] == items[1].id
