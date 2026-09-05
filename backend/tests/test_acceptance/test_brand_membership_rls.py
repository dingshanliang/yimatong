"""Real PostgreSQL proof for brand membership tenant isolation and policy seeding."""

import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.brand_membership import (
    bind_verified_member_identity,
    issue_member_recovery_token,
    join_brand_membership,
    merge_brand_memberships,
    recover_brand_membership,
)
from tests.test_acceptance.conftest import BACKEND_DIR, seed_baseline

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

TABLES = (
    "brand_memberships",
    "brand_membership_profile_links",
    "member_identity_credentials",
    "brand_membership_events",
)


async def _seed_join_facts(owner: asyncpg.Connection, tenant_id: uuid.UUID, suffix: str) -> dict:
    consent_id = uuid.uuid4()
    consumer_id = uuid.uuid4()
    public_id = await owner.fetchval(
        "SELECT public_id FROM code_items WHERE tenant_id=$1 ORDER BY public_id LIMIT 1",
        tenant_id,
    )
    assert public_id
    policy = await owner.fetchrow(
        "SELECT policy.id,policy.policy_version,policy.policy_digest "
        "FROM consumer_consent_policy_current AS current_policy "
        "JOIN consumer_consent_policies AS policy ON policy.tenant_id=current_policy.tenant_id "
        "AND policy.id=current_policy.policy_id "
        "WHERE current_policy.tenant_id=$1 AND current_policy.purpose='brand_membership'",
        tenant_id,
    )
    visitor_hash = suffix.lower().ljust(64, "0")[:64]
    scan_event_id = uuid.uuid4()
    scan_time = datetime.now(UTC)
    await owner.execute(
        "INSERT INTO consent_records(id,tenant_id,consent_type,status,purpose,authority_version,policy_id,"
        "policy_version,policy_digest,visitor_subject_hash,public_id,scan_event_id,scan_event_time,idempotency_key) "
        "VALUES($1,$2,'privacy','granted','brand_membership',1,$3,$4,$5,$6,$7,$8,$9,$10)",
        consent_id,
        tenant_id,
        policy["id"],
        policy["policy_version"],
        policy["policy_digest"],
        visitor_hash,
        public_id,
        scan_event_id,
        scan_time,
        f"consent-{suffix}",
    )
    await owner.execute(
        "INSERT INTO consumer_consent_actions"
        "(id,tenant_id,consent_id,policy_id,action,idempotency_key,payload_hash,visitor_subject_hash,result_status) "
        "VALUES($1,$2,$3,$4,'grant',$5,$6,$7,'granted')",
        uuid.uuid4(),
        tenant_id,
        consent_id,
        policy["id"],
        f"grant-{suffix}",
        "c" * 64,
        visitor_hash,
    )
    await owner.execute(
        "INSERT INTO consumer_profiles(id,tenant_id,total_points,member_level) VALUES($1,$2,0,'bronze')",
        consumer_id,
        tenant_id,
    )
    return {
        "consent_id": consent_id,
        "consumer_id": consumer_id,
        "scan_event_id": scan_event_id,
        "scan_time": scan_time,
        "visitor_id": f"visitor-{suffix}",
        "public_id": public_id,
    }


