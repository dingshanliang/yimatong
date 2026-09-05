"""Real PostgreSQL proof for the cross-product commerce trust boundary."""

import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

import asyncpg
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.services.commerce_integration as commerce_service
import app.services.member_notification as notification_service
from app.core.config import settings
from app.models.commerce_integration import (
    CommerceIdentityHandoff,
    CommerceIntegrationMessage,
    CommerceMemberReference,
)
from app.services.brand_membership import bind_verified_member_identity
from app.services.commerce_coupon import transition_commerce_coupon
from app.services.commerce_integration import (
    accept_commerce_event,
    commerce_repurchase_metrics,
    create_commerce_connection,
    create_commerce_product_mapping,
    decode_commerce_handoff,
    issue_commerce_handoff,
    list_commerce_order_facts,
    redeem_commerce_handoff,
    resolve_commerce_credential,
    revoke_commerce_credential,
    rotate_commerce_credential,
    sign_commerce_request,
    verify_commerce_signature,
)
from app.services.member_notification import (
    create_marketing_notification,
    lease_delivery,
    record_delivery_result,
    update_member_notification_preference,
)
from app.utils import utcnow
from tests.test_acceptance.conftest import seed_baseline
from tests.test_acceptance.test_brand_membership_rls import _seed_join_facts

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

TABLES = (
    "commerce_connections",
    "commerce_service_credentials",
    "commerce_member_references",
    "commerce_identity_handoffs",
    "commerce_integration_messages",
    "commerce_connection_events",
    "commerce_product_mappings",
    "commerce_order_facts",
    "commerce_order_line_facts",
    "commerce_refund_facts",
    "commerce_repurchase_attributions",
    "member_notification_preferences",
    "member_channel_grants",
    "member_notifications",
    "member_notification_deliveries",
)
BACKEND_DIR = Path(__file__).resolve().parents[2]


