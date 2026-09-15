"""微盟（Weimob）优惠券连接器适配器。

Connector.config（公开）:
  - client_id: 微盟开放平台自用型应用 client_id
  - shop_id: 店铺 ID（新云 public_account_id / WOS business_operation_system_id）
  - shop_type: 店铺类型（对应 shop_id 的标识口径，见微盟 token 文档）
  - vid: 组织架构节点 ID（发券与客户导入必填，B 端店铺设置/组织架构）
  - vid_type: 节点类型（1集团/2品牌/3区域/5商场/10门店/100自提点）

Connector.secrets（加密存储，由 prepare 维护生命周期）:
  - client_secret: 应用密钥（token 获取与推送验签共用）
  - access_token / token_expires_at / token_refreshed_at: token 生命周期状态
    （自用型正式店铺 client_credentials 模式无 refresh_token，临期直接重取）

权益 benefit_config（Benefit.config_json + worker 注入）:
  - coupon_id: 微盟券模板 ID（couponTemplateId，必填）
  - phone: 消费者手机号（worker 在 consent 校验后注入；经 customer/import 换 wid）
  - idempotency_key: 幂等锚点（= claim ID，作为 coupon/receive 的 requestId）

契约基线见 docs/02_tech/INTEGRATION_PLAYBOOK.md §7；真实店铺联调为 pending_external。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select

from app.models.connector import Connector
from app.services.connectors.base import BaseConnectorAdapter, CallbackResult, DeliveryResult
from app.services.connectors.registry import register_adapter
from app.utils import weimob as weimob_protocol

logger = logging.getLogger(__name__)

# external_id = 微盟券码（codes[] 首个，回调 receiveCoupon 事件 msgBody.code 同源）
_CODE_KEYS = ("code", "couponCode", "coupon_code")
# 回调事件分类关键词（联调校准点：以真实推送 event 为准）
_CONSUME_EVENT_KEYWORDS = ("consume", "used", "verify")
_GRANT_EVENT_KEYWORDS = ("receive", "grant", "send")


def _as_int(value: Any) -> Any:
    """微盟 v2.0 数值参数（couponTemplateId/vid/vidType）按数字发送；非数字串原样透传。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            return text
    return value


