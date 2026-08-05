"""Enterprise WeChat customer-contact integration service."""

from __future__ import annotations

import base64
import hashlib
import secrets
import string
import struct
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.config import settings
from app.models.campaign import Benefit
from app.models.connector import Connector
from app.models.wecom import (
    WeComConnectorType,
    WeComContactWay,
    WeComContactWayStatus,
    WeComExternalContact,
    WeComExternalContactStatus,
)
from app.services.connectors.secrets import decrypt_secrets, encrypt_secrets, mask_secrets
from app.services.wecom_client import WeChatWorkClient, WeComAPIError

WECOM_MODE_NONE = "none"
WECOM_MODE_GUIDE = "guide"
WECOM_MODE_REQUIRED = "required"
WECOM_MODES = {WECOM_MODE_NONE, WECOM_MODE_GUIDE, WECOM_MODE_REQUIRED}

WECOM_EVENT_ADD = "add_external_contact"
WECOM_EVENT_ADD_HALF = "add_half_external_contact"
WECOM_EVENT_DELETE = "del_external_contact"
WECOM_EVENT_DELETE_FOLLOW = "del_follow_user"


class WeComIntegrationError(RuntimeError):
    """Enterprise WeChat integration configuration or runtime error."""


def generate_callback_token() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(32))


def generate_encoding_aes_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def scan_token_hash(scan_token: str) -> str:
    return hashlib.sha256(scan_token.encode("utf-8")).hexdigest()


def build_callback_url(connector_id: uuid.UUID) -> str:
    return f"{settings.base_url.rstrip('/')}/api/v1/integrations/wecom/callback/{connector_id}"


def is_wecom_required(rules_json: dict | None) -> bool:
    return (rules_json or {}).get("wecom_mode") == WECOM_MODE_REQUIRED


def is_wecom_enabled_for_campaign(rules_json: dict | None) -> bool:
    return (rules_json or {}).get("wecom_mode") in {WECOM_MODE_GUIDE, WECOM_MODE_REQUIRED}


