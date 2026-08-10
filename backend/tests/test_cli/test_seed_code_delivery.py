import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.cli import baseline, seed


@pytest.mark.parametrize("module", [seed, baseline])
def test_cli_code_generation_idempotency_key_is_canonical_and_stable(module) -> None:
    tenant_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    production_batch_id = uuid.UUID("22222222-2222-4222-8222-222222222222")

    first = module._seed_code_generation_idempotency_key(tenant_id, production_batch_id)
    second = module._seed_code_generation_idempotency_key(tenant_id, production_batch_id)

    assert str(uuid.UUID(first)) == first
    assert first == second


@pytest.mark.anyio
@pytest.mark.parametrize("module", [seed, baseline])
async def test_cli_code_batch_uses_authoritative_delivery_chain(module, monkeypatch) -> None:
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
    monkeypatch.setattr(module, "create_code_batch", create)
    monkeypatch.setattr(module, "generate_code_csv", export)
    monkeypatch.setattr(module, "mark_printing", printing)
    monkeypatch.setattr(module, "mark_delivered", delivered)
    monkeypatch.setattr(module, "activate_batch", activate)
    db = SimpleNamespace()

    result = await module._create_and_deliver_seed_code_batch(
        db,
        tenant_id=tenant_id,
        product_id=product_id,
        sku_id=sku_id,
        production_batch_id=production_batch_id,
        batch_code="STABLE-CODE-BATCH",
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
        batch_code="STABLE-CODE-BATCH",
        idempotency_key=module._seed_code_generation_idempotency_key(tenant_id, production_batch_id),
    )
    export.assert_awaited_once_with(db, tenant_id, code_batch_id, actor_id)
    printing.assert_awaited_once_with(db, tenant_id, code_batch_id, actor_id=str(actor_id))
    delivered.assert_awaited_once_with(
        db,
        tenant_id,
        code_batch_id,
        actor_id=str(actor_id),
        reason="Seed data delivery",
        recipient="Seed data operations",
        confirm="deliver",
    )
    activate.assert_awaited_once_with(db, tenant_id, code_batch_id, actor_id=str(actor_id))