class WeimobAdapter(BaseConnectorAdapter):
    """微盟优惠券适配器：client_credentials token 生命周期 + API 发券 + 推送回调解析。

    与有赞的关键差异：身份桥用手机号（customer/import 以手机号换 wid，微盟官方
    wid 合并键），不依赖 openid 互认；回调需按微盟 ACK 契约响应（callback_ack_payload）。
    """

    # 发放需要手机号定位微盟侧会员（worker 据此做 consent 校验与注入）
    requires_phone = True

    def _get_config(self, connector: Connector) -> dict:
        if connector.secrets_encrypted:
            from app.services.connectors.secrets import decrypt_secrets

            secrets = decrypt_secrets(connector.secrets_encrypted)
            return {**connector.config, **secrets}
        return connector.config

    async def prepare(self, db, connector: Connector) -> Connector:
        """发放/同步前的 token 生命周期维护（BaseConnectorAdapter.prepare 的微盟实现）。

        token 有效时原样返回；临期或失效时用独立会话重取并立即提交。行级锁 +
        双重检查防并发重取（client_credentials 无单次有效刷新令牌，并发重取只是
        浪费配额而非事故，但仍收敛到单飞）。返回带新 token 的 transient runtime
        视图，不污染调用方 ORM 对象。
        """
        from datetime import UTC, datetime

        from app.core.database import async_session_factory, set_session_tenant_context

        from .secrets import decrypt_secrets, encrypt_secrets

        cfg = self._get_config(connector)
        client_id = cfg.get("client_id")
        client_secret = cfg.get("client_secret")
        shop_id = cfg.get("shop_id")
        shop_type = cfg.get("shop_type")
        if not all([client_id, client_secret, shop_id, shop_type]):
            return connector
        if not weimob_protocol.token_expired(cfg.get("token_expires_at")) and cfg.get("access_token"):
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
            # 双重检查：等待行锁期间其他进程可能已完成重取
            if not weimob_protocol.token_expired(current.get("token_expires_at")) and current.get("access_token"):
                return self._runtime_view(connector, locked.secrets_encrypted)

            new_token = await weimob_protocol.fetch_token(
                client_id=client_id,
                client_secret=client_secret,
                shop_id=str(shop_id),
                shop_type=str(shop_type),
            )

            merged = {
                **current,
                "access_token": new_token["access_token"],
                "token_expires_at": weimob_protocol.token_expiry_iso(new_token.get("expires_in", 0)),
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
        # 微盟券模板随权益（benefit.config_json.coupon_id）维度，连接器层库存不适用；
        # 与 youzan/wecom_crm 一致返回 -1，前端隐藏库存语义。
        return -1

    async def _resolve_wid(self, cfg: dict, phone: str, idempotency_key: str) -> tuple[str | None, str]:
        """customer/import 以手机号换 wid（幂等：同手机号返回既有 wid）。

        返回 (wid, message)；wid 为 None 时 message 说明失败原因。
        传输层异常向上抛出，由 deliver 统一映射三态。
        """
        payload = await weimob_protocol.call_weimob_api(
            path=weimob_protocol.CUSTOMER_IMPORT_PATH,
            accesstoken=cfg["access_token"],
            json_body={"phone": phone, "vid": _as_int(cfg.get("vid"))},
        )
        success_list = payload.get("successList")
        if isinstance(success_list, list) and success_list:
            wid = success_list[0].get("wid") if isinstance(success_list[0], dict) else None
            if wid not in (None, ""):
                return str(wid), "ok"
        failed_list = payload.get("failedList") or payload.get("failList")
        reason = ""
        if isinstance(failed_list, list) and failed_list and isinstance(failed_list[0], dict):
            reason = str(failed_list[0].get("errMsg") or failed_list[0].get("errorMsg") or "")
        return None, reason or f"weimob_wid_unresolved phone_present={bool(phone)} key={idempotency_key}"

    async def deliver(
        self,
        connector: Connector,
        consumer_id: str,
        benefit_config: dict,
    ) -> DeliveryResult:
        """调用微盟发券 API（weimob_crm/v2.0/coupon/receive），三态映射与有赞适配器同构。

        先以手机号换 wid，再按券模板发券；external_id = 微盟券码（codes[] 首个，
        回调 receiveCoupon 事件按同源 code 精确匹配结算）。
        """
        cfg = self._get_config(connector)
        client_id = cfg.get("client_id", "")
        client_secret = cfg.get("client_secret", "")
        access_token = cfg.get("access_token", "")
        vid = cfg.get("vid", "")
        vid_type = cfg.get("vid_type", "")
        coupon_id = benefit_config.get("coupon_id", "")
        phone = benefit_config.get("phone", "")
        idempotency_key = benefit_config.get("idempotency_key", "")

        if not all([client_id, client_secret, coupon_id, phone, vid, vid_type]):
            return DeliveryResult(
                status="failed",
                message=(
                    f"Missing required inputs: client_id={bool(client_id)}, "
                    f"client_secret={bool(client_secret)}, coupon_id={bool(coupon_id)}, "
                    f"phone={bool(phone)}, vid={bool(vid)}, vid_type={bool(vid_type)}"
                ),
            )
        if not access_token or weimob_protocol.token_expired(cfg.get("token_expires_at")):
            return DeliveryResult(
                status="pending",
                external_id=idempotency_key or None,
                message="token_refresh_required",
            )

        try:
            wid, wid_message = await self._resolve_wid(cfg, phone, idempotency_key)
            if wid is None:
                return DeliveryResult(
                    status="failed",
                    external_id=idempotency_key or None,
                    message=f"wid resolution failed: {wid_message}",
                )

            payload = await weimob_protocol.call_weimob_api(
                path=weimob_protocol.COUPON_RECEIVE_PATH,
                accesstoken=access_token,
                json_body={
                    "couponNums": [
                        {
                            "couponTemplateId": _as_int(coupon_id),
                            "num": 1,
                            "requestId": idempotency_key,
                        }
                    ],
                    "wid": _as_int(wid),
                    "scene": weimob_protocol.API_SCENE,
                    "vid": _as_int(vid),
                    "vidType": _as_int(vid_type),
                },
            )
        except weimob_protocol.WeimobAPIError as exc:
            if exc.status_code >= 500 or exc.status_code == 429:
                return DeliveryResult(
                    status="pending",
                    external_id=idempotency_key or None,
                    external_data={"status_code": exc.status_code, "reason": "provider_retryable"},
                    message=f"Retryable weimob error: {exc.code}",
                )
            return DeliveryResult(
                status="failed",
                external_id=idempotency_key or None,
                message=f"Weimob business error: code={exc.code} message={exc.message}",
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
            logger.exception("Weimob deliver error")
            return DeliveryResult(
                status="failed",
                external_id=idempotency_key or None,
                message=f"Exception: {exc}",
            )

        result_list = payload.get("couponResultList")
        results = result_list if isinstance(result_list, list) else []
        first = results[0] if results and isinstance(results[0], dict) else {}
        codes = [str(code) for code in (first.get("codes") or []) if code not in (None, "")]
        if first and first.get("isSuccess") is False:
            return DeliveryResult(
                status="failed",
                external_id=idempotency_key or None,
                message=f"Weimob coupon rejected: code={first.get('errCode')} message={first.get('errMsg')}",
            )
        external_id = next(iter(codes), idempotency_key)
        return DeliveryResult(
            status="success",
            external_id=external_id,
            external_data={"codes": codes, "wid": str(first.get("wid") or wid)},
            message="Coupon issued",
        )

    async def reconcile(self, connector: Connector, external_id: str) -> DeliveryResult:
        """回调超时后的对账：coupon/getList 按券码确认发放结果。

        查到记录 → success（发放已落地）；明确空 → failed（触发 outbox 重发）；
        查询失败或结构不明 → pending（保持等待，不盲目重发）。
        """
        cfg = self._get_config(connector)
        access_token = cfg.get("access_token", "")
        if not access_token or weimob_protocol.token_expired(cfg.get("token_expires_at")):
            return DeliveryResult(status="pending", external_id=external_id, message="token_refresh_required")

        try:
            payload = await weimob_protocol.call_weimob_api(
                path=weimob_protocol.COUPON_GETLIST_PATH,
                accesstoken=access_token,
                json_body={"codes": [external_id]},
            )
        except weimob_protocol.WeimobAPIError as exc:
            return DeliveryResult(
                status="pending" if (exc.status_code >= 500 or exc.status_code == 429) else "failed",
                external_id=external_id,
                message=f"Weimob reconcile error: code={exc.code}",
            )
        except httpx.HTTPError as exc:
            return DeliveryResult(status="pending", external_id=external_id, message=f"Transport error: {exc}")

        records = None
        for key in ("couponList", "coupons", "list", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                records = value
                break
        if records is None and isinstance(payload.get("data"), list):
            records = payload["data"]
        if isinstance(records, list) and records:
            return DeliveryResult(status="success", external_id=external_id, message="Reconciled: grant confirmed")
        if isinstance(records, list) and records == []:
            return DeliveryResult(status="failed", external_id=external_id, message="Reconciled: grant not found")
        # 响应结构无法判断（联调校准点）：宁可等待也不误重发
        return DeliveryResult(status="pending", external_id=external_id, message="Reconcile inconclusive")

    async def parse_callback(
        self,
        connector: Connector,
        request_body: bytes,
        headers: dict,
    ) -> CallbackResult:
        """解析微盟消息推送（信封 {id, topic, event, sign, msgBody}）。

        receiveCoupon 等发放事件 → 返回与 deliver 一致的券码 external_id + success；
        consumeCoupon 等核销事件 → status=ignored + coupon_transition=external_consume；
        其余事件 → status=ignored（端点按微盟 ACK 契约 200，避免平台重推 5 次）。
        """
        msg, _sign = weimob_protocol.parse_push_payload(request_body)
        if msg is None:
            return CallbackResult(status="ignored")
        topic, event = weimob_protocol.push_event_fields(msg)
        data = weimob_protocol.push_event_data(msg)
        lowered = f"{topic} {event}".lower()

        if any(k in lowered for k in _CONSUME_EVENT_KEYWORDS) and "coupon" in lowered:
            coupon_ref = next((str(data[key]) for key in _CODE_KEYS if data.get(key)), "")
            if not coupon_ref:
                # 无法定位券实例，只能忽略并留痕
                return CallbackResult(status="ignored", external_data={"event": event})
            return CallbackResult(
                status="ignored",
                coupon_transition="external_consume",
                external_coupon_ref=coupon_ref,
                external_data={"event": event},
            )

        if any(k in lowered for k in _GRANT_EVENT_KEYWORDS):
            external_id = next((str(data[key]) for key in _CODE_KEYS if data.get(key) not in (None, "")), "")
            if not external_id:
                return CallbackResult(status="ignored", external_data={"event": event})
            status_value = str(data.get("status") or data.get("state") or "").lower()
            return CallbackResult(
                external_id=external_id,
                status="success" if status_value in {"", "success", "succeeded", "ok"} else "failed",
                external_data={"event": event},
            )

        return CallbackResult(status="ignored", external_data={"event": event})

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        required = ["client_id", "client_secret", "shop_id", "shop_type", "vid", "vid_type"]
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
        client_id = cfg.get("client_id", "")
        client_secret = cfg.get("client_secret", "")
        if not client_id or not client_secret:
            return False
        msg, sign = weimob_protocol.parse_push_payload(request_body)
        if msg is None or not sign:
            return False
        return weimob_protocol.verify_push_signature(
            client_id=client_id,
            client_secret=client_secret,
            msg_id=msg.get("id"),
            msg_body=msg.get("msgBody", msg.get("msg_body")),
            sign=sign,
        )

    async def callback_ack_payload(self, callback_result: CallbackResult) -> dict | None:
        """微盟消息订阅要求回调 2 秒内返回 {"code":{"errcode":0,...}}，否则重推 5 次。"""
        return {"code": {"errcode": 0, "errmsg": "success"}}

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
        """用 client_credentials 凭证模式验证可用性（POST /connectors/{id}/test 接线）。"""
        client_id = config.get("client_id", "")
        client_secret = config.get("client_secret", "")
        shop_id = config.get("shop_id", "")
        shop_type = config.get("shop_type", "")
        if not all([client_id, client_secret, shop_id, shop_type]):
            return False, "Missing client_id/client_secret/shop_id/shop_type"
        try:
            token = await weimob_protocol.fetch_token(
                client_id=client_id,
                client_secret=client_secret,
                shop_id=str(shop_id),
                shop_type=str(shop_type),
            )
        except (weimob_protocol.WeimobAPIError, httpx.HTTPError) as exc:
            return False, f"Weimob credential check failed: {exc}"
        return bool(token.get("access_token")), "ok" if token.get("access_token") else "no access_token"


register_adapter("weimob", WeimobAdapter)
