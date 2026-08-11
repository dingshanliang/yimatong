import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.campaign_claim_worker import (
    _fail,
    _lease,
    _process_leased,
    _record_delivery_result,
    poll_campaign_claim_outbox,
)
from app.services.connectors.generic_http import GenericHttpAdapter
from app.tasks.worker import worker_loop


@pytest.mark.anyio
async def test_claim_outbox_lease_is_database_authoritative():
    db = AsyncMock()
    result = MagicMock()
    db.execute.return_value = result
    mappings = result.mappings.return_value
    mappings.all.return_value = []
    tenant_id = uuid.uuid4()

    assert await _lease(db, tenant_id, limit=17) == []

    sql = str(db.execute.await_args.args[0])
    params = db.execute.await_args.args[1]
    assert "lease_campaign_claim_outbox" in sql
    assert params["tenant_id"] == tenant_id
    assert params["limit"] == 17


@pytest.mark.anyio
async def test_claim_outbox_failure_uses_stable_error_code_and_lease_token():
    db = AsyncMock()
    tenant_id = uuid.uuid4()
    outbox_id = uuid.uuid4()
    lease_token = uuid.uuid4()

    await _fail(db, tenant_id, outbox_id, lease_token, "delivery_exception", 60)

    params = db.execute.await_args.args[1]
    assert params == {
        "tenant_id": tenant_id,
        "outbox_id": outbox_id,
        "lease_token": lease_token,
        "error_code": "delivery_exception",
        "retry_seconds": 60,
    }


def test_worker_polls_claim_outbox_without_redis_queue_authority():
    source = inspect.getsource(worker_loop)

    assert "poll_campaign_claim_outbox()" in source
    assert "lpush" not in inspect.getsource(_lease)


def test_global_outbox_poll_uses_bootstrap_object_and_tenant_key_pair():
    source = inspect.getsource(poll_campaign_claim_outbox)

    assert "select(CampaignClaimOutbox.id, CampaignClaimOutbox.tenant_id)" in source
    assert "for _, tenant_id in tenant_rows" in source


def test_external_delivery_retries_use_the_claim_as_idempotency_authority():
    worker_source = inspect.getsource(_process_leased)
    adapter_source = inspect.getsource(GenericHttpAdapter.deliver)

    assert 'delivery_config["idempotency_key"] = str(claim.id)' in worker_source
    assert "_record_delivery_result(" in worker_source
    assert 'result.status not in {"success", "pending"}' in worker_source
    assert 'request_headers["Idempotency-Key"] = idempotency_key' in adapter_source


@pytest.mark.anyio
async def test_pending_delivery_is_recorded_as_callback_waiting_instead_of_failed():
    db = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.one.return_value = {
        "outbox_id": uuid.uuid4(),
        "current_status": "awaiting_callback",
    }
    db.execute.return_value = result

    recorded = await _record_delivery_result(
        db,
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        "pending",
        "provider-idempotency-id",
        {"status_code": 503},
    )

    assert recorded["current_status"] == "awaiting_callback"
    statement, params = db.execute.await_args.args
    assert "record_campaign_claim_delivery_result" in str(statement)
    assert params["result_status"] == "pending"
    assert params["external_id"] == "provider-idempotency-id"
