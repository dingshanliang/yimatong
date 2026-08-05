"""CRM 同步 — 消费者→CRM 推送事件处理器。

监听 event_bus 的 consumer.created / consumer.updated 事件，
将消费者数据推送到企微外部联系人。
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_bus import event_bus
from app.models.connector import Connector
from app.models.integration import SyncRecord
from app.models.member import ConsumerProfile
from app.services.connectors.wecom_crm import WeChatWorkCrmAdapter
from app.services.crm.matcher import (
    create_or_update_mapping,
    decrypt_and_match_phone,
    match_by_external_id,
)

logger = logging.getLogger(__name__)

wecom_adapter = WeChatWorkCrmAdapter()


async def _get_wecom_client(db: AsyncSession, tenant_id: uuid.UUID):
    """获取租户的企微客户端。"""
    result = await db.execute(
        select(Connector).where(
            Connector.tenant_id == tenant_id,
            Connector.connector_type == "wecom_crm",
            Connector.enabled == True,  # noqa: E712
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        return None, None
    client = await wecom_adapter.get_client(connector)
    return client, connector


async def _sync_record(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    action: str,
    external_id: str | None,
    detail: dict,
):
    """写入同步审计记录。"""
    record = SyncRecord(
        tenant_id=tenant_id,
        sync_type=f"crm_push_{action}",
        external_id=external_id,
        data={"consumer_id": str(consumer_id), **detail},
    )
    db.add(record)


async def handle_consumer_created(event_type: str, data: dict, tenant_id_str: str) -> None:
    """消费者创建事件处理器。"""
    tenant_id = uuid.UUID(tenant_id_str)
    consumer_id = data.get("consumer_id")
    if not consumer_id:
        return

    from app.core.database import async_session_factory, set_session_tenant_context

    async with async_session_factory() as db:
        await set_session_tenant_context(db, tenant_id)
        # 获取消费者
        result = await db.execute(
            select(ConsumerProfile).where(
                ConsumerProfile.tenant_id == tenant_id,
                ConsumerProfile.id == uuid.UUID(consumer_id),
            )
        )
        consumer = result.scalar_one_or_none()
        if not consumer:
            return

        client, connector = await _get_wecom_client(db, tenant_id)
        if not client:
            return

        try:
            # 检查是否已有映射
            existing = await match_by_external_id(db, tenant_id, "wecom", consumer_id)

            if existing and existing.mapping:
                # 已映射，执行更新
                await client.update_external_contact(
                    existing.mapping.external_id,
                    {
                        "remark": consumer.nickname or "",
                    },
                )
                existing.mapping.last_synced_at = __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                )
                await _sync_record(
                    db,
                    tenant_id,
                    consumer.id,
                    "update",
                    existing.mapping.external_id,
                    {"status": "updated"},
                )
            else:
                # 尝试通过手机号匹配
                phone = await decrypt_and_match_phone(db, tenant_id, consumer)
                if phone:
                    contacts = await client.batch_get_external_contacts_by_phone([phone])
                    if contacts:
                        # 命中，建立映射
                        ext_user = contacts[0]
                        ext_id = ext_user.get("external_userid", "")
                        await create_or_update_mapping(
                            db,
                            tenant_id,
                            consumer.id,
                            "wecom",
                            ext_id,
                        )
                        await _sync_record(
                            db,
                            tenant_id,
                            consumer.id,
                            "match",
                            ext_id,
                            {"status": "matched_by_phone"},
                        )
                        logger.info(
                            "CRM sync: matched consumer %s → wecom %s by phone",
                            consumer_id,
                            ext_id,
                        )
                    else:
                        await _sync_record(
                            db,
                            tenant_id,
                            consumer.id,
                            "skip",
                            None,
                            {"status": "no_crm_match"},
                        )
                else:
                    await _sync_record(
                        db,
                        tenant_id,
                        consumer.id,
                        "skip",
                        None,
                        {"status": "no_phone"},
                    )

            await db.commit()
        except Exception:
            logger.exception("CRM push failed for consumer %s", consumer_id)
        finally:
            await client.close()


async def handle_consumer_updated(event_type: str, data: dict, tenant_id_str: str) -> None:
    """消费者更新事件处理器。"""
    tenant_id = uuid.UUID(tenant_id_str)
    consumer_id = data.get("consumer_id")
    if not consumer_id:
        return

    from app.core.database import async_session_factory, set_session_tenant_context

    async with async_session_factory() as db:
        await set_session_tenant_context(db, tenant_id)
        result = await db.execute(
            select(ConsumerProfile).where(
                ConsumerProfile.tenant_id == tenant_id,
                ConsumerProfile.id == uuid.UUID(consumer_id),
            )
        )
        consumer = result.scalar_one_or_none()
        if not consumer:
            return

        client, connector = await _get_wecom_client(db, tenant_id)
        if not client:
            return

        try:
            # 查映射
            existing = await match_by_external_id(db, tenant_id, "wecom", consumer_id)
            if not existing or not existing.mapping:
                # 无映射，走创建流程
                await handle_consumer_created(event_type, data, tenant_id_str)
                return

            mapping = existing.mapping

            # 推送更新到企微
            await client.update_external_contact(
                mapping.external_id,
                {"remark": consumer.nickname or ""},
            )

            # 同步标签
            tags = consumer.tags or ""
            if tags:
                tag_list = [t.strip() for t in tags.split(",") if t.strip()]
                if tag_list:
                    await client.mark_external_contact(
                        mapping.external_id,
                        add_tag=tag_list,
                    )

            mapping.last_synced_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            await _sync_record(
                db,
                tenant_id,
                consumer.id,
                "update",
                mapping.external_id,
                {"status": "updated"},
            )
            await db.commit()
        except Exception:
            logger.exception("CRM push update failed for consumer %s", consumer_id)
        finally:
            await client.close()


def register_crm_event_handlers() -> None:
    """注册 CRM 同步事件处理器到事件总线。"""
    event_bus.add_handler("consumer.created", handle_consumer_created)
    event_bus.add_handler("consumer.updated", handle_consumer_updated)
    logger.info("CRM sync event handlers registered")
