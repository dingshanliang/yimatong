"""Webhook 投递 Worker。

从 Redis List 队列取出 delivery_id，执行 HTTP 投递，记录结果。
支持指数退避重试和数据清理。
同时处理外部权益发放的重试轮询。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.config import settings
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.services.takeover import poll_pending_takeover_imports
from app.services.webhook_sender import deliver, should_retry

logger = logging.getLogger(__name__)

REDIS_QUEUE_KEY = "ymt:webhook:deliver_queue"

# 重试间隔：1min → 5min → 15min
RETRY_DELAYS = [60, 300, 900]
MAX_RETRIES = 3

# 数据保留：成功 30 天，失败 90 天
SUCCESS_RETAIN_DAYS = 30
FAILED_RETAIN_DAYS = 90

# 轮询间隔（秒）
POLL_INTERVAL = 5
CLEANUP_BATCH_SIZE = 100


async def _get_db():
    """获取数据库 session。"""
    from app.core.database import async_session_factory

    return async_session_factory()


async def process_single_delivery(delivery_id: str) -> None:
    """处理单条 webhook 投递。"""
    from app.core.database import async_session_factory, bootstrap_tenant_row

    async with async_session_factory() as db:
        delivery = await bootstrap_tenant_row(
            db,
            select(WebhookDelivery).where(WebhookDelivery.id == delivery_id).with_for_update(),
        )
        if not delivery:
            logger.warning("Delivery %s not found", delivery_id)
            return

        if delivery.status == "delivered":
            return

        # 查找 endpoint
        ep_result = await db.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.id == delivery.endpoint_id,
                WebhookEndpoint.tenant_id == delivery.tenant_id,
            )
        )
        endpoint = ep_result.scalar_one_or_none()
        if not endpoint or not endpoint.enabled:
            delivery.status = "failed"
            delivery.last_response_body = "Endpoint not found or disabled"
            await db.commit()
            return

        # 发送
        status_code, response_body = await deliver(
            url=endpoint.url,
            secret=endpoint.secret,
            envelope=delivery.payload if isinstance(delivery.payload, dict) else json.loads(delivery.payload),
        )

        delivery.last_response_code = status_code
        delivery.last_response_body = response_body[:2000]
        delivery.updated_at = datetime.now(UTC)

        if 200 <= status_code < 300:
            delivery.status = "delivered"
            logger.info("Webhook delivered %s → %s (HTTP %d)", delivery_id, endpoint.url, status_code)
        elif should_retry(status_code) and delivery.retry_count < MAX_RETRIES:
            delay = RETRY_DELAYS[min(delivery.retry_count, len(RETRY_DELAYS) - 1)]
            delivery.retry_count += 1
            delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
            delivery.status = "retrying"
            logger.info(
                "Webhook %s retry %d/%d in %ds (HTTP %d)",
                delivery_id,
                delivery.retry_count,
                MAX_RETRIES,
                delay,
                status_code,
            )
        else:
            delivery.status = "failed"
            logger.warning("Webhook %s permanently failed (HTTP %d)", delivery_id, status_code)

        await db.commit()


async def poll_pending_retries() -> int:
    """查询到期的重试投递，重新入队。返回入队数量。"""
    import redis.asyncio as aioredis

    from app.core.database import async_session_factory, bootstrap_tenant_keys, set_session_tenant_context

    count = 0
    async with async_session_factory() as control_db:
        work_keys = await bootstrap_tenant_keys(
            control_db,
            select(WebhookDelivery.id, WebhookDelivery.tenant_id)
            .where(
                WebhookDelivery.status == "retrying",
                WebhookDelivery.next_retry_at <= datetime.now(UTC),
            )
            .order_by(WebhookDelivery.next_retry_at, WebhookDelivery.id)
            .limit(100),
        )

    if work_keys:
        async with aioredis.from_url(settings.redis_url) as r:
            for delivery_id, tenant_id in work_keys:
                async with async_session_factory() as db:
                    await set_session_tenant_context(db, tenant_id)
                    d = await db.scalar(
                        select(WebhookDelivery).where(
                            WebhookDelivery.id == delivery_id,
                            WebhookDelivery.tenant_id == tenant_id,
                        )
                    )
                    if d is None or d.status != "retrying" or d.next_retry_at > datetime.now(UTC):
                        continue
                    d.status = "pending"
                    await r.lpush(REDIS_QUEUE_KEY, str(d.id))
                    count += 1
                    await db.commit()

    return count


async def poll_benefit_delivery_retries() -> int:
    """查询到期的外部权益发放重试，执行重试。返回重试数量。"""
    from app.core.database import async_session_factory, bootstrap_tenant_keys, set_session_tenant_context
    from app.models.connector import BenefitDelivery, Connector
    from app.services.benefit_delivery_handler import (
        DeliveryStatus,
        _do_deliver,
        _get_circuit_breaker,
    )

    retry_backoff_base = 2

    count = 0
    now = datetime.now(UTC)
    async with async_session_factory() as control_db:
        work_keys = await bootstrap_tenant_keys(
            control_db,
            select(BenefitDelivery.id, BenefitDelivery.tenant_id)
            .where(
                BenefitDelivery.status == DeliveryStatus.PENDING,
                BenefitDelivery.retry_count < BenefitDelivery.max_retries,
                BenefitDelivery.next_retry_at <= now,
            )
            .limit(100),
        )

    for delivery_id, tenant_id in work_keys:
        async with async_session_factory() as db:
            await set_session_tenant_context(db, tenant_id)
            d = await db.scalar(
                select(BenefitDelivery).where(
                    BenefitDelivery.id == delivery_id,
                    BenefitDelivery.tenant_id == tenant_id,
                )
            )
            if d is None:
                continue
            conn_result = await db.execute(
                select(Connector).where(Connector.id == d.connector_id, Connector.tenant_id == d.tenant_id)
            )
            connector = conn_result.scalar_one_or_none()
            if not connector or not connector.enabled:
                d.status = DeliveryStatus.FAILED
                await db.commit()
                continue

            cb = _get_circuit_breaker(connector)
            if not cb.is_available():
                d.retry_count += 1
                backoff = retry_backoff_base**d.retry_count

            try:
                await _do_deliver(db, d.tenant_id, connector, d.consumer_id, d.benefit_config)
                # _do_deliver 会创建新的 delivery 记录，旧的标记完成
                d.status = DeliveryStatus.SUCCESS if d.retry_count == 0 else d.status
                count += 1
            except Exception:
                logger.exception("Benefit delivery retry %s failed", d.id)
                d.retry_count += 1
                if d.retry_count >= d.max_retries:
                    d.status = DeliveryStatus.FAILED
                    d.next_retry_at = None
                else:
                    backoff = retry_backoff_base**d.retry_count
                    d.next_retry_at = datetime.now(UTC) + timedelta(seconds=backoff)

            await db.commit()

    return count


async def poll_retrospective_generation() -> int:
    """周期性生成试点到期复盘（beads: yimatong-bgag.2，PRD §4.2）。

    跨租户幂等扫描已上线租户的第 7/14/30 天到期复盘。详见
    app.services.retrospective.generate_due_retrospectives。
    """
    from app.services.retrospective import generate_due_retrospectives

    try:
        return await generate_due_retrospectives()
    except Exception:
        logger.exception("Retrospective generation poll error")
        return 0


async def cleanup_old_deliveries() -> int:
    """Archive a bounded batch of still-terminal, still-expired deliveries."""
    from app.core.database import async_session_factory, bootstrap_tenant_keys, set_session_tenant_context

    now = datetime.now(UTC)
    async with async_session_factory() as control_db:
        # 清理成功的（> 30 天）
        success_cutoff = now - timedelta(days=SUCCESS_RETAIN_DAYS)
        success_keys = await bootstrap_tenant_keys(
            control_db,
            select(WebhookDelivery.id, WebhookDelivery.tenant_id)
            .where(WebhookDelivery.status == "delivered", WebhookDelivery.created_at < success_cutoff)
            .order_by(WebhookDelivery.created_at, WebhookDelivery.id)
            .limit(CLEANUP_BATCH_SIZE),
        )
        failed_cutoff = now - timedelta(days=FAILED_RETAIN_DAYS)
        failed_keys = await bootstrap_tenant_keys(
            control_db,
            select(WebhookDelivery.id, WebhookDelivery.tenant_id)
            .where(WebhookDelivery.status == "failed", WebhookDelivery.created_at < failed_cutoff)
            .order_by(WebhookDelivery.created_at, WebhookDelivery.id)
            .limit(CLEANUP_BATCH_SIZE),
        )

    archived = 0
    failed = 0
    # Keep the terminal branches separate: the mutation rechecks the exact
    # status and cutoff selected by the control index. A concurrent retry or
    # state recovery therefore wins and is never archived from stale keys.
    for keys, expected_status, cutoff, counter_name in (
        (success_keys, "delivered", success_cutoff, "archived"),
        (failed_keys, "failed", failed_cutoff, "failed"),
    ):
        for delivery_id, tenant_id in keys:
            async with async_session_factory() as db:
                await set_session_tenant_context(db, tenant_id)
                result = await db.execute(
                    update(WebhookDelivery)
                    .where(
                        WebhookDelivery.id == delivery_id,
                        WebhookDelivery.tenant_id == tenant_id,
                        WebhookDelivery.status == expected_status,
                        WebhookDelivery.created_at < cutoff,
                    )
                    .values(status="archived")
                    .returning(WebhookDelivery.id)
                )
                if result.scalar_one_or_none() is None:
                    await db.rollback()
                    continue
                await db.commit()
                if counter_name == "archived":
                    archived += 1
                else:
                    failed += 1

    count = archived + failed
    if count > 0:
        logger.info("Archived %d old deliveries (success=%d, failed=%d)", count, archived, failed)

    return count


async def worker_loop() -> None:
    """Worker 主循环：从 Redis 队列取任务 + 定期轮询重试。"""
    import redis.asyncio as aioredis

    logger.info("Webhook worker started")

    # 启动后台清理任务
    cleanup_counter = 0

    while True:
        try:
            async with aioredis.from_url(settings.redis_url) as r:
                # BRPOP with 1s timeout
                result = await r.brpop([REDIS_QUEUE_KEY], timeout=1)
                if result:
                    _queue_key, item_id = result
                    await process_single_delivery(item_id.decode())
        except Exception:
            logger.exception("Worker loop error, sleeping before retry")
            await asyncio.sleep(POLL_INTERVAL)

        try:
            await poll_pending_takeover_imports()
        except Exception as exc:
            logger.error(
                "Takeover import poll aborted error_code=worker_poll_failure exception_type=%s",
                type(exc).__name__,
            )

        cleanup_counter += 1
        # 每 ~60 次循环（约 1 分钟）执行一次重试轮询
        if cleanup_counter % 60 == 0:
            try:
                await poll_pending_retries()
                await poll_benefit_delivery_retries()
            except Exception:
                logger.exception("Retry poll error")

        # 每 ~3600 次循环（约 1 小时）执行一次数据清理
        if cleanup_counter % 3600 == 0:
            try:
                await cleanup_old_deliveries()
            except Exception:
                logger.exception("Cleanup error")
            # 同时触发试点到期复盘生成（beads: yimatong-bgag.2）
            try:
                await poll_retrospective_generation()
            except Exception:
                logger.exception("Retrospective generation error")


def run_worker() -> None:
    """CLI 入口：启动 worker。"""
    import asyncio

    asyncio.run(worker_loop())
