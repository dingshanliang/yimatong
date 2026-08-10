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
