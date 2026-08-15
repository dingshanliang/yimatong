"""PostgreSQL contract for subject-bound consent and lead PII authority."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from uuid6 import uuid7

from tests.test_acceptance.test_code_batch_delivery_contract import (
    _insert_batch,
    _insert_items,
    _insert_manifest_and_deliver,
    _insert_receipt,
    _seed_catalog,
)
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


def _owner_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _runtime_dsn(url: str) -> str:
    return _owner_dsn(url).replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")


def _callback_dsn(url: str) -> str:
    return _owner_dsn(url).replace("yimatong:yimatong@", "yimatong_callback:yimatong_callback@")


async def _assert_consent_downgrade_stop_and_restore(migrated_pg_url: str, initial_revision: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        has_wechat_bind = await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM consumer_consent_actions WHERE action='wechat_bind')"
        )
    finally:
        await owner.close()

    expected_boundary = "u6f0a1b2c3d4" if has_wechat_bind else "u6e3f4a5b6c7"
    try:
        _alembic(migrated_pg_url, "downgrade", "u6e2e3f4a5b6", succeeds=False)
        owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == expected_boundary
        finally:
            await owner.close()
    finally:
        _alembic(migrated_pg_url, "upgrade", initial_revision)


async def _seed_scan(owner: asyncpg.Connection, label: str) -> dict[str, object]:
    ids = await _seed_catalog(owner, label)
    receipt = await _insert_receipt(owner, ids)
    batch = await _insert_batch(owner, ids, receipt, quantity=1, expected_item_count=1)
    await _insert_items(owner, ids["tenant"], batch, 1)
    await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", batch)
    await _insert_manifest_and_deliver(owner, ids, batch, row_count=1)
    async with owner.transaction():
        public_id = await owner.fetchval(
            "UPDATE code_items SET status='activated',activated_at=now() "
            "WHERE tenant_id=$1 AND code_batch_id=$2 RETURNING public_id",
            ids["tenant"],
            batch,
        )
        await owner.execute(
            "UPDATE code_batches SET status='activated' WHERE tenant_id=$1 AND id=$2", ids["tenant"], batch
        )
        await owner.execute("SET CONSTRAINTS trg_enforce_code_item_parent_final_state IMMEDIATE")
    visitor_id = uuid.uuid4().hex
    scan_event_id = uuid7()
    scan_time = datetime.now(UTC)
    await owner.execute(
        "INSERT INTO anonymous_visitors(id,tenant_id,visitor_id,last_seen_at,created_at,updated_at) "
        "VALUES($1,$2,$3,now(),now(),now())",
        uuid7(),
        ids["tenant"],
        visitor_id,
    )
    await owner.execute(
        "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,is_first_scan,is_valid_visit,visitor_id,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,false,true,$5,now(),now())",
        scan_event_id,
        ids["tenant"],
        public_id,
        scan_time,
        visitor_id,
    )
    other_visitor_id = uuid.uuid4().hex
    other_scan_event_id = uuid7()
    other_scan_time = datetime.now(UTC)
    await owner.execute(
        "INSERT INTO anonymous_visitors(id,tenant_id,visitor_id,last_seen_at,created_at,updated_at) "
        "VALUES($1,$2,$3,now(),now(),now())",
        uuid7(),
        ids["tenant"],
        other_visitor_id,
    )
    await owner.execute(
        "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,is_first_scan,is_valid_visit,visitor_id,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,false,true,$5,now(),now())",
        other_scan_event_id,
        ids["tenant"],
        public_id,
        other_scan_time,
        other_visitor_id,
    )
    return {
        **ids,
        "batch": batch,
        "public_id": public_id,
        "visitor_id": visitor_id,
        "scan_event_id": scan_event_id,
        "scan_time": scan_time,
        "other_visitor_id": other_visitor_id,
        "other_scan_event_id": other_scan_event_id,
        "other_scan_time": other_scan_time,
    }


async def _grant_for_subject(
    runtime: asyncpg.Connection,
    graph: dict[str, object],
    policy: asyncpg.Record,
    consent_id: uuid.UUID,
    audit_id: uuid.UUID,
    idempotency_key: str,
    token_consumer_id: uuid.UUID | None = None,
) -> asyncpg.Record:
    async with runtime.transaction():
        await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
        row = await runtime.fetchrow(
            "SELECT * FROM grant_consumer_consent($1,$2,$3,'lead_capture',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
            graph["tenant"],
            consent_id,
            audit_id,
            policy["policy_version"],
            policy["policy_digest"],
            graph["scan_event_id"],
            graph["scan_time"],
            graph["public_id"],
            graph["visitor_id"],
            token_consumer_id,
            "a" * 64,
            "acceptance",
            idempotency_key,
        )
        assert row is not None
        return row


async def test_active_consent_grants_coalesce_and_bound_subject_can_recover_receipt(
    migrated_pg_url: str,
) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    runtime_a = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    runtime_b = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    graph = await _seed_scan(owner, "consent-coalesce")
    requested_consents = (uuid7(), uuid7())
    requested_actions = (uuid7(), uuid7())
    try:
        async with runtime_a.transaction():
            await runtime_a.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
            policy = await runtime_a.fetchrow(
                "SELECT * FROM get_current_consumer_consent_policy($1,'lead_capture')",
                graph["tenant"],
            )
        assert policy is not None

        grant_a, grant_b = await asyncio.gather(
            _grant_for_subject(
                runtime_a,
                graph,
                policy,
                requested_consents[0],
                requested_actions[0],
                "coalesce-a",
            ),
            _grant_for_subject(
                runtime_b,
                graph,
                policy,
                requested_consents[1],
                requested_actions[1],
                "coalesce-b",
            ),
        )
        assert grant_a["consent_id"] == grant_b["consent_id"]
        assert sorted((grant_a["replayed"], grant_b["replayed"])) == [False, True]
        active_consent_id = grant_a["consent_id"]
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM consent_records WHERE tenant_id=$1 AND purpose='lead_capture' "
                "AND status='granted'",
                graph["tenant"],
            )
            == 1
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM consumer_consent_actions WHERE tenant_id=$1 AND consent_id=$2 AND action='grant'",
                graph["tenant"],
                active_consent_id,
            )
            == 2
        )

        consumer_id, random_consumer_id = uuid7(), uuid7()
        await owner.execute(
            "INSERT INTO consumer_profiles(id,tenant_id,member_level,total_points,extra_data,created_at,updated_at) "
            "VALUES($1,$3,'normal',0,'{}',now(),now()),($2,$3,'normal',0,'{}',now(),now())",
            consumer_id,
            random_consumer_id,
            graph["tenant"],
        )
        await owner.execute(
            "UPDATE anonymous_visitors SET consumer_id=$1,updated_at=now() WHERE tenant_id=$2 AND visitor_id=$3",
            consumer_id,
            graph["tenant"],
            graph["visitor_id"],
        )
        async with runtime_a.transaction():
            await runtime_a.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
            recovered = await runtime_a.fetchrow(
                "SELECT * FROM get_consumer_consent_receipt_status($1,$2,$3,$4,$5,$6,$7)",
                graph["tenant"],
                active_consent_id,
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
                consumer_id,
            )
            assert recovered and recovered["consumer_id"] is None and recovered["status"] == "granted"
            for scan_id, scan_time, visitor_id, token_consumer_id in (
                (graph["other_scan_event_id"], graph["other_scan_time"], graph["other_visitor_id"], None),
                (graph["scan_event_id"], graph["scan_time"], graph["visitor_id"], random_consumer_id),
                (graph["scan_event_id"], graph["scan_time"] + timedelta(seconds=1), graph["visitor_id"], consumer_id),
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with runtime_a.transaction():
                        await runtime_a.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
                        await runtime_a.fetchrow(
                            "SELECT * FROM get_consumer_consent_receipt_status($1,$2,$3,$4,$5,$6,$7)",
                            graph["tenant"],
                            active_consent_id,
                            scan_id,
                            scan_time,
                            graph["public_id"],
                            visitor_id,
                            token_consumer_id,
                        )

        async with runtime_a.transaction():
            await runtime_a.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
            withdrawn = await runtime_a.fetchrow(
                "SELECT * FROM withdraw_consumer_consent($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                graph["tenant"],
                active_consent_id,
                uuid7(),
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
                consumer_id,
                "coalesce-withdraw",
            )
            assert withdrawn and withdrawn["status"] == "withdrawn"
        regrant = await _grant_for_subject(
            runtime_a,
            graph,
            policy,
            uuid7(),
            uuid7(),
            "coalesce-regrant",
            consumer_id,
        )
        assert regrant["consent_id"] != active_consent_id
        assert regrant["consumer_id"] == consumer_id and not regrant["replayed"]
    finally:
        await runtime_b.close()
        await runtime_a.close()
        await owner.close()


async def test_consumer_consent_grant_lead_withdraw_and_acl(migrated_pg_url: str) -> None:
    from app.utils.crypto import EnvKeyProvider, encrypt_consumer_phone, hash_phone, init_crypto

    init_crypto(EnvKeyProvider())
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    initial_revision = await owner.fetchval("SELECT version_num FROM alembic_version")
    assert initial_revision
    graph = await _seed_scan(owner, "consent-authority")
    runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    consent_id = uuid7()
    try:
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
            policy = await runtime.fetchrow(
                "SELECT * FROM get_current_consumer_consent_policy($1,'lead_capture')", graph["tenant"]
            )
            assert policy and policy["policy_title"] and policy["policy_content"]
            action_count = await owner.fetchval(
                "SELECT count(*) FROM consumer_consent_actions WHERE tenant_id=$1", graph["tenant"]
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with runtime.transaction():
                    await runtime.fetchrow(
                        "SELECT * FROM grant_consumer_consent($1,$2,$3,'lead_capture',$4,$5,$6,$7,$8,$9,NULL,$10,$11,$12)",
                        graph["tenant"],
                        uuid7(),
                        uuid7(),
                        policy["policy_version"],
                        policy["policy_digest"],
                        graph["scan_event_id"],
                        graph["scan_time"],
                        graph["public_id"],
                        "wrong-visitor",
                        "a" * 64,
                        "acceptance",
                        "wrong-subject",
                    )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM consumer_consent_actions WHERE tenant_id=$1", graph["tenant"]
                )
                == action_count
            )
            granted = await runtime.fetchrow(
                "SELECT * FROM grant_consumer_consent($1,$2,$3,'lead_capture',$4,$5,$6,$7,$8,$9,NULL,$10,$11,$12)",
                graph["tenant"],
                consent_id,
                uuid7(),
                policy["policy_version"],
                policy["policy_digest"],
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
                "a" * 64,
                "acceptance",
                "grant-1",
            )
            assert granted and granted["status"] == "granted" and not granted["replayed"]
            receipt = await runtime.fetchrow(
                "SELECT * FROM get_consumer_consent_receipt_status($1,$2,$3,$4,$5,$6,NULL)",
                graph["tenant"],
                consent_id,
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
            )
            assert receipt and receipt["status"] == "granted" and receipt["purpose"] == "lead_capture"
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with runtime.transaction():
                    await runtime.fetchrow(
                        "SELECT * FROM get_consumer_consent_receipt_status($1,$2,$3,$4,$5,$6,NULL)",
                        graph["tenant"],
                        consent_id,
                        graph["scan_event_id"],
                        graph["scan_time"] + timedelta(seconds=1),
                        graph["public_id"],
                        graph["visitor_id"],
                    )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with runtime.transaction():
                    await runtime.fetchrow(
                        "SELECT * FROM get_consumer_consent_receipt_status($1,$2,$3,$4,$5,$6,NULL)",
                        graph["tenant"],
                        consent_id,
                        graph["other_scan_event_id"],
                        graph["other_scan_time"],
                        graph["public_id"],
                        graph["other_visitor_id"],
                    )
            replay = await runtime.fetchrow(
                "SELECT * FROM grant_consumer_consent($1,$2,$3,'lead_capture',$4,$5,$6,$7,$8,$9,NULL,$10,$11,$12)",
                graph["tenant"],
                uuid7(),
                uuid7(),
                policy["policy_version"],
                policy["policy_digest"],
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
                "a" * 64,
                "acceptance",
                "grant-1",
            )
            assert replay and replay["consent_id"] == consent_id and replay["replayed"]
            privacy_policy = await runtime.fetchrow(
                "SELECT * FROM get_current_consumer_consent_policy($1,'privacy_policy')", graph["tenant"]
            )
            with pytest.raises(asyncpg.InvalidParameterValueError):
                async with runtime.transaction():
                    await runtime.fetchrow(
                        "SELECT * FROM grant_consumer_consent($1,$2,$3,'privacy_policy',$4,$5,$6,$7,$8,$9,NULL,$10,$11,$12)",
                        graph["tenant"],
                        uuid7(),
                        uuid7(),
                        privacy_policy["policy_version"],
                        privacy_policy["policy_digest"],
                        graph["scan_event_id"],
                        graph["scan_time"],
                        graph["public_id"],
                        graph["visitor_id"],
                        "a" * 64,
                        "acceptance",
                        "grant-1",
                    )
            requested_consumer_id = uuid7()
            phone = "13800138000"
            phone_ciphertext, phone_nonce, phone_key_id = encrypt_consumer_phone(
                graph["tenant"], requested_consumer_id, phone
            )
            with pytest.raises(asyncpg.InvalidParameterValueError):
                async with runtime.transaction():
                    await runtime.fetchrow(
                        "SELECT * FROM capture_consumer_lead($1,$2,$3,$4,$5,$6,$7,$8,NULL,$9,$10,$11,$12,$13,$14::jsonb,$15)",
                        graph["tenant"],
                        requested_consumer_id,
                        uuid7(),
                        consent_id,
                        graph["scan_event_id"],
                        graph["scan_time"],
                        graph["public_id"],
                        graph["visitor_id"],
                        hash_phone(phone),
                        phone.encode(),
                        phone_nonce,
                        phone_key_id,
                        "测试用户",
                        json.dumps({"region": "上海"}),
                        "lead-invalid-plaintext",
                    )
            with pytest.raises(asyncpg.InvalidParameterValueError):
                async with runtime.transaction():
                    await runtime.fetchrow(
                        "SELECT * FROM capture_consumer_lead($1,$2,$3,$4,$5,$6,$7,$8,NULL,$9,$10,$11,$12,$13,$14::jsonb,$15)",
                        graph["tenant"],
                        requested_consumer_id,
                        uuid7(),
                        consent_id,
                        graph["scan_event_id"],
                        graph["scan_time"],
                        graph["public_id"],
                        graph["visitor_id"],
                        hash_phone(phone),
                        phone_ciphertext,
                        phone_nonce,
                        "aes-master-v999",
                        "测试用户",
                        json.dumps({"region": "上海"}),
                        "lead-invalid-key",
                    )
            lead = await runtime.fetchrow(
                "SELECT * FROM capture_consumer_lead($1,$2,$3,$4,$5,$6,$7,$8,NULL,$9,$10,$11,$12,$13,$14::jsonb,$15)",
                graph["tenant"],
                requested_consumer_id,
                uuid7(),
                consent_id,
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
                hash_phone(phone),
                phone_ciphertext,
                phone_nonce,
                phone_key_id,
                "测试用户",
                json.dumps({"region": "上海", "intention": "咨询"}),
                "lead-1",
            )
            assert lead and lead["outcome"] == "captured" and lead["created"]
            consumer_id = lead["consumer_id"]
            first_withdrawn_a = await runtime.fetchrow(
                "SELECT * FROM withdraw_consumer_consent($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                graph["tenant"],
                consent_id,
                uuid7(),
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
                consumer_id,
                "withdraw-a",
            )
            assert first_withdrawn_a and first_withdrawn_a["status"] == "withdrawn"
            assert first_withdrawn_a["contact_suppressed"] is True
            consent_b = uuid7()
            granted_b = await runtime.fetchrow(
                "SELECT * FROM grant_consumer_consent($1,$2,$3,'lead_capture',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
                graph["tenant"],
                consent_b,
                uuid7(),
                policy["policy_version"],
                policy["policy_digest"],
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
                consumer_id,
                "a" * 64,
                "acceptance",
                "grant-2",
            )
            assert granted_b and granted_b["consent_id"] == consent_b
            phone_ciphertext_b, phone_nonce_b, phone_key_id_b = encrypt_consumer_phone(
                graph["tenant"], consumer_id, phone
            )
            lead_b = await runtime.fetchrow(
                "SELECT * FROM capture_consumer_lead($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,$16)",
                graph["tenant"],
                consumer_id,
                uuid7(),
                consent_b,
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
                consumer_id,
                hash_phone(phone),
                phone_ciphertext_b,
                phone_nonce_b,
                phone_key_id_b,
                "新版用户",
                json.dumps({"region": "北京", "intention": "复购"}),
                "lead-2",
            )
            assert lead_b and lead_b["consumer_id"] == consumer_id and not lead_b["created"]
            other_consumer_id = uuid7()
            other_phone = "13700137000"
            other_ciphertext, other_nonce, other_key_id = encrypt_consumer_phone(
                graph["tenant"], other_consumer_id, other_phone
            )
            await owner.execute(
                "INSERT INTO consumer_profiles(id,tenant_id,phone_hash,phone_ciphertext,phone_nonce,phone_key_id,"
                "member_level,total_points,extra_data,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,$5,$6,'normal',0,'{}',now(),now())",
                other_consumer_id,
                graph["tenant"],
                hash_phone(other_phone),
                other_ciphertext,
                other_nonce,
                other_key_id,
            )
            conflicting_ciphertext, conflicting_nonce, conflicting_key_id = encrypt_consumer_phone(
                graph["tenant"], consumer_id, other_phone
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                async with runtime.transaction():
                    await runtime.fetchrow(
                        "SELECT * FROM capture_consumer_lead("
                        "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,$16)",
                        graph["tenant"],
                        consumer_id,
                        uuid7(),
                        consent_b,
                        graph["scan_event_id"],
                        graph["scan_time"],
                        graph["public_id"],
                        graph["visitor_id"],
                        consumer_id,
                        hash_phone(other_phone),
                        conflicting_ciphertext,
                        conflicting_nonce,
                        conflicting_key_id,
                        "冲突用户",
                        json.dumps({"region": "深圳"}),
                        "lead-cross-profile-conflict",
                    )
            assert await runtime.fetchval(
                "SELECT phone_hash=$1 AND lead_consent_id=$2 FROM consumer_profiles WHERE id=$3",
                hash_phone(phone),
                consent_b,
                consumer_id,
            )
            assert not await runtime.fetchval(
                "SELECT EXISTS(SELECT 1 FROM consumer_consent_actions WHERE tenant_id=$1 "
                "AND action='lead_capture' AND idempotency_key='lead-cross-profile-conflict')",
                graph["tenant"],
            )
            withdrawn_a = await runtime.fetchrow(
                "SELECT * FROM withdraw_consumer_consent($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                graph["tenant"],
                consent_id,
                uuid7(),
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
                consumer_id,
                "withdraw-a",
            )
            assert withdrawn_a and withdrawn_a["status"] == "withdrawn"
            assert withdrawn_a["replayed"] is True
            assert withdrawn_a["contact_suppressed"] is False
            current_lead = await runtime.fetchrow(
                "SELECT lead_consent_id,phone_hash,phone_ciphertext,phone_nonce,phone_key_id,nickname,"
                "extra_data,lead_contact_suppressed "
                "FROM consumer_profiles WHERE tenant_id=$1 AND id=$2",
                graph["tenant"],
                consumer_id,
            )
            assert current_lead and current_lead["lead_consent_id"] == consent_b
            assert current_lead["phone_hash"] == hash_phone(phone)
            assert bytes(current_lead["phone_ciphertext"]) == phone_ciphertext_b
            assert bytes(current_lead["phone_nonce"]) == phone_nonce_b
            assert current_lead["phone_key_id"] == phone_key_id_b
            assert current_lead["nickname"] == "新版用户"
            assert current_lead["lead_contact_suppressed"] is False
            withdrawn_b = await runtime.fetchrow(
                "SELECT * FROM withdraw_consumer_consent($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                graph["tenant"],
                consent_b,
                uuid7(),
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
                consumer_id,
                "withdraw-b",
            )
            assert withdrawn_b and withdrawn_b["status"] == "withdrawn"
            assert withdrawn_b["contact_suppressed"] is True
            withdrawn_receipt = await runtime.fetchrow(
                "SELECT * FROM get_consumer_consent_receipt_status($1,$2,$3,$4,$5,$6,$7)",
                graph["tenant"],
                consent_b,
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
                consumer_id,
            )
            assert withdrawn_receipt and withdrawn_receipt["status"] == "withdrawn"
            assert withdrawn_receipt["withdrawn_at"] is not None
        profile = await owner.fetchrow(
            "SELECT phone_hash,phone_ciphertext,phone_nonce,phone_key_id,nickname,extra_data,"
            "lead_contact_suppressed,wechat_openid_hash "
            "FROM consumer_profiles WHERE tenant_id=$1 AND id=$2",
            graph["tenant"],
            consumer_id,
        )
        assert profile and profile["phone_hash"] is None and profile["phone_ciphertext"] is None
        assert profile["phone_nonce"] is None and profile["phone_key_id"] is None
        assert profile["nickname"] is None and profile["lead_contact_suppressed"] is True
        extra_data = (
            json.loads(profile["extra_data"]) if isinstance(profile["extra_data"], str) else profile["extra_data"]
        )
        assert "region" not in extra_data and "intention" not in extra_data
        assert await owner.fetchval(
            "SELECT consumer_id=$1 FROM anonymous_visitors WHERE tenant_id=$2 AND visitor_id=$3",
            consumer_id,
            graph["tenant"],
            graph["visitor_id"],
        )
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with runtime.transaction():
                    await runtime.execute(
                        "INSERT INTO consent_records(id,tenant_id,consent_type,status) VALUES($1,$2,'privacy','granted')",
                        uuid7(),
                        graph["tenant"],
                    )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with runtime.transaction():
                    await runtime.execute(
                        "UPDATE consumer_profiles SET phone_hash=$1 WHERE tenant_id=$2 AND id=$3",
                        "c" * 64,
                        graph["tenant"],
                        consumer_id,
                    )
    finally:
        await runtime.close()
        await owner.close()
    await _assert_consent_downgrade_stop_and_restore(migrated_pg_url, str(initial_revision))


async def test_wechat_callback_binding_is_function_only_and_subject_bound(migrated_pg_url: str) -> None:
    from app.utils.crypto import EnvKeyProvider, encrypt_wechat_openid, hash_wechat_openid, init_crypto

    init_crypto(EnvKeyProvider())
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    initial_revision = await owner.fetchval("SELECT version_num FROM alembic_version")
    assert initial_revision
    graph = await _seed_scan(owner, "consent-oauth-bind")
    runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    callback = await asyncpg.connect(_callback_dsn(migrated_pg_url))
    consent_id = uuid7()
    benefit_id = uuid7()
    requested_consumer_id = uuid7()
    openid = "openid-consent-authority"
    openid_hash = hash_wechat_openid(graph["tenant"], openid)
    ciphertext, nonce, key_id = encrypt_wechat_openid(graph["tenant"], requested_consumer_id, openid)
    try:
        await owner.execute(
            "INSERT INTO benefits(id,tenant_id,name,benefit_type,config_json,stock_total,stock_used,"
            "per_person_limit,status) VALUES($1,$2,'OAuth cash','cash_red_packet','{}'::jsonb,10,0,1,'active')",
            benefit_id,
            graph["tenant"],
        )
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
            policy = await runtime.fetchrow(
                "SELECT * FROM get_current_consumer_consent_policy($1,'wechat_cash_payout')", graph["tenant"]
            )
            granted = await runtime.fetchrow(
                "SELECT * FROM grant_consumer_consent($1,$2,$3,'wechat_cash_payout',$4,$5,$6,$7,$8,$9,NULL,$10,$11,$12)",
                graph["tenant"],
                consent_id,
                uuid7(),
                policy["policy_version"],
                policy["policy_digest"],
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
                "c" * 64,
                "acceptance",
                "oauth-grant",
            )
            assert granted and granted["consumer_id"] is None
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with runtime.transaction():
                    await runtime.fetchrow(
                        "SELECT * FROM bind_wechat_oauth_consumer($1,$2,$3,$4,$5,$6,NULL,$7,$8,$9,$10,$11,$12,$13)",
                        graph["tenant"],
                        consent_id,
                        graph["scan_event_id"],
                        graph["scan_time"],
                        graph["public_id"],
                        graph["visitor_id"],
                        requested_consumer_id,
                        openid_hash,
                        ciphertext,
                        nonce,
                        key_id,
                        uuid7(),
                        benefit_id,
                    )
        async with callback.transaction():
            await callback.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
            exact_facts = await owner.fetchrow(
                "SELECT record.authority_version,record.status,record.purpose,record.scenario,"
                "record.visitor_subject_hash=encode(digest(convert_to($1::text||':'||$2,'UTF8'),'sha256'),'hex') "
                "AS subject_exact,record.public_id=$3 AS public_exact,record.consumer_id IS NULL AS consumer_exact,"
                "visitor.consumer_id IS NULL AS visitor_exact,event.scan_time=$4 AS time_exact,"
                "event.is_valid_visit AS event_valid,benefit.benefit_type='cash_red_packet' AS benefit_type_exact,"
                "benefit.status='active' AS benefit_active,policy.id=record.policy_id AS policy_exact,"
                "policy.policy_digest=record.policy_digest AS digest_exact "
                "FROM consent_records AS record JOIN anonymous_visitors AS visitor "
                "ON visitor.tenant_id=record.tenant_id AND visitor.visitor_id=$2 "
                "JOIN scan_events AS event ON event.tenant_id=record.tenant_id AND event.id=$5 "
                "JOIN benefits AS benefit ON benefit.tenant_id=record.tenant_id AND benefit.id=$6 "
                "JOIN consumer_consent_policy_current AS current_policy "
                "ON current_policy.tenant_id=record.tenant_id AND current_policy.purpose='wechat_cash_payout' "
                "JOIN consumer_consent_policies AS policy ON policy.tenant_id=current_policy.tenant_id "
                "AND policy.purpose=current_policy.purpose AND policy.id=current_policy.policy_id "
                "WHERE record.tenant_id=$1::uuid AND record.id=$7",
                str(graph["tenant"]),
                graph["visitor_id"],
                graph["public_id"],
                graph["scan_time"],
                graph["scan_event_id"],
                benefit_id,
                consent_id,
            )
            assert exact_facts and dict(exact_facts) == {
                "authority_version": 1,
                "status": "granted",
                "purpose": "wechat_cash_payout",
                "scenario": "wechat_cash_payout",
                "subject_exact": True,
                "public_exact": True,
                "consumer_exact": True,
                "visitor_exact": True,
                "time_exact": True,
                "event_valid": True,
                "benefit_type_exact": True,
                "benefit_active": True,
                "policy_exact": True,
                "digest_exact": True,
            }
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with callback.transaction():
                    await callback.fetchval("SELECT count(*) FROM consumer_profiles")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with callback.transaction():
                    await callback.fetchrow(
                        "SELECT * FROM bind_wechat_oauth_consumer($1,$2,$3,$4,$5,$6,NULL,$7,$8,$9,$10,$11,$12,$13)",
                        graph["tenant"],
                        consent_id,
                        graph["other_scan_event_id"],
                        graph["other_scan_time"],
                        graph["public_id"],
                        graph["other_visitor_id"],
                        requested_consumer_id,
                        openid_hash,
                        ciphertext,
                        nonce,
                        key_id,
                        uuid7(),
                        benefit_id,
                    )
            bound = await callback.fetchrow(
                "SELECT * FROM bind_wechat_oauth_consumer($1,$2,$3,$4,$5,$6,NULL,$7,$8,$9,$10,$11,$12,$13)",
                graph["tenant"],
                consent_id,
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
                requested_consumer_id,
                openid_hash,
                ciphertext,
                nonce,
                key_id,
                uuid7(),
                benefit_id,
            )
            assert bound and bound["consumer_id"] == requested_consumer_id and not bound["replayed"]
            replay = await callback.fetchrow(
                "SELECT * FROM bind_wechat_oauth_consumer($1,$2,$3,$4,$5,$6,NULL,$7,$8,$9,$10,$11,$12,$13)",
                graph["tenant"],
                consent_id,
                graph["scan_event_id"],
                graph["scan_time"],
                graph["public_id"],
                graph["visitor_id"],
                requested_consumer_id,
                openid_hash,
                ciphertext,
                nonce,
                key_id,
                uuid7(),
                benefit_id,
            )
            assert replay and replay["consumer_id"] == requested_consumer_id and replay["replayed"]
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM consumer_consent_actions WHERE tenant_id=$1 AND action='wechat_bind'",
                graph["tenant"],
            )
            == 1
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM platform_audit_log WHERE target_tenant_id=$1 AND action='consumer_wechat_bound'",
                str(graph["tenant"]),
            )
            == 1
        )
        assert await owner.fetchval(
            "SELECT consumer_id=$1 FROM consent_records WHERE tenant_id=$2 AND id=$3",
            requested_consumer_id,
            graph["tenant"],
            consent_id,
        )
    finally:
        await callback.close()
        await runtime.close()
        await owner.close()
    await _assert_consent_downgrade_stop_and_restore(migrated_pg_url, str(initial_revision))


async def test_anonymous_consumer_profile_creation_is_actor_bound_and_audited(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    graph = await _seed_scan(owner, "anonymous-consumer-create")
    runtime = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    session_id = uuid7()
    permission_id = uuid7()
    consumer_id = uuid7()
    try:
        await owner.execute(
            "INSERT INTO auth_sessions(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,"
            "created_at,updated_at) VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
            session_id,
            graph["account"],
            graph["tenant"],
            uuid.uuid4().hex,
        )
        await owner.execute(
            "INSERT INTO permissions(id,tenant_id,code,created_at,updated_at) "
            "VALUES($1,$2,'consumer:detail',now(),now())",
            permission_id,
            graph["tenant"],
        )
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            graph["tenant"],
            graph["admin_role"],
            permission_id,
        )
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
            created = await runtime.fetchrow(
                "SELECT * FROM create_anonymous_consumer_profile($1,$2,$3,$4)",
                graph["tenant"],
                session_id,
                uuid7(),
                consumer_id,
            )
            assert created and created["consumer_id"] == consumer_id
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with runtime.transaction():
                    await runtime.execute(
                        "INSERT INTO consumer_profiles(id,tenant_id,member_level,total_points,extra_data) "
                        "VALUES($1,$2,'normal',0,'{}'::json)",
                        uuid7(),
                        graph["tenant"],
                    )
        profile = await owner.fetchrow("SELECT * FROM consumer_profiles WHERE id=$1", consumer_id)
        assert profile and profile["phone_hash"] is None and profile["phone_ciphertext"] is None
        assert profile["wechat_openid_hash"] is None and profile["nickname"] is None
        assert profile["lead_contact_suppressed"] is False
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM platform_audit_log WHERE target_tenant_id=$1 "
                "AND action='consumer_profile_created' AND resource=$2",
                str(graph["tenant"]),
                f"consumer_profile:{consumer_id}",
            )
            == 1
        )
        await owner.execute(
            "DELETE FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3",
            graph["tenant"],
            graph["admin_role"],
            permission_id,
        )
        denied_id = uuid7()
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with runtime.transaction():
                    await runtime.fetchrow(
                        "SELECT * FROM create_anonymous_consumer_profile($1,$2,$3,$4)",
                        graph["tenant"],
                        session_id,
                        uuid7(),
                        denied_id,
                    )
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM consumer_profiles WHERE id=$1)", denied_id)
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            graph["tenant"],
            graph["admin_role"],
            permission_id,
        )
        await owner.execute("UPDATE auth_sessions SET revoked_at=now() WHERE id=$1", session_id)
        revoked_id = uuid7()
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with runtime.transaction():
                    await runtime.fetchrow(
                        "SELECT * FROM create_anonymous_consumer_profile($1,$2,$3,$4)",
                        graph["tenant"],
                        session_id,
                        uuid7(),
                        revoked_id,
                    )
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM consumer_profiles WHERE id=$1)", revoked_id)
    finally:
        await runtime.close()
        await owner.close()