async def test_brand_membership_tables_are_forced_rls_and_seed_membership_policy(migrated_pg_url: str) -> None:
    summary = await seed_baseline(migrated_pg_url)
    tenant_a = uuid.UUID(str(summary["baseline_tenant"]["id"]))
    tenant_b = uuid.UUID(str(summary["control_tenant"]["id"]))
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")

    owner = await asyncpg.connect(owner_dsn)
    try:
        for tenant_id in (tenant_a, tenant_b):
            assert await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM consumer_consent_policy_current "
                "WHERE tenant_id=$1 AND purpose='brand_membership')",
                tenant_id,
            )
        security = await owner.fetch(
            "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relnamespace='public'::regnamespace AND relname=ANY($1::text[]) ORDER BY relname",
            list(TABLES),
        )
        assert len(security) == len(TABLES)
        assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in security)
        for table in TABLES:
            assert not await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.role_table_grants "
                "WHERE table_schema='public' AND table_name=$1 AND grantee='PUBLIC')",
                table,
            )
            for privilege in ("INSERT", "UPDATE", "DELETE"):
                assert not await owner.fetchval(
                    "SELECT has_table_privilege('yimatong_app',$1,$2)", f"public.{table}", privilege
                )
            assert await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,'SELECT')", f"public.{table}")
        assert await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.brand_membership_events'::regclass "
            "AND tgname='trg_guard_immutable_brand_membership_event' AND NOT tgisinternal)"
        )
        for signature in (
            "create_brand_membership_authority(uuid,uuid,uuid,timestamp with time zone,text,text,uuid,boolean,uuid,text,uuid,uuid,text,text)",
            "bind_brand_member_identity_authority(uuid,uuid,uuid,text,text,text,bytea,bytea,text,text,uuid,text,text)",
            "recover_brand_membership_authority(uuid,uuid,uuid,uuid,uuid,uuid,text,text)",
            "merge_brand_memberships_authority(uuid,uuid,uuid,uuid,uuid,uuid,text,text,uuid,uuid,text,jsonb)",
        ):
            assert await owner.fetchval("SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", signature)
        source_facts = await _seed_join_facts(owner, tenant_a, "SOURCE")
        target_facts = await _seed_join_facts(owner, tenant_a, "TARGET")
        recovery_consumer_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO consumer_profiles(id,tenant_id,total_points,member_level) VALUES($1,$2,0,'bronze')",
            recovery_consumer_id,
            tenant_a,
        )
    finally:
        await owner.close()

    runtime = await asyncpg.connect(runtime_dsn)
    try:
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
            source_membership_id = uuid.uuid4()
            source_join = await runtime.fetchrow(
                "SELECT * FROM create_brand_membership_authority($1,$2,$3,$4,$5,$6,$7,true,$8,$9,$10,$11,$12,$13)",
                tenant_a,
                source_facts["consent_id"],
                source_facts["scan_event_id"],
                source_facts["scan_time"],
                source_facts["public_id"],
                source_facts["visitor_id"],
                source_facts["consumer_id"],
                source_membership_id,
                f"MBR-{source_membership_id.hex[:12].upper()}",
                uuid.uuid4(),
                uuid.uuid4(),
                "join-source",
                "a" * 64,
            )
            assert source_join["membership_id"] == source_membership_id
            target_membership_id = uuid.uuid4()
            target_join = await runtime.fetchrow(
                "SELECT * FROM create_brand_membership_authority($1,$2,$3,$4,$5,$6,$7,true,$8,$9,$10,$11,$12,$13)",
                tenant_a,
                target_facts["consent_id"],
                target_facts["scan_event_id"],
                target_facts["scan_time"],
                target_facts["public_id"],
                target_facts["visitor_id"],
                target_facts["consumer_id"],
                target_membership_id,
                f"MBR-{target_membership_id.hex[:12].upper()}",
                uuid.uuid4(),
                uuid.uuid4(),
                "join-target",
                "b" * 64,
            )
            assert target_join["membership_id"] == target_membership_id

            source_credential_id = uuid.uuid4()
            target_credential_id = uuid.uuid4()
            for membership_id, credential_id, issuer, subject_hash, idempotency_key, payload_hash in (
                (source_membership_id, source_credential_id, "wx-source", "1" * 64, "identity-source", "c" * 64),
                (target_membership_id, target_credential_id, "wx-target", "2" * 64, "identity-target", "d" * 64),
            ):
                bound = await runtime.fetchrow(
                    "SELECT * FROM bind_brand_member_identity_authority("
                    "$1,$2,$3,'wechat_openid',$4,$5,$6,$7,'aes-master-v1',$8,$9,$10,$11)",
                    tenant_a,
                    membership_id,
                    credential_id,
                    issuer,
                    subject_hash,
                    b"encrypted-envelope",
                    b"123456789012",
                    "e" * 64,
                    uuid.uuid4(),
                    idempotency_key,
                    payload_hash,
                )
                assert bound["credential_id"] == credential_id

            recovered = await runtime.fetchrow(
                "SELECT * FROM recover_brand_membership_authority($1,$2,$3,$4,$5,$6,$7,$8)",
                tenant_a,
                source_membership_id,
                source_credential_id,
                recovery_consumer_id,
                uuid.uuid4(),
                uuid.uuid4(),
                f"recovery-token:{uuid.uuid4()}",
                "f" * 64,
            )
            assert recovered["membership_id"] == source_membership_id

            source_token_key = f"recovery-token:{uuid.uuid4()}"
            target_token_key = f"recovery-token:{uuid.uuid4()}"
            merged = await runtime.fetchrow(
                "SELECT * FROM merge_brand_memberships_authority($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)",
                tenant_a,
                source_membership_id,
                target_membership_id,
                source_facts["consumer_id"],
                source_credential_id,
                target_credential_id,
                source_token_key,
                target_token_key,
                uuid.uuid4(),
                uuid.uuid4(),
                "9" * 64,
                json.dumps(
                    {
                        str(source_credential_id): {
                            "ciphertext_hex": "ab" * 24,
                            "nonce_hex": "cd" * 12,
                            "key_id": "aes-master-v1",
                        }
                    }
                ),
            )
            assert merged["membership_id"] == target_membership_id
            assert await runtime.fetchval(
                "SELECT status='merged' AND merged_into_id=$2 FROM brand_memberships WHERE id=$1",
                source_membership_id,
                target_membership_id,
            )
            assert await runtime.fetchval(
                "SELECT count(*)=2 FROM brand_membership_events "
                "WHERE idempotency_key=ANY($1::text[]) AND payload_hash=$2",
                [source_token_key, target_token_key],
                "9" * 64,
            )

            for statement, arguments in (
                (
                    "UPDATE brand_memberships SET status='active',merged_into_id=NULL WHERE id=$1",
                    (source_membership_id,),
                ),
                (
                    "INSERT INTO brand_membership_events(id,tenant_id,membership_id,event_type,idempotency_key,payload_hash) "
                    "VALUES($1,$2,$3,'recovered','forged-event',$4)",
                    (uuid.uuid4(), tenant_a, target_membership_id, "0" * 64),
                ),
                (
                    "UPDATE member_identity_credentials SET membership_id=$2 WHERE id=$1",
                    (target_credential_id, source_membership_id),
                ),
                (
                    "INSERT INTO brand_membership_profile_links"
                    "(id,tenant_id,membership_id,consumer_profile_id,is_primary,link_reason,verification_receipt_hash) "
                    "VALUES($1,$2,$3,$4,false,'verified_recovery',$5)",
                    (uuid.uuid4(), tenant_a, target_membership_id, uuid.uuid4(), "0" * 64),
                ),
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with runtime.transaction():
                        await runtime.execute(statement, *arguments)

            for table in TABLES:
                assert await runtime.fetchval(f"SELECT count(*) FROM public.{table}") > 0
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with runtime.transaction():
                    await runtime.execute(
                        "INSERT INTO brand_memberships(id,tenant_id,membership_number,status,join_consent_id) "
                        "VALUES(gen_random_uuid(),$1,'MBR-CROSS','active',$2)",
                        tenant_b,
                        source_facts["consent_id"],
                    )
    finally:
        await runtime.close()

    env = os.environ.copy()
    env["database_url"] = migrated_pg_url
    env["migration_database_url"] = migrated_pg_url
    env["control_database_url"] = migrated_pg_url
    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "a20b21c22d23"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert downgrade.returncode == 0, downgrade.stderr
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "a20b21c22d23"
        assert await owner.fetchval(
            "SELECT is_nullable='NO' FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='member_identity_credentials' "
            "AND column_name='verification_receipt_hash'"
        )
        for function_name in (
            "create_brand_membership_authority",
            "bind_brand_member_identity_authority",
            "recover_brand_membership_authority",
            "merge_brand_memberships_authority",
        ):
            assert await owner.fetchval(
                "SELECT count(*)=1 FROM pg_proc JOIN pg_namespace ON pg_namespace.oid=pg_proc.pronamespace "
                "WHERE pg_namespace.nspname='public' AND pg_proc.proname=$1",
                function_name,
            )
        for table in TABLES:
            privileges = await owner.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee='yimatong_app' AND table_schema='public' AND table_name=$1",
                table,
            )
            assert {row["privilege_type"] for row in privileges} == {"SELECT"}
    finally:
        await owner.close()

    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert upgrade.returncode == 0, upgrade.stderr
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "0b91acfedc87"
    finally:
        await owner.close()