async def get_active_wecom_connector(db: AsyncSession, tenant_id: uuid.UUID) -> Connector | None:
    result = await db.execute(
        select(Connector).where(
            Connector.tenant_id == tenant_id,
            Connector.connector_type == WeComConnectorType.CUSTOMER_CONTACT,
            Connector.enabled.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def get_wecom_status(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    include_callback_credentials: bool = True,
) -> dict:
    connector = await get_active_wecom_connector(db, tenant_id)
    if not connector:
        status = {
            "connected": False,
            "status": "not_configured",
            "callback_url": None,
            "config": {},
        }
        if include_callback_credentials:
            status["secrets"] = {}
        return status
    status = {
        "connected": connector.config.get("status") == "connected",
        "status": connector.config.get("status", "configured"),
        "connector_id": str(connector.id),
        "callback_url": build_callback_url(connector.id),
        "config": {
            "corp_id": connector.config.get("corp_id"),
            "customer_service_user_ids": connector.config.get("customer_service_user_ids", []),
            "last_verified_at": connector.config.get("last_verified_at"),
            "last_event_at": connector.config.get("last_event_at"),
            "last_error": connector.config.get("last_error"),
            "mock_mode": connector.config.get("mock_mode", False),
        },
    }
    if include_callback_credentials:
        secrets_data = decrypt_secrets(connector.secrets_encrypted or b"")
        status["secrets"] = {
            "secret": mask_secrets({"secret": secrets_data.get("secret", "")}).get("secret"),
            "callback_token": secrets_data.get("callback_token", ""),
            "encoding_aes_key": secrets_data.get("encoding_aes_key", ""),
        }
    return status


async def list_configured_wecom_members(db: AsyncSession, tenant_id: uuid.UUID) -> list[str]:
    """Return only persisted member ids without decrypting connector credentials."""
    connector = await get_active_wecom_connector(db, tenant_id)
    if not connector:
        return []
    return list(connector.config.get("customer_service_user_ids") or [])


async def upsert_wecom_connector(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    corp_id: str,
    secret: str | None,
    customer_service_user_ids: list[str] | None = None,
    mock_mode: bool = False,
) -> dict:
    if settings.environment == "production" and mock_mode:
        raise WeComIntegrationError("生产环境不允许启用企业微信模拟模式")
    result = await db.execute(
        select(Connector).where(
            Connector.tenant_id == tenant_id,
            Connector.connector_type == WeComConnectorType.CUSTOMER_CONTACT,
        )
    )
    connector = result.scalar_one_or_none()
    existing_secrets = decrypt_secrets(connector.secrets_encrypted or b"") if connector else {}
    secrets_data = {
        "secret": secret or existing_secrets.get("secret", ""),
        "callback_token": existing_secrets.get("callback_token") or generate_callback_token(),
        "encoding_aes_key": existing_secrets.get("encoding_aes_key") or generate_encoding_aes_key(),
    }
    config = {
        "corp_id": corp_id,
        "customer_service_user_ids": customer_service_user_ids or [],
        "mock_mode": mock_mode,
        "status": "configured",
        "last_error": None,
    }
    if connector:
        connector.name = "企业微信客户联系"
        connector.config = {**connector.config, **config}
        connector.secrets_encrypted = encrypt_secrets(secrets_data)
        connector.enabled = True
    else:
        connector = Connector(
            tenant_id=tenant_id,
            name="企业微信客户联系",
            connector_type=WeComConnectorType.CUSTOMER_CONTACT,
            config=config,
            secrets_encrypted=encrypt_secrets(secrets_data),
            enabled=True,
        )
        db.add(connector)
    await db.flush()
    await db.refresh(connector)
    return await get_wecom_status(db, tenant_id)


async def verify_wecom_connector(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    connector = await get_active_wecom_connector(db, tenant_id)
    if not connector:
        raise WeComIntegrationError("请先保存企业微信配置")

    secrets_data = decrypt_secrets(connector.secrets_encrypted or b"")
    if connector.config.get("mock_mode"):
        if settings.environment == "production":
            raise WeComIntegrationError("生产环境不允许使用企业微信模拟模式")
        connector.config = {
            **connector.config,
            "status": "connected",
            "last_verified_at": datetime.now(UTC).isoformat(),
            "last_error": None,
        }
        await db.flush()
        return await get_wecom_status(db, tenant_id)

    if not connector.config.get("corp_id") or not secrets_data.get("secret"):
        raise WeComIntegrationError("请填写企业 ID 和客户联系 Secret")

    client = WeChatWorkClient(connector.config["corp_id"], secrets_data["secret"])
    try:
        await client.list_follow_users()
    except WeComAPIError as exc:
        connector.config = {**connector.config, "status": "error", "last_error": exc.errmsg}
        await db.flush()
        raise WeComIntegrationError(_friendly_wecom_error(exc.errcode, exc.errmsg)) from exc
    finally:
        await client.close()

    connector.config = {
        **connector.config,
        "status": "connected",
        "last_verified_at": datetime.now(UTC).isoformat(),
        "last_error": None,
    }
    await db.flush()
    return await get_wecom_status(db, tenant_id)


async def list_wecom_members(db: AsyncSession, tenant_id: uuid.UUID) -> list[str]:
    connector = await get_active_wecom_connector(db, tenant_id)
    if not connector:
        return []
    if connector.config.get("mock_mode"):
        configured = connector.config.get("customer_service_user_ids") or ["demo-member"]
        return list(configured)

    secrets_data = decrypt_secrets(connector.secrets_encrypted or b"")
    client = WeChatWorkClient(connector.config["corp_id"], secrets_data["secret"])
    try:
        data = await client.list_follow_users()
    finally:
        await client.close()
    return list(data.get("follow_user", []))


async def get_or_create_claim_contact_way(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    benefit: Benefit,
    scan_token: str,
) -> WeComContactWay:
    connector = await get_active_wecom_connector(db, tenant_id)
    if not connector or connector.config.get("status") != "connected":
        raise WeComIntegrationError("企微添加入口暂不可用，请稍后再试")
    if settings.environment == "production" and connector.config.get("mock_mode"):
        raise WeComIntegrationError("生产环境不允许使用企业微信模拟模式")

    token_hash = scan_token_hash(scan_token)
    existing_result = await db.execute(
        select(WeComContactWay).where(
            WeComContactWay.tenant_id == tenant_id,
            WeComContactWay.benefit_id == benefit.id,
            WeComContactWay.scan_token_hash == token_hash,
            WeComContactWay.status == WeComContactWayStatus.ACTIVE,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        return existing

    state = f"wcx_{uuid7().hex}"
    user_ids = list(connector.config.get("customer_service_user_ids") or [])
    if not user_ids and connector.config.get("mock_mode"):
        user_ids = ["demo-member"]
    if not user_ids:
        members = await list_wecom_members(db, tenant_id)
        user_ids = members[:1]
    if not user_ids:
        raise WeComIntegrationError("请先在企业微信中配置可添加客户的成员")

    config_id: str | None = None
    qr_code: str | None = None
    if connector.config.get("mock_mode"):
        qr_code = _mock_qr_data_uri(state)
        config_id = f"mock-{state}"
    else:
        secrets_data = decrypt_secrets(connector.secrets_encrypted or b"")
        client = WeChatWorkClient(connector.config["corp_id"], secrets_data["secret"])
        try:
            data = await client.add_contact_way(state=state, user_ids=user_ids, remark="扫码活动")
        finally:
            await client.close()
        config_id = data.get("config_id")
        qr_code = data.get("qr_code")

    contact_way = WeComContactWay(
        tenant_id=tenant_id,
        connector_id=connector.id,
        campaign_id=benefit.campaign_id,
        benefit_id=benefit.id,
        config_id=config_id,
        qr_code=qr_code,
        state=state,
        user_ids=user_ids,
        scan_token_hash=token_hash,
        status=WeComContactWayStatus.ACTIVE,
    )
    db.add(contact_way)
    await db.flush()
    await db.refresh(contact_way)
    return contact_way


async def has_confirmed_wecom_contact(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    benefit_id: uuid.UUID,
    scan_token: str,
) -> bool:
    """yimatong-zgb1.12 Decision 26：只有完整 add_external_contact 才算确认。

    排除 welcome_code_pending（half-add，待客户确认）以及任何非验签 mock 证据。
    """
    token_hash = scan_token_hash(scan_token)
    result = await db.execute(
        select(WeComExternalContact.id).where(
            WeComExternalContact.tenant_id == tenant_id,
            WeComExternalContact.benefit_id == benefit_id,
            WeComExternalContact.scan_token_hash == token_hash,
            WeComExternalContact.status == WeComExternalContactStatus.ACTIVE,
            WeComExternalContact.verification_source == "confirmed_callback",
            # yimatong-zgb1.12：排除 half-add（welcome_code_pending）
            WeComExternalContact.welcome_code_pending.is_(False),
        )
    )
    return result.scalar_one_or_none() is not None


async def process_wecom_callback_event(
    db: AsyncSession,
    *,
    connector_id: uuid.UUID,
    event: dict,
    verification_source: str = "confirmed_callback",
) -> dict:
    """Apply one verified callback with monotonic, row-locked state transitions."""
    connector_result = await db.execute(select(Connector).where(Connector.id == connector_id).with_for_update())
    connector = connector_result.scalar_one_or_none()
    if not connector or connector.connector_type != WeComConnectorType.CUSTOMER_CONTACT:
        raise WeComIntegrationError("企业微信配置不存在")
    if verification_source == "mock_added":
        if settings.environment == "production" or not connector.config.get("mock_mode"):
            raise WeComIntegrationError("当前环境不允许企业微信模拟回调")
    elif verification_source != "confirmed_callback":
        raise WeComIntegrationError("未知的企业微信回调验证来源")

    event_type = str(event.get("Event") or event.get("event") or "").strip()
    if event_type != "change_external_contact":
        return {"status": "ignored", "reason": "unsupported_event"}

    change_type = str(event.get("ChangeType") or event.get("change_type") or "").strip()
    supported_types = {WECOM_EVENT_ADD, WECOM_EVENT_ADD_HALF, WECOM_EVENT_DELETE, WECOM_EVENT_DELETE_FOLLOW}
    if change_type not in supported_types:
        return {"status": "ignored", "reason": "unsupported_change_type"}

    state = event.get("State") or event.get("state")
    external_userid = event.get("ExternalUserID") or event.get("external_userid")
    user_id = event.get("UserID") or event.get("user_id")
    if not external_userid:
        return {"status": "ignored", "reason": "missing_external_userid"}

    raw_create_time = event.get("CreateTime") or event.get("create_time")
    if raw_create_time in (None, "") and verification_source == "mock_added":
        raw_create_time = int(datetime.now(UTC).timestamp())
    try:
        create_time = int(raw_create_time)
        event_time = datetime.fromtimestamp(create_time, UTC)
        event_sequence = int(event.get("Sequence") or event.get("sequence") or 0)
    except (TypeError, ValueError, OverflowError):
        return {"status": "ignored", "reason": "invalid_event_order"}
    if create_time < 0 or event_sequence < 0:
        return {"status": "ignored", "reason": "invalid_event_order"}

    contact_way = None
    if state:
        way_result = await db.execute(
            select(WeComContactWay).where(
                WeComContactWay.tenant_id == connector.tenant_id,
                WeComContactWay.state == state,
            )
        )
        contact_way = way_result.scalar_one_or_none()

    is_half_add = change_type == WECOM_EVENT_ADD_HALF
    is_delete = change_type in {WECOM_EVENT_DELETE, WECOM_EVENT_DELETE_FOLLOW}
    fingerprint_input = f"{change_type}|{create_time}|{event_sequence}|{external_userid}|{state or ''}"
    event_fingerprint = hashlib.sha256(fingerprint_input.encode()).hexdigest()

    dup_check = await db.execute(
        select(WeComExternalContact.id).where(
            WeComExternalContact.tenant_id == connector.tenant_id,
            WeComExternalContact.event_fingerprint == event_fingerprint,
        )
    )
    if dup_check.scalar_one_or_none():
        return {"status": "duplicate", "reason": "event_fingerprint_exists"}

    identity_filters = [
        WeComExternalContact.tenant_id == connector.tenant_id,
        WeComExternalContact.connector_id == connector.id,
        WeComExternalContact.external_userid == external_userid,
    ]
    if is_delete:
        if state:
            identity_filters.append(WeComExternalContact.state == state)
        elif user_id:
            identity_filters.append(WeComExternalContact.user_id == user_id)
    else:
        identity_filters.append(
            WeComExternalContact.state == state if state is not None else WeComExternalContact.state.is_(None)
        )
    contacts = list(
        (
            await db.execute(
                select(WeComExternalContact)
                .where(*identity_filters)
                .order_by(WeComExternalContact.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if not contacts:
        contact = WeComExternalContact(
            tenant_id=connector.tenant_id,
            connector_id=connector.id,
            external_userid=external_userid,
            state=state,
            user_id=user_id,
        )
        db.add(contact)
        contacts = [contact]

    event_rank = {WECOM_EVENT_ADD_HALF: 0, WECOM_EVENT_ADD: 1, WECOM_EVENT_DELETE: 2, WECOM_EVENT_DELETE_FOLLOW: 2}
    updated = 0
    for contact in contacts:
        current_order = (
            contact.event_time or datetime.min.replace(tzinfo=UTC),
            contact.event_sequence if contact.event_sequence is not None else -1,
            event_rank.get(contact.change_type or "", -1),
        )
        incoming_order = (event_time, event_sequence, event_rank[change_type])
        if incoming_order <= current_order:
            continue

        if contact_way is not None:
            contact.contact_way_id = contact_way.id
            contact.campaign_id = contact_way.campaign_id
            contact.benefit_id = contact_way.benefit_id
            contact.scan_token_hash = contact_way.scan_token_hash
        contact.user_id = user_id or contact.user_id
        contact.unionid = event.get("UnionID") or event.get("unionid") or contact.unionid
        contact.status = WeComExternalContactStatus.DELETED if is_delete else WeComExternalContactStatus.ACTIVE
        contact.raw_event = event
        if change_type == WECOM_EVENT_ADD:
            contact.verification_source = verification_source
        elif is_half_add:
            contact.verification_source = (
                "pending_callback" if verification_source == "confirmed_callback" else verification_source
            )
        else:
            contact.verification_source = (
                "termination_callback" if verification_source == "confirmed_callback" else verification_source
            )
        contact.change_type = change_type
        contact.event_fingerprint = event_fingerprint
        contact.event_time = event_time
        contact.event_sequence = event_sequence
        contact.welcome_code_pending = is_half_add
        if is_delete:
            contact.deleted_at = event_time
        elif not is_half_add:
            contact.added_at = event_time
            contact.deleted_at = None
        updated += 1

    if updated == 0:
        return {"status": "ignored", "reason": "non_newer_event"}

    connector.config = {**connector.config, "last_event_at": event_time.isoformat()}
    await db.flush()
    return {"status": "recorded", "change_type": change_type}


def verify_wecom_signature(token: str, timestamp: str, nonce: str, msg_encrypt: str, signature: str) -> bool:
    raw = "".join(sorted([token, timestamp, nonce, msg_encrypt]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest() == signature


def decrypt_wecom_message(encoding_aes_key: str, encrypted: str, corp_id: str) -> str:
    key = base64.b64decode(encoding_aes_key + "=")
    if len(key) != 32:
        raise WeComIntegrationError("消息加密密钥格式不正确")
    cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16]))
    decryptor = cipher.decryptor()
    plain = decryptor.update(base64.b64decode(encrypted)) + decryptor.finalize()
    pad = plain[-1]
    if pad < 1 or pad > 32:
        raise WeComIntegrationError("消息解密失败")
    plain = plain[:-pad]
    msg_len = struct.unpack(">I", plain[16:20])[0]
    xml = plain[20 : 20 + msg_len].decode("utf-8")
    from_appid = plain[20 + msg_len :].decode("utf-8")
    if from_appid != corp_id:
        raise WeComIntegrationError("企业 ID 不匹配")
    return xml


def parse_wecom_xml(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    return {child.tag: child.text for child in root}


def parse_wecom_callback_body(body: bytes, connector: Connector, query: dict[str, str]) -> dict:
    text = body.decode("utf-8") if body else ""
    if not text:
        return {}
    if text.lstrip().startswith("{"):
        raise WeComIntegrationError("企业微信生产回调不接受未验签 JSON")
    payload = parse_wecom_xml(text)
    encrypted = payload.get("Encrypt")
    if not encrypted:
        raise WeComIntegrationError("企业微信回调缺少加密消息")
    secrets_data = decrypt_secrets(connector.secrets_encrypted or b"")
    signature = query.get("msg_signature") or query.get("signature") or ""
    timestamp = query.get("timestamp") or ""
    nonce = query.get("nonce") or ""
    token = secrets_data.get("callback_token") or ""
    if not verify_wecom_signature(token, timestamp, nonce, encrypted, signature):
        raise WeComIntegrationError("请求签名校验失败")
    xml = decrypt_wecom_message(
        secrets_data.get("encoding_aes_key") or "",
        encrypted,
        connector.config.get("corp_id") or "",
    )
    return parse_wecom_xml(xml)


def decrypt_wecom_echo(connector: Connector, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
    secrets_data = decrypt_secrets(connector.secrets_encrypted or b"")
    token = secrets_data.get("callback_token") or ""
    if not verify_wecom_signature(token, timestamp, nonce, echostr, msg_signature):
        raise WeComIntegrationError("请求签名校验失败")
    return decrypt_wecom_message(
        secrets_data.get("encoding_aes_key") or "",
        echostr,
        connector.config.get("corp_id") or "",
    )


def _friendly_wecom_error(errcode: int, errmsg: str) -> str:
    if errcode in {40014, 42001, 40001, 41001}:
        return "企业微信凭证不可用，请检查企业 ID 和客户联系 Secret"
    if errcode in {48002, 84061, 84074}:
        return "当前企业微信应用还没有客户联系权限，请在企业微信后台开通后重试"
    return errmsg or "企业微信连接失败，请检查配置后重试"


def _mock_qr_data_uri(state: str) -> str:
    target = quote(f"{settings.base_url.rstrip('/')}/api/v1/integrations/wecom/mock-added?state={state}", safe="")
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='240' height='240'>"
        "<rect width='240' height='240' fill='#f8fafc'/>"
        "<rect x='24' y='24' width='192' height='192' fill='none' stroke='#0f172a' stroke-width='8'/>"
        "<text x='120' y='112' font-size='18' font-family='Arial' "
        "text-anchor='middle' fill='#0f172a'>企业微信</text>"
        "<text x='120' y='140' font-size='12' font-family='Arial' "
        "text-anchor='middle' fill='#475569'>本地演示二维码</text>"
        f"<text x='120' y='164' font-size='8' font-family='Arial' "
        f"text-anchor='middle' fill='#64748b'>{target[:36]}</text>"
        "</svg>"
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
