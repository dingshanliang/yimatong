"""有赞（Youzan）优惠券连接器适配器。

Connector.config（公开）:
  - client_id: 有赞开放平台自用型应用 client_id
  - shop_alias: 店铺标识（可选，仅展示）

Connector.secrets（加密存储，由 prepare 维护生命周期）:
  - client_secret: 应用密钥（API 签名与推送验签共用）
  - access_token / refresh_token / token_expires_at: token 生命周期状态

权益 benefit_config（Benefit.config_json + worker 注入）:
  - coupon_id: 有赞券模板 ID（必填）
  - openid: 消费者微信 openid（worker 在 consent 校验后注入，必填）
  - idempotency_key: 幂等锚点（= claim ID，worker 注入）

契约基线见 docs/02_tech/INTEGRATION_PLAYBOOK.md §6；真实店铺联调为 pending_external。
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select

from app.models.connector import Connector
from app.services.connectors.base import BaseConnectorAdapter, CallbackResult, DeliveryResult
from app.services.connectors.registry import register_adapter
from app.utils import youzan as youzan_protocol

logger = logging.getLogger(__name__)

# external_id 候选字段（联调校准点：以真实响应为准）
_GRANT_ID_KEYS = ("coupon_take_id", "record_id", "grant_id", "id")
# 回调事件分类关键词（联调校准点：以真实推送 topic 为准）
_CONSUME_EVENT_KEYWORDS = ("consume", "used", "verify")
_GRANT_EVENT_KEYWORDS = ("take", "grant", "send")


class YouzanAdapter(BaseConnectorAdapter):
    """有赞优惠券适配器：OAuth token 生命周期 + API 发券 + 推送回调解析。"""

    # 发放需要 openid 定位有赞侧用户（worker 据此做 consent 校验与注入）
    requires_openid = True

    def _get_config(self, connector: Connector) -> dict:
        if connector.secrets_encrypted:
            from app.services.connectors.secrets import decrypt_secrets

            secrets = decrypt_secrets(connector.secrets_encrypted)
            return {**connector.config, **secrets}
        return connector.config

    async def prepare(self, db, connector: Connector) -> Connector:
        """发放/同步前的 token 生命周期维护（BaseConnectorAdapter.prepare 的有赞实现）。

        token 有效时原样返回；临期或失效时用独立会话刷新并立即提交，防止并发双刷
        烧掉单次有效的 refresh_token；刷新失败回落自用型静默重取。
        返回带新 token 的 transient runtime 视图（不污染调用方 ORM 对象，避免主事务
        把内存态密文回写覆盖并发刷新结果）；无需刷新时返回原对象。
        """
        from datetime import UTC, datetime

        from app.core.database import async_session_factory, set_session_tenant_context

        from .secrets import decrypt_secrets, encrypt_secrets

        cfg = self._get_config(connector)
        client_id = cfg.get("client_id")
        client_secret = cfg.get("client_secret")
        if not client_id or not client_secret:
            return connector
        if not youzan_protocol.token_expired(cfg.get("token_expires_at")) and cfg.get("access_token"):
            return connector

        async with async_session_factory() as session:
            # 独立会话必须显式设置租户上下文，否则生产 RLS 下查不到连接器行
            await set_session_tenant_context(session, connector.tenant_id)
            locked = (
                await session.execute(
                    select(Connector)
                    .where(
                        Connector.id == connector.id,
                        Connector.tenant_id == connector.tenant_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if locked is None:
                return connector
            current = decrypt_secrets(locked.secrets_encrypted) if locked.secrets_encrypted else {}
            # 双重检查：等待行锁期间其他进程可能已完成刷新
            if not youzan_protocol.token_expired(current.get("token_expires_at")) and current.get("access_token"):
                return self._runtime_view(connector, locked.secrets_encrypted)

            new_token = None
            refresh_token = current.get("refresh_token") or None
            if refresh_token:
                try:
                    new_token = await youzan_protocol.fetch_token(
                        client_id=client_id,
                        client_secret=client_secret,
                        grant_type=youzan_protocol.GRANT_TYPE_REFRESH,
                        refresh_token=refresh_token,
                    )
                except (youzan_protocol.YouzanAPIError, httpx.HTTPError) as exc:
                    logger.warning("Youzan token refresh failed, falling back to silent grant: %s", exc)
            if new_token is None:
                new_token = await youzan_protocol.fetch_token(
                    client_id=client_id,
                    client_secret=client_secret,
                    grant_type=youzan_protocol.GRANT_TYPE_SILENT,
                )

            merged = {
                **current,
                "access_token": new_token["access_token"],
                # 静默重取不返回 refresh_token 时，旧值必然已烧毁或无效，清空避免回写死值
                "refresh_token": new_token.get("refresh_token") or "",
                "token_expires_at": youzan_protocol.token_expiry_iso(new_token.get("expires_in", 0)),
                "token_refreshed_at": datetime.now(UTC).isoformat(),
            }
            locked.secrets_encrypted = encrypt_secrets(merged)
            await session.commit()
            return self._runtime_view(connector, locked.secrets_encrypted)

    @staticmethod
    def _runtime_view(connector: Connector, secrets_blob: bytes) -> Connector:
        """构造带最新 secrets 的 transient 视图，供调用方继续合并 runtime 配置。"""
        from .secrets import decrypt_secrets, public_connector_config

        return Connector(
            id=connector.id,
            tenant_id=connector.tenant_id,
            name=connector.name,
            connector_type=connector.connector_type,
            config={**public_connector_config(connector.config), **decrypt_secrets(secrets_blob)},
            secrets_encrypted=secrets_blob,
            enabled=connector.enabled,
        )

    async def sync_stock(self, connector: Connector) -> int:
        # 有赞券模板随权益（benefit.config_json.coupon_id）维度，连接器层库存不适用；
        # 与 wecom_crm 一致返回 -1，前端隐藏库存语义。
        return -1

    async def deliver(
        self,
        connector: Connector,
        consumer_id: str,
        benefit_config: dict,
    ) -> DeliveryResult:
        """调用有赞发券 API（youzan.ump.coupon.take），三态映射与微信支付适配器同构。"""
        cfg = self._get_config(connector)
        client_id = cfg.get("client_id", "")
        client_secret = cfg.get("client_secret", "")
        access_token = cfg.get("access_token", "")
        coupon_id = benefit_config.get("coupon_id", "")
        openid = benefit_config.get("openid", "")
        idempotency_key = benefit_config.get("idempotency_key", "")

        if not all([client_id, client_secret, coupon_id, openid]):
            return DeliveryResult(
                status="failed",
                message=(
                    f"Missing required inputs: client_id={bool(client_id)}, "
                    f"client_secret={bool(client_secret)}, coupon_id={bool(coupon_id)}, openid={bool(openid)}"
                ),
            )
        if not access_token or youzan_protocol.token_expired(cfg.get("token_expires_at")):
            return DeliveryResult(
                status="pending",
                external_id=idempotency_key or None,
                message="token_refresh_required",
            )

        params: dict = {"coupon_id": coupon_id, "openid": openid}
        if idempotency_key:
            # 有赞侧无标准外部幂等字段，outer_id 作为我们侧对账锚点（联调校准点）
            params["outer_id"] = idempotency_key

        try:
            payload = await youzan_protocol.call_youzan_api(
                method=youzan_protocol.COUPON_TAKE_METHOD,
                params=params,
                client_id=client_id,
                client_secret=client_secret,
                access_token=access_token,
            )
        except youzan_protocol.YouzanAPIError as exc:
            if exc.status_code >= 500 or exc.status_code == 429:
                return DeliveryResult(
                    status="pending",
                    external_id=idempotency_key or None,
                    external_data={"status_code": exc.status_code, "reason": "provider_retryable"},
                    message=f"Retryable youzan error: {exc.code}",
                )
            return DeliveryResult(
                status="failed",
                external_id=idempotency_key or None,
                message=f"Youzan business error: code={exc.code} message={exc.message}",
            )
        except httpx.TimeoutException:
            return DeliveryResult(
                status="pending",
                external_id=idempotency_key or None,
                external_data={"reason": "ambiguous_provider_outcome"},
                message="Request timeout, need to reconcile",
            )
        except httpx.HTTPError as exc:
            return DeliveryResult(
                status="pending",
                external_id=idempotency_key or None,
                external_data={"reason": "ambiguous_provider_outcome"},
                message=f"Transport error: {exc}",
            )
        except Exception as exc:
            logger.exception("Youzan deliver error")
            return DeliveryResult(
                status="failed",
                external_id=idempotency_key or None,
                message=f"Exception: {exc}",
            )

        external_id = next(
            (str(payload[key]) for key in _GRANT_ID_KEYS if payload.get(key) not in (None, "")),
            idempotency_key,
        )
        return DeliveryResult(
            status="success",
            external_id=external_id,
            external_data={},
            message="Coupon issued",
        )

    async def reconcile(self, connector: Connector, external_id: str) -> DeliveryResult:
        """回调超时后的对账：查有赞核销/领取记录确认发放结果。

        查到记录 → success（发放已落地）；明确不存在 → failed（触发 outbox 重发）；
        查询本身失败 → pending（保持等待，不盲目重发）。
        """
        cfg = self._get_config(connector)
        client_id = cfg.get("client_id", "")
        client_secret = cfg.get("client_secret", "")
        access_token = cfg.get("access_token", "")
        if not all([client_id, client_secret, access_token]):
            return DeliveryResult(status="pending", external_id=external_id, message="token_refresh_required")

        try:
            payload = await youzan_protocol.call_youzan_api(
                method=youzan_protocol.COUPON_VERIFY_LOGS_METHOD,
                params={"outer_id": external_id, "record_id": external_id},
                client_id=client_id,
                client_secret=client_secret,
                access_token=access_token,
            )
        except youzan_protocol.YouzanAPIError as exc:
            return DeliveryResult(
                status="pending" if (exc.status_code >= 500 or exc.status_code == 429) else "failed",
                external_id=external_id,
                message=f"Youzan reconcile error: code={exc.code}",
            )
        except httpx.HTTPError as exc:
            return DeliveryResult(status="pending", external_id=external_id, message=f"Transport error: {exc}")

        records = payload.get("records") if isinstance(payload, dict) else None
        if isinstance(records, list) and records:
            return DeliveryResult(status="success", external_id=external_id, message="Reconciled: grant confirmed")
        empty = isinstance(payload, dict) and (records == [] or payload.get("total") == 0)
        if empty:
            return DeliveryResult(status="failed", external_id=external_id, message="Reconciled: grant not found")
        # 响应结构无法判断（联调校准点）：宁可等待也不误重发
        return DeliveryResult(status="pending", external_id=external_id, message="Reconcile inconclusive")

    async def parse_callback(
        self,
        connector: Connector,
        request_body: bytes,
        headers: dict,
    ) -> CallbackResult:
        """解析有赞消息推送。

        发放结算类事件 → 返回与 deliver 一致的 external_id + success/failed；
        核销类事件 → status=ignored + coupon_transition=external_consume（钱包状态回流）；
        其余事件 → status=ignored（端点直接 200，避免有赞永久重推）。
        """
        msg, raw_msg, _sign = youzan_protocol.parse_push_payload(request_body)
        if msg is None:
            return CallbackResult(status="ignored")
        event_type = youzan_protocol.push_event_type(msg)
        data = youzan_protocol.push_event_data(msg)
        lowered = event_type.lower()

        if any(k in lowered for k in _CONSUME_EVENT_KEYWORDS) and "coupon" in lowered:
            coupon_ref = next(
                (str(data[key]) for key in ("coupon_no", "code", "coupon_code", "record_id") if data.get(key)),
                "",
            )
            if not coupon_ref:
                # 无法定位券实例，只能忽略并留痕
                return CallbackResult(status="ignored", external_data={"event": event_type})
            return CallbackResult(
                status="ignored",
                coupon_transition="external_consume",
                external_coupon_ref=coupon_ref,
                external_data={"event": event_type},
            )

        if any(k in lowered for k in _GRANT_EVENT_KEYWORDS):
            external_id = next(
                (str(data[key]) for key in _GRANT_ID_KEYS if data.get(key) not in (None, "")),
                "",
            )
            if not external_id:
                return CallbackResult(status="ignored", external_data={"event": event_type})
            status_value = str(data.get("status") or data.get("state") or "").lower()
            return CallbackResult(
                external_id=external_id,
                status="success" if status_value in {"success", "succeeded", "ok"} else "failed",
                external_data={"event": event_type},
            )

        return CallbackResult(status="ignored", external_data={"event": event_type})

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        required = ["client_id", "client_secret"]
        missing = [k for k in required if not config.get(k)]
        if missing:
            return False, f"Missing required fields: {', '.join(missing)}"
        client_id = str(config["client_id"])
        if len(client_id) > 64:
            return False, "client_id too long (max 64)"
        return True, ""

    async def verify_callback(
        self,
        connector: Connector,
        request_body: bytes,
        headers: dict,
    ) -> bool:
        cfg = self._get_config(connector)
        client_secret = cfg.get("client_secret", "")
        if not client_secret:
            return False
        msg, raw_msg, sign = youzan_protocol.parse_push_payload(request_body)
        if msg is None or not sign:
            return False
        return youzan_protocol.verify_push_signature(raw_msg, sign, client_secret)

    async def on_delivery_success(
        self,
        db,
        *,
        tenant_id,
        claim_id,
        delivery_id,
        external_id: str,
        consumer_id: str,
        benefit_config: dict,
    ) -> None:
        """发放结算成功后确认外部券钱包同步（pending → synchronized）。

        无对应钱包资产时为 no-op；本钩子由 worker 包裹异常隔离，确认失败绝不
        回滚发放结算（券停留 pending，复购工作台可见并阻断核销）。
        """
        from app.services.external_coupon_wallet import confirm_external_coupon_sync

        await confirm_external_coupon_sync(db, tenant_id=tenant_id, claim_id=claim_id, external_id=external_id)

    async def test_connection(self, config: dict) -> tuple[bool, str]:
        """用自用型静默授权验证凭证可用性（POST /connectors/{id}/test 接线）。"""
        client_id = config.get("client_id", "")
        client_secret = config.get("client_secret", "")
        if not client_id or not client_secret:
            return False, "Missing client_id/client_secret"
        try:
            token = await youzan_protocol.fetch_token(
                client_id=client_id,
                client_secret=client_secret,
                grant_type=youzan_protocol.GRANT_TYPE_SILENT,
            )
        except (youzan_protocol.YouzanAPIError, httpx.HTTPError) as exc:
            return False, f"Youzan credential check failed: {exc}"
        return bool(token.get("access_token")), "ok" if token.get("access_token") else "no access_token"


register_adapter("youzan", YouzanAdapter)
