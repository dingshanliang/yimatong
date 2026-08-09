"""码解析公开路由"""

import logging
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import _session_uses_postgresql, get_db, lock_active_tenant_context, set_session_tenant_context
from app.middleware.rate_limit import rate_limiter
from app.models.code import CodeItem, CodeItemStatus, CodeType, to_lifecycle
from app.models.scan import ScanEvent
from app.services.page_render import render_page
from app.services.page_templates import (
    EXPIRED_PAGE,
    INNER_VERIFY_PAGE,
    NOT_ACTIVE_PAGE,
    NOT_FOUND_PAGE,
    OUTER_LANDING_PAGE,
    REVOKED_PAGE,
    build_code_page,
)
from app.services.public_id import validate_public_id
from app.services.quota import QuotaExceededError
from app.services.resolve_cache import resolve_cache
from app.services.resolver import resolve_public_code
from app.services.resolver_response import build_json_response
from app.services.scan_event import parse_environment, record_scan_event
from app.services.scan_token import create_scan_token
from app.utils.client_ip import compute_ip_hash, get_client_ip

logger = logging.getLogger(__name__)

resolver_router = APIRouter(tags=["resolver"])

# 终止性状态：不颁发 scan_token、不记录扫码、不返回溯源资料。
# yimatong-zgb1.6：frozen 从此集合移除——frozen 保留溯源（AC3），只暂停权益。
# revoked/expired → voided 生命周期（1.3 归一化），created → unactivated。
_TERMINAL_STATUSES = frozenset(
    {
        CodeItemStatus.revoked,
        CodeItemStatus.created,
        CodeItemStatus.expired,
    }
)


@resolver_router.get("/c/{public_id}", summary="解析码")
async def resolve_code_endpoint(
    public_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    accept = request.headers.get("accept", "")
    want_json = "application/json" in accept

    # 1. 限流 + 格式校验
    client_ip = get_client_ip(request)
    rate_result = await rate_limiter.check_resolver(client_ip, public_id)
    if not rate_result.allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests"},
            headers={"Retry-After": str(rate_result.retry_after)},
        )

    if not validate_public_id(public_id):
        return _not_found(want_json)

    # 2. 公开码只能用精确 public_id 做一次受控租户定位。定位完成后立即
    # 关闭 bypass 并在同一事务写入精确 tenant RLS context；后续码、套餐、
    # 配额、页面和事件查询都只能看见该租户。
    tenant_uuid = await _scope_public_code_tenant(db, public_id)
    if tenant_uuid is None:
        return _not_found(want_json)

    # 3. 缓存查询 -> DB 回退。缓存租户必须与数据库定位结果一致，避免
    # 污染或过期缓存把另一个租户的数据带入当前会话。
    cached = await resolve_cache.get(f"resolve:{public_id}")
    data = cached or await resolve_public_code(db, public_id)
    if data and not cached:
        await resolve_cache.set(f"resolve:{public_id}", data)

    if not data or data.get("tenant_id") != str(tenant_uuid):
        return _not_found(want_json)

    from app.services.entitlement import TenantPlanExpiredError

    try:
        await lock_active_tenant_context(db, tenant_uuid)
    except TenantPlanExpiredError:
        return _plan_expired(want_json)

    status = data["status"]

    # 4. 终止性状态（revoked/created/expired）— 不颁发 token、不返回溯源
    if status in _TERMINAL_STATUSES:
        return _error_status(status, public_id, want_json)

    # 5. 计算 IP hash（一次，复用）
    ip_hash = compute_ip_hash(client_ip)

    # 6. 记录扫码事件（frozen 也记录查验，但不颁发 scan_token → 权益自然暂停）
    user_agent = request.headers.get("user-agent", "")
    is_frozen = status == CodeItemStatus.frozen
    # yimatong-zgb1.10：读取 visitor_id（H5 localStorage 携带，X-Visitor-ID 头）
    request_visitor_id = request.headers.get("X-Visitor-ID") or None
    # yimatong-zgb1.10 Decision 22：解析或签发匿名访客（first-party 稳定 ID）
    from app.services.visitor import resolve_or_create_visitor

    visitor = await resolve_or_create_visitor(
        db,
        tenant_id=tenant_uuid,
        visitor_id=request_visitor_id,
        environment=parse_environment(user_agent),
        ip_hash=ip_hash,
    )
    visitor_id = visitor.visitor_id
    # yimatong-zgb1.10 Decision 20：有效访问判断（4 条规则）
    is_robot = _is_robot_traffic(user_agent, request)
    is_valid_visit = status in (CodeItemStatus.activated, CodeItemStatus.frozen) and not is_robot
    # frozen 仍记录扫码事实（消费者查看了溯源），但不颁发 scan_token
    try:
        scan_info = await _record_scan(
            db,
            data,
            public_id,
            ip_hash,
            user_agent,
            status,
            visitor_id=visitor_id,
            is_valid_visit=is_valid_visit,
        )
    except QuotaExceededError:
        return _quota_exceeded(want_json)
    # 把签发的 visitor_id 放进 scan_info，H5 存 localStorage
    scan_info["visitor_id"] = visitor_id

    # 7. 生成 scan_token（含 tenant_id）— frozen 不颁发（权益暂停，AC3）
    scan_token = None
    if not is_frozen:
        scan_token = create_scan_token(
            public_id=public_id,
            ip_hash=ip_hash,
            tenant_id=data["tenant_id"],
        )
    else:
        # frozen：标记权益暂停（H5 据此隐藏领取入口）
        scan_info["benefit_paused"] = True
        scan_info["paused_reason"] = "frozen"

    # 8. JSON 模式
    if want_json:
        resp = await build_json_response(db, data, scan_token, scan_info)
        return JSONResponse(content=resp)

    # 9. HTML 模式
    return await _html_response(db, data, public_id)


