"""Real ASGI proof for signed WeCom callbacks through the callback-only authority."""

from __future__ import annotations

import base64
import hashlib
import struct
import uuid
from pathlib import Path
from unittest.mock import patch

import asyncpg
import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_db
from app.main import app
from app.services.connectors.secrets import encrypt_secrets
from tests.test_acceptance.test_campaign_authority_rollout import _isolated_campaign_rollout_database
from tests.test_acceptance.test_code_batch_delivery_contract import _seed_catalog

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


def _encrypted_callback(
    *,
    corp_id: str,
    callback_token: str,
    aes_key: bytes,
    event_xml: str,
) -> tuple[bytes, dict[str, str]]:
    raw = b"0123456789abcdef" + struct.pack(">I", len(event_xml.encode())) + event_xml.encode() + corp_id.encode()
    pad = 32 - len(raw) % 32
    padded = raw + bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16])).encryptor()
    encrypted = base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()
    timestamp, nonce = "1700000000", "asgi-nonce"
    signature = hashlib.sha1("".join(sorted([callback_token, timestamp, nonce, encrypted])).encode()).hexdigest()
    body = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode()
    return body, {"msg_signature": signature, "timestamp": timestamp, "nonce": nonce}


async def test_signed_wecom_callback_records_once_and_exact_retry_replays(migrated_pg_url: str) -> None:
    from app.core import database
    from app.services import wecom_callback_authority

    async with _isolated_campaign_rollout_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        owner = await asyncpg.connect(owner_dsn)
        runtime_engine = control_engine = callback_engine = None
        try:
            await owner.execute((Path(__file__).parents[2] / "scripts" / "init_runtime_role.sql").read_text())
            ids = await _seed_catalog(owner, "wecom-asgi-authority")
            connector_id = uuid.uuid4()
            corp_id = f"ww{uuid.uuid4().hex[:16]}"
            callback_token = "verified-callback-token"
            aes_key = b"K" * 32
            encoding_aes_key = base64.b64encode(aes_key).decode().rstrip("=")
            await owner.execute(
                "INSERT INTO connectors(id,tenant_id,name,connector_type,config,secrets_encrypted,enabled,created_at,updated_at) "
                "VALUES($1,$2,'WeCom ASGI','wecom_customer_contact',$3::jsonb,$4,true,now(),now())",
                connector_id,
                ids["tenant"],
                f'{{"corp_id":"{corp_id}"}}',
                encrypt_secrets({"callback_token": callback_token, "encoding_aes_key": encoding_aes_key}),
            )
            state = f"state-{uuid.uuid4().hex}"
            await owner.execute(
                "INSERT INTO wecom_contact_ways(id,tenant_id,connector_id,state,user_ids,status,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,$5::jsonb,'active',now(),now())",
                uuid.uuid4(),
                ids["tenant"],
                connector_id,
                state,
                '["staff-1","staff-2"]',
            )
            external_id = f"external-{uuid.uuid4().hex[:16]}"
            event_xml = (
                "<xml><Event>change_external_contact</Event><ChangeType>add_external_contact</ChangeType>"
                f"<ExternalUserID>{external_id}</ExternalUserID><UserID>staff-1</UserID><State>{state}</State>"
                "<CreateTime>1700000000</CreateTime><Sequence>1</Sequence></xml>"
            )
            body, query = _encrypted_callback(
                corp_id=corp_id,
                callback_token=callback_token,
                aes_key=aes_key,
                event_xml=event_xml,
            )

            runtime_engine = create_async_engine(
                database_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
            )
            control_engine = create_async_engine(database_url)
            callback_engine = create_async_engine(
                database_url.replace("yimatong:yimatong@", "yimatong_callback:yimatong_callback@")
            )
            runtime_factory = async_sessionmaker(runtime_engine, expire_on_commit=False)
            control_factory = async_sessionmaker(control_engine, expire_on_commit=False)
            callback_factory = async_sessionmaker(callback_engine, expire_on_commit=False)

            async def override_get_db():
                async with runtime_factory() as session:
                    try:
                        yield session
                        await session.commit()
                    except Exception:
                        await session.rollback()
                        raise

            app.dependency_overrides[get_db] = override_get_db
            with (
                patch.object(database, "control_session_factory", control_factory),
                patch.object(wecom_callback_authority, "callback_session_factory", callback_factory),
            ):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    first = await client.post(
                        f"/api/v1/integrations/wecom/callback/{connector_id}",
                        params=query,
                        content=body,
                    )
                    replay = await client.post(
                        f"/api/v1/integrations/wecom/callback/{connector_id}",
                        params=query,
                        content=body,
                    )
                    missing_user_body, missing_user_query = _encrypted_callback(
                        corp_id=corp_id,
                        callback_token=callback_token,
                        aes_key=aes_key,
                        event_xml=(
                            "<xml><Event>change_external_contact</Event><ChangeType>add_external_contact</ChangeType>"
                            f"<ExternalUserID>{external_id}</ExternalUserID><State>{state}</State>"
                            "<CreateTime>1700000001</CreateTime><Sequence>1</Sequence></xml>"
                        ),
                    )
                    missing_user = await client.post(
                        f"/api/v1/integrations/wecom/callback/{connector_id}",
                        params=missing_user_query,
                        content=missing_user_body,
                    )
                    unknown_user_body, unknown_user_query = _encrypted_callback(
                        corp_id=corp_id,
                        callback_token=callback_token,
                        aes_key=aes_key,
                        event_xml=(
                            "<xml><Event>change_external_contact</Event><ChangeType>add_external_contact</ChangeType>"
                            f"<ExternalUserID>{external_id}</ExternalUserID><UserID>staff-unknown</UserID>"
                            f"<State>{state}</State><CreateTime>1700000001</CreateTime><Sequence>1</Sequence></xml>"
                        ),
                    )
                    unknown_user = await client.post(
                        f"/api/v1/integrations/wecom/callback/{connector_id}",
                        params=unknown_user_query,
                        content=unknown_user_body,
                    )
                    staff_two_body, staff_two_query = _encrypted_callback(
                        corp_id=corp_id,
                        callback_token=callback_token,
                        aes_key=aes_key,
                        event_xml=(
                            "<xml><Event>change_external_contact</Event><ChangeType>add_external_contact</ChangeType>"
                            f"<ExternalUserID>{external_id}</ExternalUserID><UserID>staff-2</UserID>"
                            f"<State>{state}</State><CreateTime>1700000002</CreateTime><Sequence>1</Sequence></xml>"
                        ),
                    )
                    staff_two = await client.post(
                        f"/api/v1/integrations/wecom/callback/{connector_id}",
                        params=staff_two_query,
                        content=staff_two_body,
                    )
                    delete_one_body, delete_one_query = _encrypted_callback(
                        corp_id=corp_id,
                        callback_token=callback_token,
                        aes_key=aes_key,
                        event_xml=(
                            "<xml><Event>change_external_contact</Event><ChangeType>del_external_contact</ChangeType>"
                            f"<ExternalUserID>{external_id}</ExternalUserID><UserID>staff-1</UserID>"
                            "<CreateTime>1700000003</CreateTime><Sequence>1</Sequence></xml>"
                        ),
                    )
                    delete_one = await client.post(
                        f"/api/v1/integrations/wecom/callback/{connector_id}",
                        params=delete_one_query,
                        content=delete_one_body,
                    )
                    readd_one_body, readd_one_query = _encrypted_callback(
                        corp_id=corp_id,
                        callback_token=callback_token,
                        aes_key=aes_key,
                        event_xml=(
                            "<xml><Event>change_external_contact</Event><ChangeType>add_external_contact</ChangeType>"
                            f"<ExternalUserID>{external_id}</ExternalUserID><UserID>staff-1</UserID>"
                            f"<State>{state}</State><CreateTime>1700000004</CreateTime><Sequence>1</Sequence></xml>"
                        ),
                    )
                    readd_one = await client.post(
                        f"/api/v1/integrations/wecom/callback/{connector_id}",
                        params=readd_one_query,
                        content=readd_one_body,
                    )
                    delete_two_body, delete_two_query = _encrypted_callback(
                        corp_id=corp_id,
                        callback_token=callback_token,
                        aes_key=aes_key,
                        event_xml=(
                            "<xml><Event>change_external_contact</Event><ChangeType>del_follow_user</ChangeType>"
                            f"<ExternalUserID>{external_id}</ExternalUserID><UserID>staff-2</UserID>"
                            "<CreateTime>1700000005</CreateTime><Sequence>1</Sequence></xml>"
                        ),
                    )
                    delete_two = await client.post(
                        f"/api/v1/integrations/wecom/callback/{connector_id}",
                        params=delete_two_query,
                        content=delete_two_body,
                    )

            assert first.status_code == 200 and first.text == "success"
            assert replay.status_code == 200 and replay.text == "success"
            assert missing_user.status_code == 422
            assert unknown_user.status_code == 404
            assert staff_two.status_code == 200 and staff_two.text == "success"
            assert delete_one.status_code == 200 and delete_one.text == "success"
            assert readd_one.status_code == 200 and readd_one.text == "success"
            assert delete_two.status_code == 200 and delete_two.text == "success"
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM wecom_callback_receipts WHERE tenant_id=$1 AND connector_id=$2",
                    ids["tenant"],
                    connector_id,
                )
                == 5
            )
            contacts = await owner.fetch(
                "SELECT user_id,status,verification_source,welcome_code_pending,raw_event "
                "FROM wecom_external_contacts WHERE tenant_id=$1 AND connector_id=$2 AND external_userid=$3 "
                "ORDER BY user_id",
                ids["tenant"],
                connector_id,
                external_id,
            )
            assert [dict(contact) for contact in contacts] == [
                {
                    "user_id": "staff-1",
                    "status": "active",
                    "verification_source": "confirmed_callback",
                    "welcome_code_pending": False,
                    "raw_event": "{}",
                },
                {
                    "user_id": "staff-2",
                    "status": "deleted",
                    "verification_source": "termination_callback",
                    "welcome_code_pending": False,
                    "raw_event": "{}",
                },
            ]
        finally:
            app.dependency_overrides.pop(get_db, None)
            for engine in (runtime_engine, control_engine, callback_engine):
                if engine is not None:
                    await engine.dispose()
            await owner.close()
