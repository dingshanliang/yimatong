"""权益领取端点（H5 前端使用，scan_token 鉴权）"""

import hashlib
import hmac
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, lock_active_tenant_context
from app.middleware.rate_limit import rate_limiter
from app.schemas.benefit_claim import BenefitClaimRequest
from app.services.scan_token import require_launch_claim_authority, verify_scan_token
from app.utils.client_ip import compute_ip_hash, get_client_ip

benefit_claim_router = APIRouter(prefix="/api/v1", tags=["benefit-claims"])


def _rate_limit_identity(client_ip: str) -> str:
    key = (settings.hmac_pepper or settings.secret_key).encode()
    return hmac.new(key, client_ip.encode(), hashlib.sha256).hexdigest()


def _claim_scan_token(request: Request, body: BenefitClaimRequest) -> str | None:
    """Keep Authorization precedence consistent with the repository auth boundary."""

    auth_header = request.headers.get("Authorization", "")
    return auth_header[7:] if auth_header.startswith("Bearer ") else body.scan_token


@benefit_claim_router.post("/benefit-claims", status_code=201)
async def claim_benefit_h5(
    request: Request,
    body: BenefitClaimRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """H5 端权益领取（scan_token 鉴权，无需 admin token）"""
    # 0. IP 级速率限制
    client_ip = get_client_ip(request)
    rate_result = await rate_limiter.check(f"claim:{_rate_limit_identity(client_ip)}", 20, 60)
    if not rate_result.allowed:
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后再试",
            headers={"Retry-After": str(rate_result.retry_after)},
        )

    # 1. 验证 scan_token
    token = _claim_scan_token(request, body)

    if not token:
        raise HTTPException(status_code=401, detail="scan_token required")

    ip_hash = compute_ip_hash(client_ip)
    payload = verify_scan_token(token, expected_ip_hash=ip_hash)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid token")

    # 从 scan_token payload 中提取 tenant_id，确保只能领取同租户的权益
    token_tenant_id = payload.get("tenant_id")
    if not token_tenant_id:
        raise HTTPException(status_code=401, detail="invalid token: missing tenant_id")
    try:
        tid = uuid.UUID(str(token_tenant_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="invalid token: corrupt tenant_id")

    # scan_token 是该公开端点的可信租户来源。业务查询前在同一事务中建立
    # PostgreSQL RLS 上下文并锁定租户套餐行，锁保持到领取/外部发放 commit。
    # 套餐门先于 launch authority 校验：过期租户应得到明确的 403 套餐提示，
    # 而不是 v1 合成 token 缺 launch 字段导致的 401「请重新扫码」
    from app.services.entitlement import (
        PLAN_EXPIRED_CODE,
        PLAN_EXPIRED_DETAIL,
        TenantPlanExpiredError,
    )

    try:
        tid = await lock_active_tenant_context(db, tid)
    except TenantPlanExpiredError:
        return JSONResponse(
            status_code=403,
            content={"code": PLAN_EXPIRED_CODE, "detail": PLAN_EXPIRED_DETAIL},
        )

    try:
        launch_authority = require_launch_claim_authority(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=401,
            detail={"code": "scan_token_unbound", "message": "请重新扫码后领取权益"},
        ) from exc

    # 2. 查找权益（带租户隔离：只能领取 scan_token 所属租户的权益）
    from app.models.campaign import Benefit

    benefit_id = body.benefit_id  # Pydantic 已验证为 UUID

    # A scan token proves a previous scan, not current eligibility. Rebuild the
    # code -> code batch -> production batch chain under the locked tenant.
    from app.models.code import CodeBatch, CodeItem, CodeItemStatus
    from app.models.product import ProductionBatch
    from app.services.product import is_production_batch_effectively_active

    token_public_id = payload.get("public_id")
    if not isinstance(token_public_id, str):
        raise HTTPException(status_code=401, detail="invalid token: missing public_id")
    locator_result = await db.execute(
        select(CodeItem.id, CodeItem.code_batch_id, CodeBatch.production_batch_id)
        .join(
            CodeBatch,
            (CodeBatch.id == CodeItem.code_batch_id) & (CodeBatch.tenant_id == CodeItem.tenant_id),
        )
        .where(
            CodeItem.public_id == token_public_id,
            CodeItem.tenant_id == tid,
        )
    )
    locator = locator_result.one_or_none()
    if locator is None:
        raise HTTPException(
            status_code=403,
            detail={"code": "production_batch_unavailable", "message": "该码对应生产批次当前不可领取权益"},
        )
    code_item_id, code_batch_id, production_batch_id = locator

    production_batch = await db.scalar(
        select(ProductionBatch).where(
            ProductionBatch.id == production_batch_id,
            ProductionBatch.tenant_id == tid,
        )
    )
    code_batch = await db.scalar(
        select(CodeBatch).where(
            CodeBatch.id == code_batch_id,
            CodeBatch.tenant_id == tid,
        )
    )
    live_code = await db.scalar(
        select(CodeItem).where(
            CodeItem.id == code_item_id,
            CodeItem.tenant_id == tid,
        )
    )
    chain_is_available = (
        production_batch is not None
        and code_batch is not None
        and live_code is not None
        and code_batch.production_batch_id == production_batch.id
        and code_batch.product_id == production_batch.product_id
        and code_batch.sku_id == production_batch.sku_id
        and live_code.code_batch_id == code_batch.id
        and live_code.public_id == token_public_id
        and live_code.status in (CodeItemStatus.activated, CodeItemStatus.bound)
        and is_production_batch_effectively_active(production_batch)
    )
    if not chain_is_available:
        raise HTTPException(
            status_code=403,
            detail={"code": "production_batch_unavailable", "message": "该码对应生产批次当前不可领取权益"},
        )
    if code_batch.id != launch_authority.code_batch_id:
        return JSONResponse(
            status_code=409,
            content={"detail": {"code": "launch_release_not_current", "message": "上线内容已变化，请重新扫码后领取"}},
        )
    from app.services.launch import resolve_current_launch_release

    current_release = await resolve_current_launch_release(db, tid, token_public_id)
    if current_release is None or any(
        (
            uuid.UUID(str(current_release["release_id"])) != launch_authority.launch_release_id,
            uuid.UUID(str(current_release["campaign_id"])) != launch_authority.campaign_id,
            uuid.UUID(str(current_release["code_batch_id"])) != launch_authority.code_batch_id,
            str(current_release["content_digest"]) != launch_authority.content_digest,
        )
    ):
        return JSONResponse(
            status_code=409,
            content={"detail": {"code": "launch_release_not_current", "message": "上线内容已变化，请重新扫码后领取"}},
        )

    result = await db.execute(select(Benefit).where(Benefit.id == benefit_id, Benefit.tenant_id == tid))
    benefit = result.scalar_one_or_none()
    if not benefit:
        raise HTTPException(status_code=404, detail="benefit not found")
    if benefit.campaign_id != launch_authority.campaign_id:
        return JSONResponse(
            status_code=409,
            content={"detail": {"code": "benefit_not_in_launch_release", "message": "该权益不属于本次上线活动"}},
        )

    # 3. 权益状态检查（对所有类型生效，包括红包）
    if benefit.status != "active":
        raise HTTPException(status_code=409, detail="权益已停用")

    from app.services.benefit_claim_admission import build_claim_consumer_id, build_claim_idempotency_key
    from app.services.benefit_claim_eligibility import ClaimEligibilityError, validate_claim_eligibility

    try:
        idempotency_key = build_claim_idempotency_key(payload, benefit_id)
        consumer_id = build_claim_consumer_id(payload)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid scan authority") from exc

    try:
        campaign = await validate_claim_eligibility(
            db,
            tenant_id=tid,
            benefit=benefit,
            scanned_product_id=code_batch.product_id,
            public_id=token_public_id,
            scan_event_id=uuid.UUID(payload["scan_event_id"]),
            consumer_id=consumer_id,
        )
    except (ClaimEligibilityError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="活动或权益当前不可领取") from exc

    # A platform coupon becomes a durable member asset only when a published
    # authoritative rule is explicitly linked to this benefit. Preflight the
    # active membership before claim admission so inventory is never consumed
    # without a recoverable wallet asset.
    coupon_rule = None
    coupon_membership = None
    if benefit.benefit_type == "platform_coupon":
        from app.models.repurchase_coupon import RepurchaseCouponRuleVersion
        from app.services.brand_membership import get_brand_membership_for_profile

        coupon_rule = await db.scalar(
            select(RepurchaseCouponRuleVersion).where(
                RepurchaseCouponRuleVersion.tenant_id == tid,
                RepurchaseCouponRuleVersion.benefit_id == benefit.id,
            )
        )
        if coupon_rule is not None:
            if coupon_rule.status != "published":
                raise HTTPException(status_code=409, detail="coupon_rule_not_issuable")
            try:
                coupon_consumer_id = uuid.UUID(str(consumer_id))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=409, detail="active_membership_required") from exc
            coupon_membership = await get_brand_membership_for_profile(db, tid, coupon_consumer_id)
            if coupon_membership is None:
                raise HTTPException(status_code=409, detail="active_membership_required")

    # 5. 红包类权益只解析 OAuth/connector 前置条件。实际扣减与发放同样先走
    # claim+outbox，HTTP 请求内绝不调用外部支付。
    if benefit.benefit_type == "cash_red_packet":
        cash_context = await _prepare_cash_red_packet_claim(benefit, token, payload, db)
        if cash_context.get("status") == "require_wechat_auth":
            return cash_context
        consumer_id = cash_context["consumer_id"]

    # 6. 企业微信添加门槛：只以后端收到的企业微信事件为准
    from app.services.wecom_integration import (
        WeComIntegrationError,
        get_or_create_claim_contact_way,
        has_confirmed_wecom_contact,
        is_wecom_required,
    )

    if campaign and is_wecom_required(campaign.rules_json):
        if not await has_confirmed_wecom_contact(
            db,
            tenant_id=benefit.tenant_id,
            benefit_id=benefit.id,
            scan_token=token,
        ):
            try:
                contact_way = await get_or_create_claim_contact_way(
                    db,
                    tenant_id=benefit.tenant_id,
                    benefit=benefit,
                    scan_token=token,
                )
            except WeComIntegrationError as exc:
                raise HTTPException(
                    status_code=403,
                    detail={"code": "require_wecom_contact", "message": str(exc)},
                ) from exc
            return JSONResponse(
                status_code=403,
                content={
                    "code": "require_wecom_contact",
                    "detail": {
                        "code": "require_wecom_contact",
                        "message": "请先添加企业微信，再继续领取权益",
                        "qr_code": contact_way.qr_code,
                        "state": contact_way.state,
                    },
                },
            )

    # 7. 如果需要手机号，先提示补全，避免提前占用幂等 key
    if benefit.config_json.get("require_phone") and not body.phone:
        raise HTTPException(
            status_code=403,
            detail={"code": "require_auth", "message": "需要授权手机号"},
        )

    from app.services.campaign import claim_benefit

    # yimatong-zgb1.7：透传 public_id 用于风险门禁（scan_token payload 已校验 public_id）
    claim_public_id = payload.get("public_id") if isinstance(payload.get("public_id"), str) else None
    result = await claim_benefit(
        db,
        benefit.tenant_id,
        benefit_id,
        consumer_id,
        idempotency_key,
        public_id=claim_public_id,
        scan_event_id=uuid.UUID(payload["scan_event_id"]),
        scanned_product_id=code_batch.product_id,
        launch_authority=launch_authority,
    )
    outcome = result.get("outcome", result.get("status"))
    if _is_successful_claim_outcome(outcome):
        from app.services.benefit_claim_status import build_claim_success_payload

        response = await build_claim_success_payload(db, benefit, result, consumer_id)
        if coupon_rule is not None and coupon_membership is not None:
            from app.services.consent import require_consumer_scan_authority
            from app.services.repurchase_coupon import issue_member_coupon

            scan_authority = require_consumer_scan_authority(payload)
            claim_id = uuid.UUID(str(result.get("claim_id") or result.get("claim", {}).get("id")))
            coupon = await issue_member_coupon(
                db,
                tenant_id=tid,
                membership_id=uuid.UUID(str(coupon_membership["membership_id"])),
                rule_version_id=coupon_rule.id,
                source_claim_id=claim_id,
                source_scan_event_id=scan_authority.scan_event_id,
                source_scan_time=scan_authority.scan_time,
                source_public_id=scan_authority.public_id,
                idempotency_key=f"coupon-issue:{claim_id}",
                actor_type="consumer",
                actor_id=scan_authority.consumer_id,
            )
            response["coupon"] = {
                "id": str(coupon.id),
                "coupon_number": coupon.coupon_number,
                "status": coupon.status,
                "valid_from": coupon.valid_from.isoformat(),
                "valid_until": coupon.valid_until.isoformat(),
            }
        return response
    if outcome == "risk_paused":
        # yimatong-zgb1.7 AC3：风险状态下服务端阻断权益领取
        raise HTTPException(
            status_code=403,
            detail={"code": "risk_paused", "message": result.get("message", "该码存在风险信号，权益领取暂时暂停")},
        )
    if outcome == "inactive":
        raise HTTPException(status_code=409, detail="权益已停用")
    if outcome == "campaign_inactive":
        raise HTTPException(status_code=409, detail="活动已结束")
    if outcome == "out_of_stock":
        raise HTTPException(status_code=410, detail="权益已抢光")
    if outcome == "limit_reached":
        raise HTTPException(status_code=403, detail="您已达到本次活动领取上限")
    if outcome == "benefit_not_in_launch_release":
        return JSONResponse(
            status_code=409,
            content={"detail": {"code": "benefit_not_in_launch_release", "message": "该权益不属于本次上线活动"}},
        )
    if outcome == "launch_release_not_current":
        return JSONResponse(
            status_code=409,
            content={"detail": {"code": "launch_release_not_current", "message": "上线内容已变化，请重新扫码后领取"}},
        )
    raise HTTPException(status_code=404, detail="benefit not found")


