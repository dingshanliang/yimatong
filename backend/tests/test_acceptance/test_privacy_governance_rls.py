"""Real PostgreSQL proof for member privacy governance and sensitive exports."""

import asyncio
import os
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.privacy_governance import (
    create_consumer_privacy_request,
    download_sensitive_export,
    maintain_privacy_retention,
    mutate_privacy_request,
    mutate_sensitive_export,
    prepare_sensitive_export,
    reveal_consumer_phone,
)
from app.utils.crypto import encrypt_consumer_phone
from tests.test_acceptance.conftest import seed_baseline
from tests.test_acceptance.test_brand_membership_rls import _seed_join_facts

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLES = (
    "privacy_rights_requests",
    "privacy_rights_events",
    "member_pii_access_events",
    "sensitive_member_exports",
    "sensitive_member_export_events",
)


async def test_privacy_rights_pii_reveal_and_sensitive_export_are_hard_gated(migrated_pg_url: str) -> None:
    summary = await seed_baseline(migrated_pg_url)
    tenant_a = uuid.UUID(str(summary["baseline_tenant"]["id"]))
    tenant_b = uuid.UUID(str(summary["control_tenant"]["id"]))
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    try:
        actor = await owner.fetchrow(
            "SELECT account.id AS account_id,account.organization_id,account.auth_version,account_role.role_id "
            "FROM accounts account JOIN account_roles account_role ON account_role.tenant_id=account.tenant_id "
            "AND account_role.account_id=account.id WHERE account.tenant_id=$1 LIMIT 1",
            tenant_a,
        )
        assert actor is not None
        approver_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,is_active,auth_version,"
            "must_change_password,failed_login_attempts) VALUES($1,$2,$3,$4,'test','隐私审批人',true,0,false,0)",
            approver_id,
            tenant_a,
            actor["organization_id"],
            f"privacy-approver-{approver_id.hex[:8]}@example.test",
        )
        await owner.execute(
            "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
            tenant_a,
            approver_id,
            actor["role_id"],
        )
        for code in ("consumer:pii_reveal", "export:sensitive_approve"):
            permission_id = await owner.fetchval(
                "SELECT id FROM permissions WHERE tenant_id=$1 AND code=$2",
                tenant_a,
                code,
            )
            if permission_id is None:
                permission_id = uuid.uuid4()
                await owner.execute(
                    "INSERT INTO permissions(id,tenant_id,code,description) VALUES($1,$2,$3,'隐私验收高风险权限')",
                    permission_id,
                    tenant_a,
                    code,
                )
            await owner.execute(
                "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
                tenant_a,
                actor["role_id"],
                permission_id,
            )
        actor_session = uuid.uuid4()
        approver_session = uuid.uuid4()
        for session_id, account_id, auth_version in (
            (actor_session, actor["account_id"], actor["auth_version"]),
            (approver_session, approver_id, 0),
        ):
            await owner.execute(
                "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at) "
                "VALUES($1,$2,$3,$4,$5,statement_timestamp()+interval '1 hour')",
                session_id,
                tenant_a,
                account_id,
                auth_version,
                f"privacy-{session_id.hex}",
            )
        facts = await _seed_join_facts(owner, tenant_a, f"PRIVACY-{uuid.uuid4().hex[:8]}")
        membership_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO brand_memberships(id,tenant_id,membership_number,status,join_consent_id,joined_at) "
            "VALUES($1,$2,$3,'active',$4,statement_timestamp())",
            membership_id,
            tenant_a,
            f"MBR-{membership_id.hex[:12].upper()}",
            facts["consent_id"],
        )
        await owner.execute(
            "INSERT INTO brand_membership_profile_links(id,tenant_id,membership_id,consumer_profile_id,is_primary,"
            "link_reason,verification_receipt_hash) VALUES($1,$2,$3,$4,true,'explicit_join',$5)",
            uuid.uuid4(),
            tenant_a,
            membership_id,
            facts["consumer_id"],
            "b" * 64,
        )
        ciphertext, nonce, key_id = encrypt_consumer_phone(tenant_a, facts["consumer_id"], "13800138000")
        await owner.execute(
            "UPDATE consumer_profiles SET phone_hash=$1,phone_ciphertext=$2,phone_nonce=$3,phone_key_id=$4,nickname='测试会员' "
            "WHERE tenant_id=$5 AND id=$6",
            "a" * 64,
            ciphertext,
            nonce,
            key_id,
            tenant_a,
            facts["consumer_id"],
        )
        excluded_consumer_id = uuid.uuid4()
        excluded_membership_id = uuid.uuid4()
        excluded_ciphertext, excluded_nonce, excluded_key_id = encrypt_consumer_phone(
            tenant_a, excluded_consumer_id, "13900139000"
        )
        await owner.execute(
            "INSERT INTO consumer_profiles(id,tenant_id,nickname,phone_hash,phone_ciphertext,phone_nonce,phone_key_id,"
            "total_points,member_level) VALUES($1,$2,'范围外会员',$3,$4,$5,$6,0,'bronze')",
            excluded_consumer_id,
            tenant_a,
            "e" * 64,
            excluded_ciphertext,
            excluded_nonce,
            excluded_key_id,
        )
        await owner.execute(
            "INSERT INTO brand_memberships(id,tenant_id,membership_number,status,join_consent_id,joined_at) "
            "VALUES($1,$2,$3,'active',$4,statement_timestamp())",
            excluded_membership_id,
            tenant_a,
            f"MBR-{excluded_membership_id.hex[:12].upper()}",
            facts["consent_id"],
        )
        await owner.execute(
            "INSERT INTO brand_membership_profile_links(id,tenant_id,membership_id,consumer_profile_id,is_primary,"
            "link_reason,verification_receipt_hash) VALUES($1,$2,$3,$4,true,'explicit_join',$5)",
            uuid.uuid4(),
            tenant_a,
            excluded_membership_id,
            excluded_consumer_id,
            "f" * 64,
        )
        for table in TABLES:
            security = await owner.fetchrow(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                "WHERE relnamespace='public'::regnamespace AND relname=$1",
                table,
            )
            assert security and security["relrowsecurity"] and security["relforcerowsecurity"]
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,$2)", table, privilege)
        assert not await owner.fetchval(
            "SELECT has_column_privilege('yimatong_app','sensitive_member_exports','artifact_ciphertext','SELECT')"
        )
        assert not await owner.fetchval(
            "SELECT has_column_privilege('yimatong_app','sensitive_member_exports','download_token_digest','SELECT')"
        )
    finally:
        await owner.close()

    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    engine = create_async_engine(runtime_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    export_id = uuid.uuid4()
    try:
        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_a)})
            request_id = await create_consumer_privacy_request(
                db,
                tenant_id=tenant_a,
                consumer_id=facts["consumer_id"],
                membership_id=membership_id,
                request_type="delete",
                reason="消费者要求删除非必要会员资料",
                evidence={"channel": "member_center"},
            )
            phone = await reveal_consumer_phone(
                db,
                tenant_id=tenant_a,
                actor_id=actor["account_id"],
                auth_session_id=actor_session,
                consumer_id=facts["consumer_id"],
                reason="处理消费者权利申请时核验联系方式",
                ticket_ref="PIR-ACCEPTANCE",
                request_trace_id="trace-privacy-acceptance",
            )
            assert phone == "13800138000"
            await mutate_privacy_request(
                db,
                tenant_id=tenant_a,
                actor_id=actor["account_id"],
                auth_session_id=actor_session,
                request_id=request_id,
                payload={
                    "action": "assign",
                    "owner_account_id": str(actor["account_id"]),
                    "reason": "指派隐私负责人",
                    "evidence": {},
                },
            )
            await mutate_sensitive_export(
                db,
                tenant_id=tenant_a,
                actor_id=actor["account_id"],
                auth_session_id=actor_session,
                export_id=export_id,
                payload={
                    "action": "request",
                    "reason": "经核验的监管材料准备",
                    "recipient_purpose": "响应品牌范围合规审查",
                    "requested_fields": ["membership_number", "nickname", "phone"],
                    "filters": {"membership_status": "active", "membership_ids": [str(membership_id)]},
                },
            )

        async with factory() as db:
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,false)"), {"tenant_id": str(tenant_a)})
            with pytest.raises(HTTPException) as self_approval:
                await mutate_sensitive_export(
                    db,
                    tenant_id=tenant_a,
                    actor_id=actor["account_id"],
                    auth_session_id=actor_session,
                    export_id=export_id,
                    payload={"action": "approve", "reason": "申请人试图自批"},
                )
            assert self_approval.value.status_code == 403
            await db.rollback()

        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_a)})
            await mutate_sensitive_export(
                db,
                tenant_id=tenant_a,
                actor_id=approver_id,
                auth_session_id=approver_session,
                export_id=export_id,
                payload={"action": "approve", "reason": "第二名授权人员确认目的与字段"},
            )

        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_a)})
            token = await prepare_sensitive_export(
                db,
                tenant_id=tenant_a,
                actor_id=actor["account_id"],
                auth_session_id=actor_session,
                export_id=export_id,
                reason="审批通过后生成加密文件",
            )
            content, file_name = await download_sensitive_export(
                db,
                tenant_id=tenant_a,
                actor_id=actor["account_id"],
                auth_session_id=actor_session,
                export_id=export_id,
                token=token,
                reason="申请人一次性下载",
            )
            assert "13800138000" in content.decode("utf-8-sig")
            assert "13900139000" not in content.decode("utf-8-sig")
            assert file_name.endswith(".csv")

        async with factory() as db:
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,false)"), {"tenant_id": str(tenant_a)})
            with pytest.raises(HTTPException) as replay:
                await download_sensitive_export(
                    db,
                    tenant_id=tenant_a,
                    actor_id=actor["account_id"],
                    auth_session_id=actor_session,
                    export_id=export_id,
                    token=token,
                    reason="重复下载应拒绝",
                )
            assert replay.value.status_code == 403
            await db.rollback()

        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_a)})
            await mutate_privacy_request(
                db,
                tenant_id=tenant_a,
                actor_id=actor["account_id"],
                auth_session_id=actor_session,
                request_id=request_id,
                payload={
                    "action": "complete",
                    "reason": "已完成身份核验与必要保存审查",
                    "outcome": "非必要个人信息已删除，必要审计事实限制访问保留",
                    "evidence": {"review": "privacy_acceptance"},
                },
            )
            restrict_request_id = await create_consumer_privacy_request(
                db,
                tenant_id=tenant_a,
                consumer_id=facts["consumer_id"],
                membership_id=membership_id,
                request_type="restrict",
                reason="消费者要求暂停后续会员运营",
                evidence={"channel": "member_center"},
            )
            await mutate_privacy_request(
                db,
                tenant_id=tenant_a,
                actor_id=actor["account_id"],
                auth_session_id=actor_session,
                request_id=restrict_request_id,
                payload={"action": "restrict", "reason": "立即限制处理", "evidence": {}},
            )

        owner = await asyncpg.connect(owner_dsn)
        try:
            await owner.execute(
                "UPDATE consumer_profiles SET nickname='过期备份昵称',phone_hash=$1,phone_ciphertext=$2,phone_nonce=$3,"
                "phone_key_id=$4,lead_contact_suppressed=false WHERE tenant_id=$5 AND id=$6",
                "c" * 64,
                ciphertext,
                nonce,
                key_id,
                tenant_a,
                facts["consumer_id"],
            )
            await owner.execute(
                "UPDATE sensitive_member_exports SET expires_at=statement_timestamp()-interval '1 second' "
                "WHERE tenant_id=$1 AND id=$2",
                tenant_a,
                export_id,
            )
        finally:
            await owner.close()

        async with factory() as db, db.begin():
            retention = await maintain_privacy_retention(db, tenant_a)
            assert retention == {"restored_controls": 1, "purged_exports": 1}
            restored = (
                (
                    await db.execute(
                        text(
                            "SELECT nickname,phone_hash,phone_ciphertext,lead_contact_suppressed "
                            "FROM consumer_profiles WHERE tenant_id=:tenant_id AND id=:consumer_id"
                        ),
                        {"tenant_id": tenant_a, "consumer_id": facts["consumer_id"]},
                    )
                )
                .mappings()
                .one()
            )
            assert restored["nickname"] is None
            assert restored["phone_hash"] is None
            assert restored["phone_ciphertext"] is None
            assert restored["lead_contact_suppressed"] is True
            export_summary = await db.execute(
                text(
                    "SELECT status,deleted_at FROM sensitive_member_export_summaries "
                    "WHERE tenant_id=:tenant_id AND id=:export_id"
                ),
                {"tenant_id": tenant_a, "export_id": export_id},
            )
            export_row = export_summary.mappings().one()
            assert export_row["status"] == "deleted"
            assert export_row["deleted_at"] is not None

        owner = await asyncpg.connect(owner_dsn)
        try:
            deleted_artifact = await owner.fetchrow(
                "SELECT artifact_ciphertext,download_token_digest FROM sensitive_member_exports "
                "WHERE tenant_id=$1 AND id=$2",
                tenant_a,
                export_id,
            )
            assert deleted_artifact is not None
            assert deleted_artifact["artifact_ciphertext"] is None
            assert deleted_artifact["download_token_digest"] is None
        finally:
            await owner.close()

        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_a)})
            assert await db.scalar(text("SELECT count(*) FROM member_pii_access_events")) == 2
            assert await db.scalar(text("SELECT count(*) FROM sensitive_member_export_events")) == 5

        async with factory() as db, db.begin():
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_b)})
            for table in ("privacy_rights_requests", "privacy_rights_events", "member_pii_access_events"):
                assert await db.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    finally:
        await engine.dispose()

    runtime = await asyncpg.connect(owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    try:
        for statement in (
            "UPDATE privacy_rights_requests SET status='completed'",
            "DELETE FROM privacy_rights_events",
            "UPDATE member_pii_access_events SET reason='forged'",
            "SELECT artifact_ciphertext FROM sensitive_member_exports",
            "UPDATE sensitive_member_export_events SET reason='forged'",
        ):
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await runtime.execute(statement)
    finally:
        await runtime.close()

    env = os.environ.copy()
    env["database_url"] = migrated_pg_url
    env["migration_database_url"] = migrated_pg_url
    downgrade = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "downgrade",
        "-2",
        cwd=BACKEND_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await downgrade.communicate()
    assert downgrade.returncode != 0
    assert b"privacy governance facts exist; archive before downgrade" in stdout + stderr
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