# -- 内部辅助函数 --


async def _scope_public_code_tenant(db: AsyncSession, public_id: str) -> uuid.UUID | None:
    """Locate one public code's tenant, then lock the session to that tenant.

    The temporary bypass is intentionally confined to a projection of
    ``CodeItem.tenant_id`` constrained by the already validated public ID.  No
    business object is loaded while bypass is active.
    """

    if _session_uses_postgresql(db):
        from app.core.database import control_session_factory

        async with control_session_factory() as control_db:
            await control_db.execute(text("SELECT set_config('app.tenant_id', '', true)"))
            await control_db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
            tenant_id = await control_db.scalar(
                select(CodeItem.tenant_id).where(CodeItem.public_id == public_id).limit(1)
            )
    else:
        tenant_id = await db.scalar(select(CodeItem.tenant_id).where(CodeItem.public_id == public_id).limit(1))
    if tenant_id is None:
        return None
    return await set_session_tenant_context(db, tenant_id)


def _not_found(want_json: bool):
    """yimatong-zgb1.6 AC5：不存在的码不泄露租户/批次/内部错误信息。"""
    if want_json:
        return JSONResponse(
            status_code=404,
            content={
                "detail": "not_found",
                "code_data": {"result": "not_found"},
            },
        )
    return HTMLResponse(content=NOT_FOUND_PAGE, status_code=404)