async def test_brand_membership_service_uses_restricted_postgres_authority(migrated_pg_url: str) -> None:
    summary = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(str(summary["baseline_tenant"]["id"]))
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    try:
        source_facts = await _seed_join_facts(owner, tenant_id, f"SVC-SOURCE-{uuid.uuid4().hex[:8]}")
        target_facts = await _seed_join_facts(owner, tenant_id, f"SVC-TARGET-{uuid.uuid4().hex[:8]}")
        recovery_consumer_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO consumer_profiles(id,tenant_id,total_points,member_level) VALUES($1,$2,0,'bronze')",
            recovery_consumer_id,
            tenant_id,
        )
    finally:
        await owner.close()

    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    engine = create_async_engine(runtime_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_id)})
            source = await join_brand_membership(
                db,
                tenant_id=tenant_id,
                consent_id=source_facts["consent_id"],
                scan_event_id=source_facts["scan_event_id"],
                scan_time=source_facts["scan_time"],
                public_id=source_facts["public_id"],
                visitor_id=source_facts["visitor_id"],
                token_consumer_id=source_facts["consumer_id"],
                idempotency_key=f"service-join-source-{uuid.uuid4()}",
            )
            target = await join_brand_membership(
                db,
                tenant_id=tenant_id,
                consent_id=target_facts["consent_id"],
                scan_event_id=target_facts["scan_event_id"],
                scan_time=target_facts["scan_time"],
                public_id=target_facts["public_id"],
                visitor_id=target_facts["visitor_id"],
                token_consumer_id=target_facts["consumer_id"],
                idempotency_key=f"service-join-target-{uuid.uuid4()}",
            )
            source_credential = await bind_verified_member_identity(
                db,
                tenant_id=tenant_id,
                membership_id=source["membership_id"],
                credential_type="wechat_openid",
                issuer=f"service-source-{uuid.uuid4()}",
                subject=f"openid-source-{uuid.uuid4()}",
                verification_receipt_hash="5" * 64,
                idempotency_key=f"service-bind-source-{uuid.uuid4()}",
            )
            target_credential = await bind_verified_member_identity(
                db,
                tenant_id=tenant_id,
                membership_id=target["membership_id"],
                credential_type="wechat_openid",
                issuer=f"service-target-{uuid.uuid4()}",
                subject=f"openid-target-{uuid.uuid4()}",
                verification_receipt_hash="6" * 64,
                idempotency_key=f"service-bind-target-{uuid.uuid4()}",
            )
            recovery_token = issue_member_recovery_token(tenant_id, source["membership_id"], source_credential.id)
            recovered = await recover_brand_membership(
                db,
                tenant_id=tenant_id,
                current_consumer_id=recovery_consumer_id,
                recovery_token=recovery_token,
                idempotency_key=f"service-recover-{uuid.uuid4()}",
            )
            replayed_recovery = await recover_brand_membership(
                db,
                tenant_id=tenant_id,
                current_consumer_id=recovery_consumer_id,
                recovery_token=recovery_token,
                idempotency_key=f"service-recover-replay-{uuid.uuid4()}",
            )
            assert recovered["membership_id"] == source["membership_id"]
            assert replayed_recovery["replayed"] is True
            merged = await merge_brand_memberships(
                db,
                tenant_id=tenant_id,
                current_consumer_id=source["consumer_id"],
                current_recovery_token=issue_member_recovery_token(
                    tenant_id, source["membership_id"], source_credential.id
                ),
                target_recovery_token=issue_member_recovery_token(
                    tenant_id, target["membership_id"], target_credential.id
                ),
                idempotency_key=f"service-merge-{uuid.uuid4()}",
            )
            assert merged["membership_id"] == target["membership_id"]
            assert await db.scalar(
                text(
                    "SELECT count(*)=2 FROM brand_membership_events "
                    "WHERE tenant_id=:tenant_id AND event_type IN ('merged_source','merged_target') "
                    "AND payload_hash=(SELECT payload_hash FROM brand_membership_events "
                    "WHERE tenant_id=:tenant_id AND event_type='merged_source' ORDER BY occurred_at DESC LIMIT 1)"
                ),
                {"tenant_id": tenant_id},
            )
    finally:
        await engine.dispose()