def _is_successful_claim_outcome(outcome: object) -> bool:
    """Treat an authoritative replay as the same successful claim, without re-mutating inventory."""

    return outcome in {"idempotent", "replayed", "success"}


async def _prepare_cash_red_packet_claim(
    benefit,
    token: str,
    payload: dict,
    db: AsyncSession,
):
    """处理现金红包领取。

    如果消费者已有 OpenID（ConsumerProfile），直接领取。
    否则返回 403 + OAuth URL，前端跳转授权。
    """
    tenant_id = benefit.tenant_id

    # 检查租户是否开通了红包功能；使用统一权益词表并对历史脏值 fail closed。
    from app.models.tenant import Tenant
    from app.services.entitlement import is_feature_enabled

    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant or not is_feature_enabled(tenant.enabled_features, "cash_red_packet"):
        raise HTTPException(status_code=403, detail="cash_red_packet not enabled for this tenant")

    # 检查租户是否配置了微信支付 connector
    from app.models.connector import Connector

    conn_result = await db.execute(
        select(Connector).where(
            Connector.tenant_id == tenant_id,
            Connector.connector_type == "wechat_pay_transfer",
            Connector.enabled.is_(True),
        )
    )
    connector = conn_result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=422, detail="No wechat_pay_transfer connector configured")

    # 尝试从 scan_token payload 中获取 consumer_id
    consumer_id_str = payload.get("consumer_id")
    from app.models.member import ConsumerProfile

    consumer = None
    if consumer_id_str:
        try:
            consumer_result = await db.execute(
                select(ConsumerProfile).where(
                    ConsumerProfile.id == uuid.UUID(consumer_id_str),
                    ConsumerProfile.tenant_id == tenant_id,
                )
            )
            consumer = consumer_result.scalar_one_or_none()
        except ValueError:
            pass

    from app.models.consent import ConsentRecord, ConsentStatus, ConsentType

    consent_status = None
    if consumer is not None:
        consent_status = await db.scalar(
            select(ConsentRecord.status)
            .where(
                ConsentRecord.tenant_id == tenant_id,
                ConsentRecord.consumer_id == consumer.id,
                ConsentRecord.consent_type == ConsentType.privacy,
                ConsentRecord.scenario == "wechat_cash_payout",
            )
            .order_by(ConsentRecord.granted_at.desc())
            .limit(1)
        )
    if not consumer or not consumer.wechat_openid_hash or consent_status != ConsentStatus.granted:
        # 没有 OpenID，返回需要 OAuth 的信号
        return {
            "status": "require_wechat_auth",
            "benefit_id": str(benefit.id),
            "auth_url_path": "/wechat/auth-url",
        }

    return {"status": "ready", "consumer_id": str(consumer.id), "connector_id": str(connector.id)}
