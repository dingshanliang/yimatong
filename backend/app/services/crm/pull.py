"""CRM 同步 — 定时从企微拉取联系人变更。

arq 定时任务，每小时执行一次，拉取增量变更并同步到一码通。
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connector import Connector
from app.models.integration import SyncRecord
from app.models.member import ConsumerProfile, MemberLevel
from app.services.connectors.wecom_crm import WeChatWorkCrmAdapter
from app.services.crm.conflict import resolve_consumer_conflict
from app.services.crm.matcher import create_or_update_mapping, match_by_external_id

logger = logging.getLogger(__name__)

wecom_adapter = WeChatWorkCrmAdapter()


def _wecom_tags_to_string(corp_tags: list) -> str:
    """将企微标签列表转为逗号分隔字符串。"""
    if not corp_tags:
        return ""
    return ",".join(str(t.get("tag_name", "")) for t in corp_tags if t.get("tag_name"))


def _extract_consumer_data(external_contact: dict) -> dict:
    """从企微外部联系人数据提取消费者字段。"""
    return {
        "nickname": external_contact.get("name", ""),
        "tags": _wecom_tags_to_string(external_contact.get("tag", {}).get("tag_name_list", [])),
    }


async def sync_wecom_contacts_for_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """为单个租户同步企微联系人。

    Returns:
        同步统计 {"pulled": N, "created": N, "updated": N, "errors": N}
    """
    stats = {"pulled": 0, "created": 0, "updated": 0, "errors": 0}

    # 获取企微连接器
    result = await db.execute(
        select(Connector).where(
            Connector.tenant_id == tenant_id,
            Connector.connector_type == "wecom_crm",
            Connector.enabled == True,  # noqa: E712
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        return stats

    client = await wecom_adapter.get_client(connector)
    try:
        # 拉取外部联系人列表（分页）
        cursor = ""
        while True:
            resp = await client.list_external_contacts(cursor=cursor, limit=100)
            external_userid_list = resp.get("external_userid_list", [])
            cursor = resp.get("next_cursor", "")

            for ext_id in external_userid_list:
                stats["pulled"] += 1
                try:
                    await _sync_single_contact(db, tenant_id, client, ext_id)
                except Exception:
                    logger.exception("CRM pull: failed to sync %s", ext_id)
                    stats["errors"] += 1

            if not cursor:
                break

        await db.commit()
    finally:
        await client.close()

    return stats


async def _sync_single_contact(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    client,
    external_id: str,
) -> None:
    """同步单个企微联系人到一码通。"""
    # 获取联系人详情
    contact_resp = await client.get_external_contact(external_id)
    external_contact = contact_resp.get("external_contact", {})

    # 查映射
    existing = await match_by_external_id(db, tenant_id, "wecom", external_id)

    remote_data = _extract_consumer_data(external_contact)

    if existing and existing.consumer:
        # 已有映射，执行冲突处理后更新
        consumer = existing.consumer
        local_data = {
            "nickname": consumer.nickname or "",
            "tags": consumer.tags or "",
            "member_level": consumer.member_level,
        }

        conflict_result = resolve_consumer_conflict(local_data, remote_data)
        if conflict_result.updates:
            for key, value in conflict_result.updates.items():
                if hasattr(consumer, key):
                    setattr(consumer, key, value)

            # 更新映射的 last_synced_at
            existing.mapping.last_synced_at = datetime.now(UTC)

            # 写审计
            record = SyncRecord(
                tenant_id=tenant_id,
                sync_type="crm_pull_update",
                external_id=external_id,
                data={
                    "consumer_id": str(consumer.id),
                    "updates": conflict_result.updates,
                    "conflicts": [
                        {
                            "field": c.field_name,
                            "local": c.local_value,
                            "remote": c.remote_value,
                            "resolved": c.resolved_value,
                            "source": c.source,
                        }
                        for c in conflict_result.conflicts
                    ],
                },
            )
            db.add(record)
    else:
        # 无映射，创建新消费者
        consumer = ConsumerProfile(
            tenant_id=tenant_id,
            nickname=remote_data.get("nickname", ""),
            tags=remote_data.get("tags", ""),
            member_level=MemberLevel.normal,
        )
        db.add(consumer)
        await db.flush()

        # 创建映射
        await create_or_update_mapping(
            db,
            tenant_id=tenant_id,
            consumer_id=consumer.id,
            source_system="wecom",
            external_id=external_id,
        )

        # 写审计
        record = SyncRecord(
            tenant_id=tenant_id,
            sync_type="crm_pull_create",
            external_id=external_id,
            data={"consumer_id": str(consumer.id), "source": "wecom"},
        )
        db.add(record)


async def crm_pull_job(ctx: dict) -> dict:
    """arq 定时任务入口：拉取所有租户的企微联系人变更。"""
    from app.core.database import async_session_factory

    logger.info("CRM pull job started")

    total_stats = {"tenants": 0, "pulled": 0, "created": 0, "updated": 0, "errors": 0}

    async with async_session_factory() as db:
        # 获取所有启用了企微 CRM 的租户
        result = await db.execute(
            select(Connector.tenant_id)
            .where(
                Connector.connector_type == "wecom_crm",
                Connector.enabled == True,  # noqa: E712
            )
            .distinct()
        )
        tenant_ids = [row[0] for row in result.fetchall()]

    for tenant_id in tenant_ids:
        from app.core.database import async_session_factory

        async with async_session_factory() as db:
            stats = await sync_wecom_contacts_for_tenant(db, tenant_id)
            total_stats["tenants"] += 1
            for k in ("pulled", "created", "updated", "errors"):
                total_stats[k] += stats.get(k, 0)

    logger.info("CRM pull job completed: %s", total_stats)
    return total_stats