def _browser_service_unavailable(reason: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(
        status_code=status_code,
        content=f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>暂时无法继续查验</title>
</head>
<body style="font-family:system-ui,-apple-system,sans-serif;max-width:36rem;margin:12vh auto;padding:0 1.5rem">
  <main>
    <h1 style="font-size:1.5rem">当前无法继续查验</h1><p>{reason}</p>
    <p>请稍后再试；如持续无法使用，请联系商品品牌方处理。</p>
  </main>
</body></html>""",
    )


def _plan_expired(want_json: bool):
    from app.services.entitlement import PLAN_EXPIRED_CODE, PLAN_EXPIRED_DETAIL

    if not want_json:
        return _browser_service_unavailable("品牌方的服务套餐当前已到期。", 403)
    return JSONResponse(
        status_code=403,
        content={"code": PLAN_EXPIRED_CODE, "detail": PLAN_EXPIRED_DETAIL},
    )


def _quota_exceeded(want_json: bool):
    if not want_json:
        return _browser_service_unavailable("品牌方当前可用的扫码服务次数已用完。", 429)
    return JSONResponse(
        status_code=429,
        content={"code": "QUOTA_EXCEEDED", "detail": "扫码服务额度已用完，请联系品牌方"},
    )


# 终止状态映射：(HTTP 状态码, HTML 模板)
_ERROR_MAP: dict[str, tuple[int, str]] = {
    CodeItemStatus.revoked: (410, REVOKED_PAGE),
    CodeItemStatus.expired: (410, EXPIRED_PAGE),
    CodeItemStatus.created: (200, NOT_ACTIVE_PAGE),
}


def _error_status(status: str, public_id: str, want_json: bool):
    """yimatong-zgb1.6：4 种状态互不混淆的 API 契约。

    返回 lifecycle（权威四状态）+ result（互斥结果标识）+ public_id + 兼容 status。
    HTTP 状态码：unactivated=200, voided(revoked/expired)=410。
    """
    http_code, html_page = _ERROR_MAP.get(status, (200, NOT_ACTIVE_PAGE))
    lifecycle = to_lifecycle(status)
    # result 字段：互不混淆的消费者侧结果标识
    result = lifecycle.value  # unactivated / voided
    if want_json:
        return JSONResponse(
            status_code=http_code,
            content={
                "code_data": {
                    "public_id": public_id,
                    "status": status,  # 兼容旧客户端
                    "lifecycle": lifecycle.value,  # 权威四状态
                    "result": result,  # 互斥结果标识
                }
            },
        )
    return HTMLResponse(content=html_page, status_code=http_code)


def _is_robot_traffic(user_agent: str, request: Request) -> bool:
    """yimatong-zgb1.10 Decision 20：识别 robot/internal test 流量（不计入有效访问）。

    判断依据（保守，宁可漏判不可误判真实消费者）：
    - UA 含明显爬虫标识（bot/crawler/spider/curl/wget/python-requests）
    - 请求头 X-Internal-Test 标记（内部测试流量）
    """
    if not user_agent:
        return False
    ua_lower = user_agent.lower()
    robot_markers = ("bot", "crawler", "spider", "curl", "wget", "python-requests", "scrapy")
    if any(marker in ua_lower for marker in robot_markers):
        return True
    # 内部测试标记头
    if request.headers.get("X-Internal-Test"):
        return True
    return False


async def _record_scan(
    db: AsyncSession,
    data: dict,
    public_id: str,
    ip_hash: str | None,
    user_agent: str,
    status: str,
    visitor_id: str | None = None,
    is_valid_visit: bool = False,
) -> dict:
    """记录一次有效查验并构建消费者侧 scan_info 契约。

    yimatong-zgb1.4 契约（轻防伪结果 + 首查权威事实）：
    - ``is_first_scan`` / ``scan_count`` 保留兼容（旧客户端）。
    - ``verification_count`` = 本码累计被查验次数（含本次），首次 = 1（post-insert COUNT，
      更直观；旧 scan_count 设同值作为别名）。
    - ``first_scan_time`` = 权威首查时间，单一源读自 ``code_items.first_scanned_at``
      （由 record_scan_event 内部原子 UPDATE 维护，rowcount==1 即首查赢家）。
    - ``verification_time`` = 本次查验时间（ISO8601）。

    失败不阻断主流程（与历史行为一致）：扫码解析即使记录失败仍可继续，只记日志。
    """
    scan_info: dict = {
        "is_first_scan": False,
        "scan_count": 0,
        "verification_count": 0,
        "first_scan_time": None,
        "verification_time": None,
        # yimatong-zgb1.5 AC1：最近查验时间（repeat scan 时展示，与 verification_time 同值，
        # 但契约字段名更清晰——消费者侧"最近一次查验"语义）。
        "last_scan_time": None,
    }
    if status != CodeItemStatus.activated:
        # yimatong-zgb1.6：frozen 码仍记录查验事实（消费者查看了溯源），其他非 activated 状态不记录。
        if status != CodeItemStatus.frozen:
            return scan_info

    tenant_id = uuid.UUID(data["tenant_id"])
    try:
        event = await record_scan_event(
            db=db,
            tenant_id=tenant_id,
            public_id=public_id,
            ip_hash=ip_hash,
            user_agent=user_agent,
            environment=parse_environment(user_agent),
            visitor_id=visitor_id,
            is_valid_visit=is_valid_visit,
        )
        # 权威首查时间：读自 code_items.first_scanned_at（单一源；与 event.is_first_scan 一致）。
        item_result = await db.execute(
            select(CodeItem.first_scanned_at).where(CodeItem.public_id == public_id, CodeItem.tenant_id == tenant_id)
        )
        first_scanned_at = item_result.scalar()
        # post-insert COUNT：含本次在内的累计查验次数（首次 = 1）。
        # tenant 维度过滤（yimatong-zgb1.4 AC4）：跨租户调用方写入的 scan_events
        # 不应计入本租户的查验计数。
        count_after = await db.execute(
            select(func.count())
            .select_from(ScanEvent)
            .where(ScanEvent.public_id == public_id, ScanEvent.tenant_id == tenant_id)
        )
        verification_count = int(count_after.scalar() or 0)

        scan_info["is_first_scan"] = event.is_first_scan
        scan_info["verification_count"] = verification_count
        scan_info["scan_count"] = verification_count  # 兼容别名
        scan_info["first_scan_time"] = first_scanned_at.isoformat() if first_scanned_at else None
        scan_info["verification_time"] = event.scan_time.isoformat()
        # yimatong-zgb1.5：last_scan_time = 本次查验时间（与 verification_time 同值，契约字段名更清晰）
        scan_info["last_scan_time"] = event.scan_time.isoformat()
    except QuotaExceededError:
        raise
    except Exception:
        logger.exception("Failed to record scan event for public_id=%s", public_id)
    return scan_info


async def _html_response(db: AsyncSession, data: dict, public_id: str):
    code_type = data.get("code_type", CodeType.single)

    if code_type == CodeType.outer:
        return HTMLResponse(content=OUTER_LANDING_PAGE.format(public_id=public_id))

    # inner 和 single 共享模板渲染逻辑
    template_id = data.get("template_id")
    tenant_id = data.get("tenant_id")
    if template_id and tenant_id:
        html = await render_page(db, uuid.UUID(tenant_id), uuid.UUID(template_id))
        if html:
            return HTMLResponse(content=html)

    # 模板渲染失败时的降级页
    if code_type == CodeType.inner:
        return HTMLResponse(content=INNER_VERIFY_PAGE.format(public_id=public_id))

    return HTMLResponse(content=build_code_page(data))