async def _runtime_factory(migrated_pg_url: str):
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    engine = create_async_engine(runtime_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def test_commerce_boundary_derives_tenant_rejects_replay_and_preserves_facts(
    migrated_pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "shared_wechat_miniprogram_appid", "wx-acceptance-appid")
    monkeypatch.setattr(settings, "wechat_subscription_template_ids", {"coupon_expiry": "wx-template-acceptance"})
    summary = await seed_baseline(migrated_pg_url)
    tenant_a = uuid.UUID(str(summary["baseline_tenant"]["id"]))
    tenant_b = uuid.UUID(str(summary["control_tenant"]["id"]))
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    try:
        actor_a = await owner.fetchval("SELECT id FROM accounts WHERE tenant_id=$1 ORDER BY id LIMIT 1", tenant_a)
        actor_b = await owner.fetchval("SELECT id FROM accounts WHERE tenant_id=$1 ORDER BY id LIMIT 1", tenant_b)
        auth_session_a = uuid.uuid4()
        auth_session_b = uuid.uuid4()
        for auth_session_id, tenant_id, actor_id in (
            (auth_session_a, tenant_a, actor_a),
            (auth_session_b, tenant_b, actor_b),
        ):
            await owner.execute(
                "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at) "
                "SELECT $1,$2,$3,auth_version,$4,statement_timestamp()+interval '1 hour' "
                "FROM accounts WHERE tenant_id=$2 AND id=$3",
                auth_session_id,
                tenant_id,
                actor_id,
                f"commerce-{auth_session_id.hex}",
            )
        unprivileged_actor = uuid.uuid4()
        unprivileged_session = uuid.uuid4()
        organization_a = await owner.fetchval(
            "SELECT id FROM organizations WHERE tenant_id=$1 ORDER BY id LIMIT 1", tenant_a
        )
        await owner.execute(
            "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,is_active,auth_version,"
            "must_change_password,failed_login_attempts) "
            "VALUES($1,$2,$3,$4,$5,'No Commerce Permission',true,0,false,0)",
            unprivileged_actor,
            tenant_a,
            organization_a,
            f"no-commerce-{unprivileged_actor.hex[:8]}@example.test",
            "not-used-in-acceptance",
        )
        await owner.execute(
            "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at) "
            "VALUES($1,$2,$3,0,$4,statement_timestamp()+interval '1 hour')",
            unprivileged_session,
            tenant_a,
            unprivileged_actor,
            f"commerce-{unprivileged_session.hex}",
        )
        facts = await _seed_join_facts(owner, tenant_a, f"COMMERCE-{uuid.uuid4().hex[:8]}")
        product_id = await owner.fetchval(
            "SELECT batch.product_id FROM code_items item JOIN code_batches batch "
            "ON batch.tenant_id=item.tenant_id AND batch.id=item.code_batch_id "
            "WHERE item.tenant_id=$1 AND item.public_id=$2",
            tenant_a,
            facts["public_id"],
        )
        await owner.execute(
            "INSERT INTO anonymous_visitors(id,tenant_id,visitor_id,consumer_id) VALUES($1,$2,$3,$4)",
            uuid.uuid4(),
            tenant_a,
            facts["visitor_id"],
            facts["consumer_id"],
        )
        await owner.execute(
            "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,is_first_scan,is_valid_visit,visitor_id) "
            "VALUES($1,$2,$3,$4,false,true,$5)",
            facts["scan_event_id"],
            tenant_a,
            facts["public_id"],
            facts["scan_time"],
            facts["visitor_id"],
        )
        foreign_consumer_id = uuid.uuid4()
        foreign_scan_event_id = uuid.uuid4()
        foreign_visitor_id = f"foreign-{uuid.uuid4().hex[:20]}"
        await owner.execute(
            "INSERT INTO consumer_profiles(id,tenant_id,total_points,member_level) VALUES($1,$2,0,'bronze')",
            foreign_consumer_id,
            tenant_a,
        )
        await owner.execute(
            "INSERT INTO anonymous_visitors(id,tenant_id,visitor_id,consumer_id) VALUES($1,$2,$3,$4)",
            uuid.uuid4(),
            tenant_a,
            foreign_visitor_id,
            foreign_consumer_id,
        )
        await owner.execute(
            "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,is_first_scan,is_valid_visit,visitor_id) "
            "VALUES($1,$2,$3,statement_timestamp(),false,true,$4)",
            foreign_scan_event_id,
            tenant_a,
            facts["public_id"],
            foreign_visitor_id,
        )
        for table in TABLES:
            security = await owner.fetchrow(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                "WHERE relnamespace='public'::regnamespace AND relname=$1",
                table,
            )
            assert security and security["relrowsecurity"] and security["relforcerowsecurity"]
            if table in {"commerce_service_credentials", "commerce_identity_handoffs"}:
                assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,'SELECT')", table)
            else:
                assert await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,'SELECT')", table)
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,$2)", table, privilege)
        for function in (
            "mutate_commerce_connection_authority(uuid,jsonb)",
            "mutate_commerce_handoff_authority(uuid,jsonb)",
            "accept_commerce_message_authority(uuid,jsonb)",
        ):
            expected_principal = (
                "yimatong_app"
                if function == "mutate_commerce_connection_authority(uuid,jsonb)"
                else "yimatong_callback"
            )
            assert await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", expected_principal, function)
        assert not await owner.fetchval(
            "SELECT has_function_privilege('yimatong_callback',"
            "'ensure_commerce_member_reference_authority(uuid,jsonb)','EXECUTE')"
        )
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_callback',"
            "'project_commerce_order_event_authority(uuid,uuid)','EXECUTE')"
        )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',"
            "'project_commerce_order_event_authority(uuid,uuid)','EXECUTE')"
        )
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',"
            "'create_commerce_product_mapping_authority(uuid,jsonb)','EXECUTE')"
        )
        for function in (
            "mutate_member_notification_preference_authority(uuid,jsonb)",
            "create_member_marketing_notification_authority(uuid,jsonb)",
            "record_coupon_member_notification_authority(uuid,uuid)",
        ):
            assert await owner.fetchval("SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", function)
        assert not await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',"
            "'mutate_member_notification_delivery_authority(uuid,jsonb)','EXECUTE')"
        )
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_callback',"
            "'mutate_member_notification_delivery_authority(uuid,jsonb)','EXECUTE')"
        )
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',"
            "'lease_member_notification_delivery_authority(uuid,uuid,uuid)','EXECUTE')"
        )
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_callback',"
            "'record_commerce_member_notification_authority(uuid,uuid)','EXECUTE')"
        )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',"
            "'record_commerce_member_notification_authority(uuid,uuid)','EXECUTE')"
        )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('yimatong_callback',"
            "'create_commerce_product_mapping_authority(uuid,jsonb)','EXECUTE')"
        )
        assert not await owner.fetchval(
            "SELECT has_column_privilege('yimatong_app','commerce_service_credentials','secret_ciphertext','SELECT')"
        )
        assert not await owner.fetchval(
            "SELECT has_column_privilege('yimatong_app','commerce_identity_handoffs','token_digest','SELECT')"
        )
    finally:
        await owner.close()

    engine, factory = await _runtime_factory(migrated_pg_url)
    control_engine = create_async_engine(migrated_pg_url)
    control_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(commerce_service, "control_session_factory", control_factory)
    callback_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_callback:yimatong_callback@")
    callback_engine = create_async_engine(callback_url)
    callback_factory = async_sessionmaker(callback_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(commerce_service, "callback_session_factory", callback_factory)
    monkeypatch.setattr(notification_service, "callback_session_factory", callback_factory)
    try:
        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_a)})
            membership_id = uuid.uuid4()
            await db.execute(
                text(
                    "SELECT * FROM create_brand_membership_authority("
                    ":tenant_id,:consent_id,:scan_event_id,:scan_time,:public_id,:visitor_id,:consumer_id,true,"
                    ":membership_id,:membership_number,:link_id,:event_id,:idempotency_key,:payload_hash)"
                ),
                {
                    "tenant_id": tenant_a,
                    "consent_id": facts["consent_id"],
                    "scan_event_id": facts["scan_event_id"],
                    "scan_time": facts["scan_time"],
                    "public_id": facts["public_id"],
                    "visitor_id": facts["visitor_id"],
                    "consumer_id": facts["consumer_id"],
                    "membership_id": membership_id,
                    "membership_number": f"MBR-{membership_id.hex[:12].upper()}",
                    "link_id": uuid.uuid4(),
                    "event_id": uuid.uuid4(),
                    "idempotency_key": "commerce-membership-join",
                    "payload_hash": "c" * 64,
                },
            )
            connection_a, secrets_a = await create_commerce_connection(
                db,
                tenant_id=tenant_a,
                external_tenant_ref="commerce-tenant-a",
                external_shop_ref="shop-a",
                base_url="https://commerce-a.example.test/api/",
                capabilities=["identity_handoff", "order_events", "coupon_lifecycle", "reconciliation"],
                idempotency_key="commerce-connect-a",
                actor_id=actor_a,
                auth_session_id=auth_session_a,
            )
            mapping = await create_commerce_product_mapping(
                db,
                tenant_id=tenant_a,
                connection_id=connection_a.id,
                external_product_ref="MEDUSA-PRODUCT-A",
                product_id=product_id,
                actor_id=actor_a,
                auth_session_id=auth_session_a,
            )
            assert mapping.product_id == product_id
            assert len(secrets_a) == 2
            assert all(
                89.99 <= (item["valid_until"] - item["valid_from"]).total_seconds() / 86400 <= 90 for item in secrets_a
            )
            incoming_a = next(item for item in secrets_a if item["direction"] == "commerce_to_yimatong")
            rotated = await rotate_commerce_credential(
                db,
                tenant_id=tenant_a,
                connection_id=connection_a.id,
                direction="commerce_to_yimatong",
                idempotency_key="commerce-rotate-a",
                actor_id=actor_a,
                auth_session_id=auth_session_a,
            )
            assert rotated["version"] == 2
            overlap_seconds = await db.scalar(
                text(
                    "SELECT extract(epoch FROM (overlap_until-statement_timestamp())) "
                    "FROM commerce_service_credentials WHERE id=:id"
                ),
                {"id": incoming_a["id"]},
            )
            assert 86340 < float(overlap_seconds) <= 86400

        # The consumer handoff endpoint only operates on an already committed
        # membership/connection. Commit those facts before crossing into the
        # independently authenticated callback role that owns handoff writes.
        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_a)})
            token, _ = await issue_commerce_handoff(
                db,
                tenant_id=tenant_a,
                connection_id=connection_a.id,
                membership_id=membership_id,
                idempotency_key="commerce-handoff-a",
                consumer_id=facts["consumer_id"],
                scan_event_id=facts["scan_event_id"],
                public_id=facts["public_id"],
            )
        token_payload = decode_commerce_handoff(token)
        assert set(token_payload).isdisjoint({"phone", "openid", "name", "address", "membership_id"})

        resolved_old, resolved_connection = await resolve_commerce_credential(incoming_a["id"])
        assert resolved_old.tenant_id == tenant_a
        assert resolved_connection.id == connection_a.id
        resolved_new, _ = await resolve_commerce_credential(rotated["id"])
        assert resolved_new.version == 2
        with pytest.raises(HTTPException) as unknown_credential:
            await resolve_commerce_credential(uuid.uuid4())
        assert unknown_credential.value.detail == "invalid_commerce_credential"

        async with factory() as db, db.begin():
            redeemed_connection, member_ref = await redeem_commerce_handoff(db, token=token, payload=token_payload)
            assert redeemed_connection.id == connection_a.id
            assert member_ref == token_payload["member_ref"]

        with pytest.raises(HTTPException) as replay:
            async with factory() as db, db.begin():
                await redeem_commerce_handoff(db, token=token, payload=token_payload)
        assert replay.value.detail in {"commerce_handoff_already_consumed", "commerce_handoff_already_redeemed"}

        original_app_key = settings.secret_key
        monkeypatch.setattr(settings, "secret_key", original_app_key + "-rotated")
        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_a)})
            rotated_key_token, _ = await issue_commerce_handoff(
                db,
                tenant_id=tenant_a,
                connection_id=connection_a.id,
                membership_id=membership_id,
                idempotency_key="commerce-handoff-after-app-key-rotation",
                consumer_id=facts["consumer_id"],
                scan_event_id=facts["scan_event_id"],
                public_id=facts["public_id"],
            )
        rotated_key_payload = decode_commerce_handoff(rotated_key_token)
        assert rotated_key_payload["member_ref"] == member_ref
        async with factory() as db, db.begin():
            redeemed_connection, rotated_key_member_ref = await redeem_commerce_handoff(
                db, token=rotated_key_token, payload=rotated_key_payload
            )
            assert redeemed_connection.id == connection_a.id
            assert rotated_key_member_ref == member_ref
            assert (
                await db.scalar(
                    text(
                        "SELECT payload->>'member_ref' FROM commerce_integration_messages WHERE message_id=:message_id"
                    ),
                    {"message_id": f"identity-handoff:{rotated_key_payload['handoff_id']}"},
                )
                == member_ref
            )
        monkeypatch.setattr(settings, "secret_key", original_app_key)

        owner = await asyncpg.connect(owner_dsn)
        try:
            coupon_rule_id = uuid.uuid4()
            coupon_id = uuid.uuid4()
            await owner.execute(
                "INSERT INTO repurchase_coupon_rule_versions(id,tenant_id,rule_key,version,name,amount_minor,"
                "minimum_spend_minor,currency,product_scope,eligible_product_refs,channel_scope,validity_mode,"
                "valid_days,issuance_limit,issued_count,status,published_at) "
                "VALUES($1,$2,'acceptance-attribution',1,'复购归因券',100,500,'CNY','all','[]','online',"
                "'relative',30,100,1,'published',statement_timestamp())",
                coupon_rule_id,
                tenant_a,
            )
            await owner.execute(
                "INSERT INTO member_coupons(id,tenant_id,membership_id,rule_version_id,coupon_number,status,"
                "valid_from,valid_until,used_order_ref,used_at,authority_type,sync_status,version) "
                "VALUES($1,$2,$3,$4,$5,'used',statement_timestamp()-interval '1 day',"
                "statement_timestamp()+interval '29 days','CHECKOUT-A',statement_timestamp(),"
                "'yimatong','not_required',1)",
                coupon_id,
                tenant_a,
                membership_id,
                coupon_rule_id,
                f"RCP-{coupon_id.hex[:16].upper()}",
            )
            await owner.execute(
                "INSERT INTO repurchase_coupon_events(id,tenant_id,rule_version_id,coupon_id,event_type,"
                "from_status,to_status,idempotency_key,payload_digest,order_ref,actor_type,details) "
                "VALUES($1,$2,$3,$4,'committed','reserved','used',$5,$6,'CHECKOUT-A','service','{}'::json)",
                uuid.uuid4(),
                tenant_a,
                coupon_rule_id,
                coupon_id,
                f"acceptance-commit-{coupon_id}",
                "d" * 64,
            )
            marketing_policy = await owner.fetchrow(
                "SELECT policy.id,policy.policy_version,policy.policy_digest FROM consumer_consent_policy_current current "
                "JOIN consumer_consent_policies policy ON policy.tenant_id=current.tenant_id "
                "AND policy.id=current.policy_id WHERE current.tenant_id=$1 AND policy.consent_type='marketing'",
                tenant_a,
            )
            marketing_consent_id = uuid.uuid4()
            await owner.execute(
                "INSERT INTO consent_records(id,tenant_id,consumer_id,consent_type,status,purpose,authority_version,"
                "policy_id,policy_version,policy_digest,visitor_subject_hash,public_id,scan_event_id,scan_event_time,"
                "idempotency_key) VALUES($1,$2,$3,'marketing','granted','lead_capture',1,$4,$5,$6,$7,$8,$9,$10,$11)",
                marketing_consent_id,
                tenant_a,
                facts["consumer_id"],
                marketing_policy["id"],
                marketing_policy["policy_version"],
                marketing_policy["policy_digest"],
                "m" * 64,
                facts["public_id"],
                facts["scan_event_id"],
                facts["scan_time"],
                "notification-marketing-consent",
            )
        finally:
            await owner.close()

        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_a)})
            connection = await db.get(type(connection_a), connection_a.id)
            credential = resolved_old
            assert connection is not None
            body = b'{"event_id":"ORDER-A","event_version":2}'
            timestamp = int(time.time())
            signature = sign_commerce_request(incoming_a["secret"], timestamp, "/api/v1/commerce/events", body)
            verify_commerce_signature(
                credential=credential,
                timestamp=str(timestamp),
                path="/api/v1/commerce/events",
                body=body,
                signature=signature,
            )
            before_failures = await db.scalar(select(func.count()).select_from(CommerceIntegrationMessage))
            for invalid_timestamp, invalid_signature in (
                (str(timestamp), "0" * 64),
                (str(timestamp - 301), signature),
            ):
                with pytest.raises(HTTPException):
                    verify_commerce_signature(
                        credential=credential,
                        timestamp=invalid_timestamp,
                        path="/api/v1/commerce/events",
                        body=body,
                        signature=invalid_signature,
                    )
            assert await db.scalar(select(func.count()).select_from(CommerceIntegrationMessage)) == before_failures
            event_v2 = {
                "event_id": "ORDER-A",
                "event_version": 2,
                "event_type": "commerce.order.paid",
                "occurred_at": utcnow(),
                "member_ref": member_ref,
                "data": {
                    "order_ref": "ORDER-A",
                    "source_system": "medusa_v2",
                    "status": "paid",
                    "currency": "CNY",
                    "order_original_amount_fen": 1200,
                    "order_refunded_amount_fen": 0,
                    "product_original_amount_fen": 1000,
                    "product_refunded_amount_fen": 0,
                    "coverage_status": "complete",
                    "paid_at": utcnow(),
                    "fulfilled_at": None,
                    "completed_at": None,
                    "cancelled_at": None,
                    "coupon_ref": coupon_id,
                    "coupon_order_ref": "CHECKOUT-A",
                    "line_items": [
                        {
                            "line_ref": "LINE-A",
                            "product_ref": "MEDUSA-PRODUCT-A",
                            "sku_ref": "MEDUSA-SKU-A",
                            "quantity": 1,
                            "original_amount_fen": 1000,
                            "refunded_amount_fen": 0,
                        }
                    ],
                    "refunds": [],
                },
            }
            digest_v2 = hashlib.sha256(body).hexdigest()
            accepted, replayed = await accept_commerce_event(
                db, credential=credential, connection=connection, event=event_v2, body_digest=digest_v2
            )
            same, replayed = await accept_commerce_event(
                db, credential=credential, connection=connection, event=event_v2, body_digest=digest_v2
            )
            assert replayed and same.id == accepted.id
            event_v1 = {
                **event_v2,
                "event_version": 1,
                "event_type": "commerce.order.placed",
                "occurred_at": event_v2["occurred_at"] - timedelta(seconds=1),
                "data": {**event_v2["data"], "status": "placed", "paid_at": None},
            }
            older, replayed = await accept_commerce_event(
                db,
                credential=credential,
                connection=connection,
                event=event_v1,
                body_digest=hashlib.sha256(b"ORDER-A-v1").hexdigest(),
            )
            assert not replayed and older.message_version == 1
            projected = (
                await db.execute(
                    text(
                        "SELECT fact.status,fact.product_net_amount_fen,attr.is_packaging_repurchase,"
                        "attr.entry_attributed,attr.coverage_status,attr.trust_level "
                        "FROM commerce_order_facts fact JOIN commerce_repurchase_attributions attr "
                        "ON attr.tenant_id=fact.tenant_id AND attr.order_fact_id=fact.id "
                        "WHERE fact.external_order_ref='ORDER-A'"
                    )
                )
            ).one()
            assert projected == ("paid", 1000, True, True, "complete", "authoritative")
            assert (
                await db.scalar(
                    text("SELECT entry_attributed AND coupon_attributed FROM commerce_repurchase_attributions")
                )
                is True
            )
            duplicate_coupon_order = {
                **event_v2,
                "event_id": "ORDER-B",
                "occurred_at": event_v2["occurred_at"] + timedelta(milliseconds=500),
                "data": {**event_v2["data"], "order_ref": "ORDER-B"},
            }
            with pytest.raises(IntegrityError, match="uq_commerce_repurchase_attributions_coupon"):
                async with db.begin_nested():
                    await accept_commerce_event(
                        db,
                        credential=credential,
                        connection=connection,
                        event=duplicate_coupon_order,
                        body_digest=hashlib.sha256(b"ORDER-B").hexdigest(),
                    )

            partial_refund = {
                **event_v2,
                "event_id": "ORDER-A-REFUND-1",
                "event_version": 3,
                "event_type": "commerce.order.refunded",
                "occurred_at": event_v2["occurred_at"] + timedelta(seconds=1),
                "data": {
                    **event_v2["data"],
                    "status": "partially_refunded",
                    "order_refunded_amount_fen": 300,
                    "product_refunded_amount_fen": 300,
                    "line_items": [{**event_v2["data"]["line_items"][0], "refunded_amount_fen": 300}],
                    "refunds": [
                        {
                            "refund_ref": "REFUND-A-1",
                            "order_amount_fen": 300,
                            "product_amount_fen": 300,
                            "occurred_at": event_v2["occurred_at"] + timedelta(seconds=1),
                            "line_refunds": [{"line_ref": "LINE-A", "amount_fen": 300}],
                        }
                    ],
                },
            }
            partial_coupon = await transition_commerce_coupon(
                db,
                credential=credential,
                connection=connection,
                member_ref=member_ref,
                coupon_ref=coupon_id,
                order_id="CHECKOUT-A",
                amount_fen=100,
                action="reverse",
                idempotency_key="acceptance-partial-refund",
                goods_subtotal_fen=None,
                line_items=[],
                full_refund=False,
            )
            assert partial_coupon.status == "used"
            await accept_commerce_event(
                db,
                credential=credential,
                connection=connection,
                event=partial_refund,
                body_digest=hashlib.sha256(b"ORDER-A-refund-1").hexdigest(),
            )
            assert await db.scalar(text("SELECT net_product_sales_fen FROM commerce_repurchase_attributions")) == 700
            full_refund = {
                **partial_refund,
                "event_id": "ORDER-A-REFUND-2",
                "event_version": 4,
                "occurred_at": event_v2["occurred_at"] + timedelta(seconds=2),
                "data": {
                    **partial_refund["data"],
                    "status": "refunded",
                    "order_refunded_amount_fen": 1200,
                    "product_refunded_amount_fen": 1000,
                    "line_items": [{**event_v2["data"]["line_items"][0], "refunded_amount_fen": 1000}],
                    "refunds": [
                        *partial_refund["data"]["refunds"],
                        {
                            "refund_ref": "REFUND-A-2",
                            "order_amount_fen": 900,
                            "product_amount_fen": 700,
                            "occurred_at": event_v2["occurred_at"] + timedelta(seconds=2),
                            "line_refunds": [{"line_ref": "LINE-A", "amount_fen": 700}],
                        },
                    ],
                },
            }
            restored_coupon = await transition_commerce_coupon(
                db,
                credential=credential,
                connection=connection,
                member_ref=member_ref,
                coupon_ref=coupon_id,
                order_id="CHECKOUT-A",
                amount_fen=100,
                action="reverse",
                idempotency_key="acceptance-full-refund",
                goods_subtotal_fen=None,
                line_items=[],
                full_refund=True,
            )
            assert restored_coupon.status == "available"
            await accept_commerce_event(
                db,
                credential=credential,
                connection=connection,
                event=full_refund,
                body_digest=hashlib.sha256(b"ORDER-A-refund-2").hexdigest(),
            )
            fully_refunded = (
                await db.execute(
                    text(
                        "SELECT fact.status,fact.product_net_amount_fen,attr.is_member_order,"
                        "attr.is_packaging_repurchase,attr.net_product_sales_fen,"
                        "(SELECT count(*) FROM commerce_refund_facts) "
                        "FROM commerce_order_facts fact JOIN commerce_repurchase_attributions attr "
                        "ON attr.tenant_id=fact.tenant_id AND attr.order_fact_id=fact.id"
                    )
                )
            ).one()
            assert fully_refunded == ("refunded", 0, False, False, 0, 2)
            items, total = await list_commerce_order_facts(db, tenant_a)
            assert total == 1
            assert items[0]["external_order_ref"] == "ORDER-A"
            assert items[0]["net_product_sales_fen"] == 0
            assert await commerce_repurchase_metrics(db, tenant_a) == {
                "member_orders": 0,
                "packaging_repurchase_orders": 0,
                "repurchase_members": 0,
                "net_product_sales_fen": 0,
                "entry_attributed_orders": 0,
                "coupon_attributed_orders": 0,
                "complete_coverage_orders": 1,
                "partial_or_missing_orders": 0,
            }
            service_message_count = await db.scalar(text("SELECT count(*) FROM member_notifications"))
            assert service_message_count == 3
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM member_notification_deliveries WHERE status='authorization_missing'")
                )
                == 3
            )
            await bind_verified_member_identity(
                db,
                tenant_id=tenant_a,
                membership_id=membership_id,
                credential_type="wechat_openid",
                issuer="wx-acceptance-appid",
                subject=f"openid-{membership_id}",
                verification_receipt_hash="9" * 64,
                idempotency_key=f"notification-wechat-bind-{membership_id}",
            )
            preference = await update_member_notification_preference(
                db,
                tenant_id=tenant_a,
                membership_id=membership_id,
                consumer_id=facts["consumer_id"],
                action="subscribe_marketing",
                marketing_consent_id=marketing_consent_id,
                template_code="coupon_expiry",
            )
            assert preference.marketing_enabled is True
            marketing_base = {
                "membership_id": membership_id,
                "notification_type": "coupon_expiry",
                "source_product": "yimatong",
                "source_event_version": 1,
                "object_ref": str(coupon_id),
                "title": "复购券即将到期",
                "body": "您领取的复购券即将到期。",
                "action_path": "/member/coupons",
                "facts": {"coupon_id": str(coupon_id)},
                "template_code": "coupon_expiry",
                "template_version": "v1",
            }
            first_marketing = await create_marketing_notification(
                db,
                tenant_id=tenant_a,
                source_event_id="coupon-expiry-1",
                occurred_at=utcnow(),
                **marketing_base,
            )
            assert first_marketing is not None
            delivery_id = await db.scalar(
                text("SELECT id FROM member_notification_deliveries WHERE notification_id=:id"),
                {"id": first_marketing.id},
            )
            for index, offset in enumerate((1, 24 * 60, 2 * 24 * 60), start=2):
                assert (
                    await create_marketing_notification(
                        db,
                        tenant_id=tenant_a,
                        source_event_id=f"coupon-expiry-{index}",
                        occurred_at=utcnow() + timedelta(minutes=offset),
                        **marketing_base,
                    )
                    is not None
                )
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM member_notification_deliveries "
                        "WHERE suppression_reason='marketing_frequency_limited'"
                    )
                )
                == 3
            )
            assert await db.scalar(text("SELECT count(*) FROM commerce_order_facts")) == 1
            before_cross_ref = await db.scalar(select(func.count()).select_from(CommerceIntegrationMessage))
            with pytest.raises(HTTPException) as cross_ref:
                await accept_commerce_event(
                    db,
                    credential=credential,
                    connection=connection,
                    event={**event_v2, "event_id": "ORDER-FOREIGN", "member_ref": "cmr_foreign"},
                    body_digest=hashlib.sha256(b"foreign").hexdigest(),
                )
            assert cross_ref.value.status_code == 403
            assert await db.scalar(select(func.count()).select_from(CommerceIntegrationMessage)) == before_cross_ref

        lease_token = uuid.uuid4()
        async with factory() as db, db.begin():
            assert await lease_delivery(
                db,
                tenant_id=tenant_a,
                delivery_id=delivery_id,
                lease_token=lease_token,
            )
        with pytest.raises(HTTPException, match="notification delivery evidence invalid"):
            async with factory() as db, db.begin():
                await record_delivery_result(
                    db,
                    tenant_id=tenant_a,
                    delivery_id=delivery_id,
                    lease_token=lease_token,
                    result="accepted",
                    channel_message_ref=None,
                    error_code=None,
                )
        async with factory() as db, db.begin():
            lease_frequency = await create_marketing_notification(
                db,
                tenant_id=tenant_a,
                source_event_id="coupon-expiry-during-lease",
                occurred_at=utcnow(),
                **marketing_base,
            )
            assert lease_frequency is not None
            assert (
                await db.scalar(
                    text(
                        "SELECT suppression_reason FROM member_notification_deliveries "
                        "WHERE notification_id=:notification_id"
                    ),
                    {"notification_id": lease_frequency.id},
                )
                == "marketing_frequency_limited"
            )
            preference = await update_member_notification_preference(
                db,
                tenant_id=tenant_a,
                membership_id=membership_id,
                consumer_id=facts["consumer_id"],
                action="unsubscribe_marketing",
            )
            assert preference.marketing_enabled is False
        owner = await asyncpg.connect(owner_dsn)
        try:
            opted_out = await owner.fetchrow(
                "SELECT status,lease_token,attempt_count FROM member_notification_deliveries WHERE id=$1",
                delivery_id,
            )
            assert opted_out == ("suppressed", None, 0)
        finally:
            await owner.close()
        with pytest.raises(HTTPException, match="notification delivery not attemptable"):
            async with factory() as db, db.begin():
                await record_delivery_result(
                    db,
                    tenant_id=tenant_a,
                    delivery_id=delivery_id,
                    lease_token=lease_token,
                    result="transient_failure",
                    channel_message_ref=None,
                    error_code="late-callback",
                )
        async with factory() as db, db.begin():
            await update_member_notification_preference(
                db,
                tenant_id=tenant_a,
                membership_id=membership_id,
                consumer_id=facts["consumer_id"],
                action="subscribe_marketing",
                marketing_consent_id=marketing_consent_id,
                template_code="coupon_expiry",
            )
            retry_notification = await create_marketing_notification(
                db,
                tenant_id=tenant_a,
                source_event_id="coupon-expiry-retry-proof",
                occurred_at=utcnow(),
                **marketing_base,
            )
            assert retry_notification is not None
            delivery_id = await db.scalar(
                text("SELECT id FROM member_notification_deliveries WHERE notification_id=:id"),
                {"id": retry_notification.id},
            )

        for attempt in range(3):
            lease_token = uuid.uuid4()
            async with factory() as db, db.begin():
                assert await lease_delivery(
                    db,
                    tenant_id=tenant_a,
                    delivery_id=delivery_id,
                    lease_token=lease_token,
                )
            async with factory() as db, db.begin():
                delivery = await record_delivery_result(
                    db,
                    tenant_id=tenant_a,
                    delivery_id=delivery_id,
                    lease_token=lease_token,
                    result="transient_failure",
                    channel_message_ref=None,
                    error_code=f"temporary-{attempt}",
                )
            if attempt < 2:
                owner = await asyncpg.connect(owner_dsn)
                try:
                    await owner.execute(
                        "UPDATE member_notification_deliveries SET next_attempt_at=statement_timestamp() "
                        "WHERE tenant_id=$1 AND id=$2",
                        tenant_a,
                        delivery_id,
                    )
                finally:
                    await owner.close()
        assert delivery.status == "exhausted" and delivery.attempt_count == 3

        owner = await asyncpg.connect(owner_dsn)
        try:
            await owner.execute(
                "UPDATE member_notification_deliveries SET created_at=statement_timestamp()-interval '8 days' "
                "WHERE tenant_id=$1 AND id=$2",
                tenant_a,
                delivery_id,
            )
        finally:
            await owner.close()
        async with factory() as db, db.begin():
            await update_member_notification_preference(
                db,
                tenant_id=tenant_a,
                membership_id=membership_id,
                consumer_id=facts["consumer_id"],
                action="subscribe_marketing",
                marketing_consent_id=marketing_consent_id,
                template_code="coupon_expiry",
            )
            withdrawal_notification = await create_marketing_notification(
                db,
                tenant_id=tenant_a,
                source_event_id="coupon-expiry-consent-withdrawal",
                occurred_at=utcnow(),
                **marketing_base,
            )
            assert withdrawal_notification is not None
            withdrawn_delivery_id = await db.scalar(
                text("SELECT id FROM member_notification_deliveries WHERE notification_id=:id"),
                {"id": withdrawal_notification.id},
            )
        withdrawn_lease = uuid.uuid4()
        async with factory() as db, db.begin():
            assert await lease_delivery(
                db,
                tenant_id=tenant_a,
                delivery_id=withdrawn_delivery_id,
                lease_token=withdrawn_lease,
            )
        owner = await asyncpg.connect(owner_dsn)
        try:
            await owner.execute(
                "UPDATE consent_records SET status='withdrawn',withdrawn_at=statement_timestamp() "
                "WHERE tenant_id=$1 AND id=$2",
                tenant_a,
                marketing_consent_id,
            )
            withdrawn = await owner.fetchrow(
                "SELECT status,lease_token,attempt_count FROM member_notification_deliveries WHERE id=$1",
                withdrawn_delivery_id,
            )
            assert withdrawn == ("suppressed", None, 0)
        finally:
            await owner.close()
        with pytest.raises(HTTPException, match="notification delivery not attemptable"):
            async with factory() as db, db.begin():
                await record_delivery_result(
                    db,
                    tenant_id=tenant_a,
                    delivery_id=withdrawn_delivery_id,
                    lease_token=withdrawn_lease,
                    result="transient_failure",
                    channel_message_ref=None,
                    error_code="late-consent-callback",
                )

        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_b)})
            connection_b, _ = await create_commerce_connection(
                db,
                tenant_id=tenant_b,
                external_tenant_ref="commerce-tenant-b",
                external_shop_ref="shop-b",
                base_url="https://commerce-b.example.test/api",
                capabilities=["order_events"],
                idempotency_key="commerce-connect-b",
                actor_id=actor_b,
                auth_session_id=auth_session_b,
            )
            assert connection_b.tenant_id == tenant_b
            for table in TABLES:
                count = await db.scalar(text(f"SELECT count(*) FROM {table}"))
                assert count == (
                    1
                    if table in {"commerce_connections", "commerce_connection_events"}
                    else 2
                    if table == "commerce_service_credentials"
                    else 0
                )

        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_a)})
            await revoke_commerce_credential(
                db,
                tenant_id=tenant_a,
                connection_id=connection_a.id,
                credential_id=rotated["id"],
                idempotency_key="commerce-revoke-a",
                actor_id=actor_a,
                auth_session_id=auth_session_a,
            )
            assert await db.scalar(select(func.count()).select_from(CommerceIdentityHandoff)) == 2
            assert await db.scalar(select(func.count()).select_from(CommerceMemberReference)) == 1

        with pytest.raises(HTTPException) as revoked:
            await resolve_commerce_credential(rotated["id"])
        assert revoked.value.detail == "expired_or_revoked_commerce_credential"

        owner = await asyncpg.connect(owner_dsn)
        try:
            expired_id = uuid.uuid4()
            await owner.execute(
                "INSERT INTO commerce_service_credentials("
                "id,tenant_id,connection_id,direction,version,key_prefix,secret_ciphertext,valid_from,valid_until) "
                "VALUES($1,$2,$3,'commerce_to_yimatong',3,$4,$5,"
                "statement_timestamp()-interval '2 days',statement_timestamp()-interval '1 day')",
                expired_id,
                tenant_a,
                connection_a.id,
                f"expired-{expired_id.hex[:8]}",
                b"expired-test-ciphertext",
            )
        finally:
            await owner.close()
        with pytest.raises(HTTPException) as expired:
            await resolve_commerce_credential(expired_id)
        assert expired.value.detail == "expired_or_revoked_commerce_credential"
    finally:
        await engine.dispose()
        await control_engine.dispose()
        await callback_engine.dispose()

    runtime = await asyncpg.connect(owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    try:
        for secret_query in (
            "SELECT secret_ciphertext FROM commerce_service_credentials LIMIT 1",
            "SELECT token_digest FROM commerce_identity_handoffs LIMIT 1",
        ):
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await runtime.fetchval(secret_query)

        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime.fetchval(
                    "SELECT accept_commerce_message_authority($1,$2::jsonb)",
                    tenant_a,
                    json.dumps({"connection_id": str(connection_a.id)}),
                )

        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
            member_reference_id = await runtime.fetchval(
                "SELECT id FROM commerce_member_references WHERE connection_id=$1 AND member_ref=$2",
                connection_a.id,
                member_ref,
            )
            before_foreign_scan = {
                table: await runtime.fetchval(f"SELECT count(id) FROM {table}")
                for table in (
                    "commerce_member_references",
                    "commerce_identity_handoffs",
                    "commerce_integration_messages",
                    "commerce_connection_events",
                )
            }

        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime.fetchval(
                    "SELECT mutate_commerce_handoff_authority($1,$2::jsonb)",
                    tenant_a,
                    json.dumps(
                        {
                            "action": "issue",
                            "handoff_id": str(uuid.uuid4()),
                            "connection_id": str(connection_a.id),
                            "member_reference_id": str(member_reference_id),
                            "membership_id": str(membership_id),
                            "consumer_id": str(facts["consumer_id"]),
                            "scan_event_id": str(foreign_scan_event_id),
                            "public_id": facts["public_id"],
                            "member_ref": member_ref,
                            "token_digest": "5" * 64,
                            "expires_at": (utcnow() + timedelta(minutes=5)).isoformat(),
                            "outbox_id": str(uuid.uuid4()),
                            "event_id": str(uuid.uuid4()),
                            "idempotency_key": "direct-issue-foreign-scan",
                            "payload_digest": "6" * 64,
                        }
                    ),
                )
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
            assert {
                table: await runtime.fetchval(f"SELECT count(id) FROM {table}") for table in before_foreign_scan
            } == before_foreign_scan

        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
            with pytest.raises(asyncpg.InsufficientPrivilegeError, match="commerce connection authority denied"):
                await runtime.fetchval(
                    "SELECT mutate_commerce_connection_authority($1,$2::jsonb)",
                    tenant_a,
                    json.dumps(
                        {
                            "action": "disconnect",
                            "connection_id": str(connection_a.id),
                            "actor_id": str(actor_a),
                            "event_id": str(uuid.uuid4()),
                            "idempotency_key": "direct-no-session",
                            "payload_digest": "d" * 64,
                        }
                    ),
                )

        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
            with pytest.raises(asyncpg.InsufficientPrivilegeError, match="session or permission denied"):
                await runtime.fetchval(
                    "SELECT mutate_commerce_connection_authority($1,$2::jsonb)",
                    tenant_a,
                    json.dumps(
                        {
                            "action": "disconnect",
                            "connection_id": str(connection_a.id),
                            "actor_id": str(unprivileged_actor),
                            "auth_session_id": str(unprivileged_session),
                            "event_id": str(uuid.uuid4()),
                            "idempotency_key": "direct-unprivileged-session",
                            "payload_digest": "1" * 64,
                        }
                    ),
                )

        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
            member_reference_id = await runtime.fetchval(
                "SELECT id FROM commerce_member_references WHERE connection_id=$1 AND member_ref=$2",
                connection_a.id,
                member_ref,
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime.fetchval(
                    "SELECT mutate_commerce_handoff_authority($1,$2::jsonb)",
                    tenant_a,
                    json.dumps(
                        {
                            "action": "issue",
                            "handoff_id": str(uuid.uuid4()),
                            "connection_id": str(connection_a.id),
                            "member_reference_id": str(member_reference_id),
                            "membership_id": str(membership_id),
                            "member_ref": member_ref,
                            "token_digest": "2" * 64,
                            "expires_at": (utcnow() + timedelta(minutes=5)).isoformat(),
                            "outbox_id": str(uuid.uuid4()),
                            "event_id": str(uuid.uuid4()),
                            "idempotency_key": "direct-issue-without-scan",
                            "payload_digest": "3" * 64,
                        }
                    ),
                )

        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime.fetchval(
                    "SELECT mutate_commerce_handoff_authority($1,$2::jsonb)",
                    tenant_a,
                    json.dumps(
                        {
                            "action": "redeem",
                            "handoff_id": token_payload["handoff_id"],
                            "connection_id": str(connection_a.id),
                            "member_ref": member_ref,
                            "target_shop": "shop-a",
                            "token_digest": hashlib.sha256(token.encode()).hexdigest(),
                            "event_id": str(uuid.uuid4()),
                            "idempotency_key": "direct-redeem",
                            "payload_digest": "e" * 64,
                        }
                    ),
                )

        owner = await asyncpg.connect(owner_dsn)
        try:
            copied_ciphertext = await owner.fetchval(
                "SELECT secret_ciphertext FROM commerce_service_credentials WHERE id=$1", incoming_a["id"]
            )
        finally:
            await owner.close()
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
            with pytest.raises(asyncpg.CheckViolationError, match="commerce_credential_ciphertext_reuse"):
                await runtime.fetchval(
                    "SELECT mutate_commerce_connection_authority($1,$2::jsonb)",
                    tenant_a,
                    json.dumps(
                        {
                            "action": "rotate_credential",
                            "connection_id": str(connection_a.id),
                            "actor_id": str(actor_a),
                            "auth_session_id": str(auth_session_a),
                            "credential": {
                                "id": str(uuid.uuid4()),
                                "direction": "commerce_to_yimatong",
                                "version": 4,
                                "key_prefix": f"copy-{uuid.uuid4().hex[:8]}",
                                "secret_ciphertext": bytes(copied_ciphertext).hex(),
                                "valid_from": utcnow().isoformat(),
                                "valid_until": (utcnow() + timedelta(days=90)).isoformat(),
                            },
                            "event_id": str(uuid.uuid4()),
                            "idempotency_key": "direct-copy-ciphertext",
                            "payload_digest": "f" * 64,
                        },
                        default=str,
                    ),
                )

        for table in TABLES:
            for statement in (
                f"INSERT INTO {table} DEFAULT VALUES",
                f"UPDATE {table} SET tenant_id=tenant_id",
                f"DELETE FROM {table}",
            ):
                async with runtime.transaction():
                    await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await runtime.execute(statement)
    finally:
        await runtime.close()

    owner = await asyncpg.connect(owner_dsn)
    try:
        before_callback_failure = {
            table: await owner.fetchval("SELECT count(id) FROM " + table) for table in before_foreign_scan
        }
    finally:
        await owner.close()

    callback = await asyncpg.connect(owner_dsn.replace("yimatong:yimatong@", "yimatong_callback:yimatong_callback@"))
    try:
        async with callback.transaction():
            await callback.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
            with pytest.raises(asyncpg.CheckViolationError, match="commerce_handoff_scope_invalid"):
                await callback.fetchval(
                    "SELECT mutate_commerce_handoff_authority($1,$2::jsonb)",
                    tenant_a,
                    json.dumps(
                        {
                            "action": "issue",
                            "handoff_id": str(uuid.uuid4()),
                            "connection_id": str(connection_a.id),
                            "member_reference_id": str(member_reference_id),
                            "membership_id": str(membership_id),
                            "consumer_id": str(facts["consumer_id"]),
                            "scan_event_id": str(foreign_scan_event_id),
                            "public_id": facts["public_id"],
                            "member_ref": member_ref,
                            "token_digest": "7" * 64,
                            "expires_at": (utcnow() + timedelta(minutes=5)).isoformat(),
                            "outbox_id": str(uuid.uuid4()),
                            "event_id": str(uuid.uuid4()),
                            "idempotency_key": "callback-foreign-scan",
                            "payload_digest": "4" * 64,
                        }
                    ),
                )
    finally:
        await callback.close()

    owner = await asyncpg.connect(owner_dsn)
    try:
        assert {
            table: await owner.fetchval("SELECT count(id) FROM " + table) for table in before_foreign_scan
        } == before_callback_failure
    finally:
        await owner.close()

    env = os.environ.copy()
    env["database_url"] = migrated_pg_url
    env["migration_database_url"] = migrated_pg_url
    owner = await asyncpg.connect(owner_dsn)
    try:
        await owner.execute(
            "UPDATE member_notification_deliveries SET status='delivering',lease_token=$1,"
            "lease_expires_at=statement_timestamp()+interval '1 minute' WHERE id=$2",
            uuid.uuid4(),
            withdrawn_delivery_id,
        )
    finally:
        await owner.close()
    in_flight_downgrade = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "downgrade",
        "-1",
        cwd=BACKEND_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await in_flight_downgrade.communicate()
    assert in_flight_downgrade.returncode != 0
    assert b"notification deliveries are in flight; wait for leases before downgrade" in stdout + stderr
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "5e3f9a4bac21"
        await owner.execute(
            "UPDATE member_notification_deliveries SET status='suppressed',lease_token=NULL,lease_expires_at=NULL "
            "WHERE id=$1",
            withdrawn_delivery_id,
        )
    finally:
        await owner.close()
    coupon_fact_downgrade = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "downgrade",
        "-1",
        cwd=BACKEND_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await coupon_fact_downgrade.communicate()
    assert coupon_fact_downgrade.returncode != 0
    assert b"commerce coupon attribution facts exist; archive before downgrade" in stdout + stderr
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "5e3f9a4bac21"
        await owner.execute(
            "UPDATE commerce_repurchase_attributions SET coupon_id=NULL,coupon_attributed=false "
            "WHERE coupon_id IS NOT NULL"
        )
    finally:
        await owner.close()
    downgrade = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "downgrade",
        "c8a8c3346d7c",
        cwd=BACKEND_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await downgrade.communicate()
    assert downgrade.returncode != 0
    assert b"partial refund receipts exist; archive before downgrade" in stdout + stderr
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "0b91acfedc87"
    finally:
        await owner.close()
    upgrade = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        "head",
        cwd=BACKEND_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await upgrade.communicate()
    assert upgrade.returncode == 0, (stdout + stderr).decode()
