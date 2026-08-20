import hashlib
import time
import uuid
from datetime import UTC, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.commerce_integration import (
    CommerceIdentityHandoff,
    CommerceIntegrationMessage,
    CommerceMemberReference,
    CommerceServiceCredential,
)
from app.models.member import BrandMembership
from app.models.tenant import Tenant
from app.schemas.commerce_integration import CommerceIncomingEvent
from app.services.commerce_integration import (
    accept_commerce_event,
    create_commerce_connection,
    decode_commerce_handoff,
    disconnect_commerce_connection,
    issue_commerce_handoff,
    redeem_commerce_handoff,
    rotate_commerce_credential,
    sign_commerce_request,
    verify_commerce_signature,
)
from app.utils import utcnow


async def _membership(db, suffix: str) -> tuple[uuid.UUID, uuid.UUID, BrandMembership]:
    tenant_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    db.add(Tenant(id=tenant_id, name=f"Commerce {suffix}", slug=f"commerce-{suffix}-{tenant_id.hex[:8]}"))
    membership = BrandMembership(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        membership_number=f"MBR-{tenant_id.hex[:12].upper()}",
        status="active",
        join_consent_id=uuid.uuid4(),
    )
    db.add(membership)
    await db.flush()
    return tenant_id, actor_id, membership


async def _connection(db, suffix: str):
    tenant_id, actor_id, membership = await _membership(db, suffix)
    connection, secrets = await create_commerce_connection(
        db,
        tenant_id=tenant_id,
        external_tenant_ref=f"tenant-{suffix}",
        external_shop_ref=f"shop-{suffix}",
        base_url="https://commerce.example.test/api/",
        capabilities=["order_events", "identity_handoff", "order_events"],
        idempotency_key=f"connect-{suffix}",
        actor_id=actor_id,
    )
    return tenant_id, actor_id, membership, connection, secrets


@pytest.mark.anyio
async def test_connection_credentials_are_single_reveal_90_day_and_rotate_with_24_hour_overlap(db):
    tenant_id, actor_id, _, connection, secrets = await _connection(db, "credentials")

    assert connection.base_url == "https://commerce.example.test/api"
    assert connection.capabilities == ["identity_handoff", "order_events"]
    assert {item["direction"] for item in secrets} == {"yimatong_to_commerce", "commerce_to_yimatong"}
    assert all(
        timedelta(days=89, hours=23) < item["valid_until"] - item["valid_from"] <= timedelta(days=90)
        for item in secrets
    )

    replayed, replayed_secrets = await create_commerce_connection(
        db,
        tenant_id=tenant_id,
        external_tenant_ref="tenant-credentials",
        external_shop_ref="shop-credentials",
        base_url="https://commerce.example.test/api/",
        capabilities=["identity_handoff", "order_events"],
        idempotency_key="connect-credentials",
        actor_id=actor_id,
    )
    assert replayed.id == connection.id
    assert replayed_secrets == []

    rotated = await rotate_commerce_credential(
        db,
        tenant_id=tenant_id,
        connection_id=connection.id,
        direction="commerce_to_yimatong",
        idempotency_key="rotate-incoming",
        actor_id=actor_id,
    )
    assert rotated["version"] == 2
    old = await db.scalar(
        select(CommerceServiceCredential).where(
            CommerceServiceCredential.connection_id == connection.id,
            CommerceServiceCredential.direction == "commerce_to_yimatong",
            CommerceServiceCredential.version == 1,
        )
    )
    assert old is not None and old.overlap_until is not None
    overlap_until = old.overlap_until.replace(tzinfo=UTC) if old.overlap_until.tzinfo is None else old.overlap_until
    assert timedelta(hours=23, minutes=59) < overlap_until - utcnow() <= timedelta(hours=24)


@pytest.mark.anyio
async def test_handoff_is_pii_free_connection_scoped_and_single_use(db):
    tenant_id, _, membership, connection, _ = await _connection(db, "handoff")
    token, _ = await issue_commerce_handoff(
        db,
        tenant_id=tenant_id,
        connection_id=connection.id,
        membership_id=membership.id,
        idempotency_key="handoff-issue",
    )
    payload = decode_commerce_handoff(token)

    assert set(payload).isdisjoint({"phone", "openid", "name", "address", "membership_id"})
    assert payload["target_shop"] == connection.external_shop_ref
    assert payload["exp"] - payload["iat"] == 300

    redeemed_connection, member_ref = await redeem_commerce_handoff(db, token=token, payload=payload)
    assert redeemed_connection.id == connection.id
    assert member_ref == payload["member_ref"]
    with pytest.raises(HTTPException) as replay:
        await redeem_commerce_handoff(db, token=token, payload=payload)
    assert replay.value.status_code == 409
    assert await db.scalar(select(func.count()).select_from(CommerceIdentityHandoff)) == 1


@pytest.mark.anyio
async def test_signature_enforces_exact_body_and_five_minute_window(db):
    _, _, _, connection, secrets = await _connection(db, "signature")
    incoming = next(item for item in secrets if item["direction"] == "commerce_to_yimatong")
    credential = await db.get(CommerceServiceCredential, incoming["id"])
    assert credential is not None
    body = b'{"event_id":"order-1"}'
    timestamp = int(time.time())
    signature = sign_commerce_request(incoming["secret"], timestamp, "/api/v1/commerce/events", body)

    verify_commerce_signature(
        credential=credential,
        timestamp=str(timestamp),
        path="/api/v1/commerce/events",
        body=body,
        signature=signature,
    )
    with pytest.raises(HTTPException) as wrong_signature:
        verify_commerce_signature(
            credential=credential,
            timestamp=str(timestamp),
            path="/api/v1/commerce/events",
            body=body + b" ",
            signature=signature,
        )
    assert wrong_signature.value.detail == "invalid_commerce_signature"
    with pytest.raises(HTTPException) as stale:
        verify_commerce_signature(
            credential=credential,
            timestamp=str(timestamp - 301),
            path="/api/v1/commerce/events",
            body=body,
            signature=signature,
        )
    assert stale.value.detail == "commerce_timestamp_outside_five_minute_window"
    assert connection.status == "active"


@pytest.mark.anyio
async def test_disconnect_revokes_both_directions_and_preserves_connection_fact(db):
    tenant_id, actor_id, _, connection, _ = await _connection(db, "disconnect")

    disconnected = await disconnect_commerce_connection(
        db,
        tenant_id=tenant_id,
        connection_id=connection.id,
        idempotency_key="disconnect-commerce",
        actor_id=actor_id,
    )

    assert disconnected.id == connection.id
    assert disconnected.status == "disconnected"
    assert disconnected.disconnected_at is not None
    credentials = (
        await db.scalars(
            select(CommerceServiceCredential).where(CommerceServiceCredential.connection_id == connection.id)
        )
    ).all()
    assert len(credentials) == 2
    assert all(item.revoked_at is not None for item in credentials)


@pytest.mark.anyio
async def test_incoming_events_are_versioned_replay_safe_and_member_ref_scoped(db):
    tenant_id, _, membership, connection, secrets = await _connection(db, "events")
    incoming = next(item for item in secrets if item["direction"] == "commerce_to_yimatong")
    credential = await db.get(CommerceServiceCredential, incoming["id"])
    assert credential is not None
    token, _ = await issue_commerce_handoff(
        db,
        tenant_id=tenant_id,
        connection_id=connection.id,
        membership_id=membership.id,
        idempotency_key="events-handoff",
    )
    member_ref = decode_commerce_handoff(token)["member_ref"]
    base_event = {
        "event_id": "order-42",
        "event_version": 2,
        "event_type": "order.paid",
        "occurred_at": utcnow(),
        "member_ref": member_ref,
        "data": {"order_ref": "ORDER-42"},
    }
    digest = hashlib.sha256(b"event-v2").hexdigest()
    accepted, replayed = await accept_commerce_event(
        db, credential=credential, connection=connection, event=base_event, body_digest=digest
    )
    same, replayed = await accept_commerce_event(
        db, credential=credential, connection=connection, event=base_event, body_digest=digest
    )
    assert replayed is True and same.id == accepted.id

    older = {**base_event, "event_version": 1, "data": {"order_ref": "ORDER-42", "state": "created"}}
    older_message, replayed = await accept_commerce_event(
        db,
        credential=credential,
        connection=connection,
        event=older,
        body_digest=hashlib.sha256(b"event-v1").hexdigest(),
    )
    assert replayed is False and older_message.message_version == 1

    with pytest.raises(HTTPException) as conflict:
        await accept_commerce_event(
            db,
            credential=credential,
            connection=connection,
            event={**base_event, "data": {"changed": True}},
            body_digest=hashlib.sha256(b"changed").hexdigest(),
        )
    assert conflict.value.detail == "commerce_event_idempotency_conflict"

    before = await db.scalar(select(func.count()).select_from(CommerceIntegrationMessage))
    with pytest.raises(HTTPException) as cross_connection_ref:
        await accept_commerce_event(
            db,
            credential=credential,
            connection=connection,
            event={**base_event, "event_id": "order-foreign", "member_ref": "cmr_foreign"},
            body_digest=hashlib.sha256(b"foreign").hexdigest(),
        )
    assert cross_connection_ref.value.status_code == 403
    assert await db.scalar(select(func.count()).select_from(CommerceIntegrationMessage)) == before
    assert await db.scalar(select(func.count()).select_from(CommerceMemberReference)) == 1


def test_commerce_order_contract_rejects_inconsistent_refunds_and_event_state() -> None:
    now = utcnow()
    payload = {
        "event_id": "contract-1",
        "event_version": 1,
        "event_type": "commerce.order.refunded",
        "occurred_at": now,
        "data": {
            "order_ref": "ORDER-CONTRACT-1",
            "source_system": "medusa_v2",
            "status": "partially_refunded",
            "currency": "CNY",
            "order_original_amount_fen": 1000,
            "order_refunded_amount_fen": 200,
            "product_original_amount_fen": 800,
            "product_refunded_amount_fen": 200,
            "coverage_status": "complete",
            "paid_at": now,
            "line_items": [
                {
                    "line_ref": "line-1",
                    "product_ref": "product-1",
                    "quantity": 1,
                    "original_amount_fen": 800,
                    "refunded_amount_fen": 200,
                }
            ],
            "refunds": [
                {
                    "refund_ref": "refund-1",
                    "order_amount_fen": 200,
                    "product_amount_fen": 200,
                    "occurred_at": now,
                    "line_refunds": [{"line_ref": "line-1", "amount_fen": 200}],
                }
            ],
        },
    }
    assert CommerceIncomingEvent.model_validate(payload).data.product_refunded_amount_fen == 200
    with pytest.raises(ValueError, match="line_refunded_amount_exceeds_original"):
        CommerceIncomingEvent.model_validate(
            {
                **payload,
                "data": {
                    **payload["data"],
                    "line_items": [{**payload["data"]["line_items"][0], "refunded_amount_fen": 801}],
                },
            }
        )
    with pytest.raises(ValueError, match="event_type_status_mismatch"):
        CommerceIncomingEvent.model_validate({**payload, "event_type": "commerce.order.completed"})
