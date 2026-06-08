from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.agency_auth import _switch_router as agency_switch_router
from app.api.v1.agency_auth import router as agency_auth_router
from app.api.v1.ai import ai_router
from app.api.v1.analytics import analytics_router
from app.api.v1.analytics_dashboard import dashboard_router
from app.api.v1.auth import router as auth_router
from app.api.v1.benefit_claims import benefit_claim_router
from app.api.v1.benefits import benefit_router
from app.api.v1.campaigns import campaign_router
from app.api.v1.channel_analytics import channel_analytics_router
from app.api.v1.channels import channel_router
from app.api.v1.code_batches import code_batch_router, code_item_router
from app.api.v1.connectors import connector_router
from app.api.v1.consents import consent_router
from app.api.v1.consumers import consumer_router
from app.api.v1.files import file_router
from app.api.v1.gmv import gmv_router
from app.api.v1.i18n import i18n_router
from app.api.v1.imports import import_router
from app.api.v1.industry_templates import template_router
from app.api.v1.integration import integration_router
from app.api.v1.members import member_router
from app.api.v1.open_api import open_api_router
from app.api.v1.ops import ops_router
from app.api.v1.organizations import router as orgs_router
from app.api.v1.page_templates import page_template_router, page_version_router
from app.api.v1.password import router as password_router
from app.api.v1.platform import router as platform_router
from app.api.v1.prd_compat import prd_compat_router
from app.api.v1.private_domain import private_domain_router
from app.api.v1.products import asset_router, batch_router, brand_router, product_router, sku_router
from app.api.v1.public_pages import public_page_router
from app.api.v1.regional import regional_router
from app.api.v1.resolver import resolver_router
from app.api.v1.risk import risk_router
from app.api.v1.risk_dashboard import risk_dashboard_router
from app.api.v1.risk_evaluate import risk_evaluate_router
from app.api.v1.risk_notifications import risk_notification_router
from app.api.v1.risk_rules import risk_rule_router
from app.api.v1.roles import router as roles_router
from app.api.v1.scan_events import scan_event_router
from app.api.v1.tasks import task_router
from app.api.v1.invite_codes import router as invite_codes_router
from app.api.v1.tenants import router as tenants_router
from app.api.v1.webhooks import webhook_router
from app.api.v1.wechat_oauth import wechat_oauth_router
from app.api.v1.wecom_integrations import wecom_integration_router
from app.core.config import settings
from app.core.error_handlers import register_exception_handlers
from app.core.logging import setup_logging
from app.middleware.logging import LoggingMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.tenant import TenantScopeMiddleware

OPENAPI_TAGS = [
    {"name": "auth", "description": "认证与授权：登录、刷新 Token、登出、获取当前用户信息"},
    {"name": "tenants", "description": "租户管理：创建、查询、更新租户信息"},
    {"name": "organizations", "description": "组织管理：企业组织架构维护"},
    {"name": "accounts", "description": "账号管理：平台账号创建与维护"},
    {"name": "platform", "description": "平台管理：平台级配置与运营操作"},
    {"name": "roles", "description": "角色与权限：角色定义、权限分配"},
    {"name": "members", "description": "成员管理：租户成员邀请、激活、管理"},
    {"name": "brands", "description": "品牌管理：品牌创建、查询、更新"},
    {"name": "products", "description": "产品管理：产品定义、属性维护"},
    {"name": "product-assets", "description": "产品资料：检测报告、资质证书、素材与故事"},
    {"name": "skus", "description": "SKU 管理：产品规格与 SKU 维护"},
    {"name": "production-batches", "description": "生产批次管理：批次创建与查询"},
    {"name": "code-batches", "description": "码批次管理：批量生码、码规则配置"},
    {"name": "code-items", "description": "码项管理：单个码的查询与状态管理"},
    {"name": "resolver", "description": "码解析：消费者扫码公开接口，返回页面或 JSON 数据"},
    {"name": "campaigns", "description": "营销活动：活动创建、配置、上下线"},
    {"name": "benefits", "description": "权益管理：权益定义、库存、发放规则"},
    {"name": "benefit-claims", "description": "权益领取：消费者领取权益接口"},
    {"name": "page-templates", "description": "页面模板：H5 页面模板管理"},
    {"name": "page-versions", "description": "页面版本：模板版本发布、预览、回滚"},
    {"name": "public-pages", "description": "公开页面：消费者端页面配置与渲染"},
    {"name": "industry-templates", "description": "行业模板：预设行业页面与配置模板"},
    {"name": "consumers", "description": "消费者管理：消费者档案、画像、标签"},
    {"name": "scan-events", "description": "扫码事件：扫码记录查询与分析"},
    {"name": "consents", "description": "同意书管理：隐私协议、用户授权记录"},
    {"name": "analytics", "description": "数据分析：扫码统计、活动效果分析"},
    {"name": "analytics-dashboards", "description": "分析仪表盘：可视化报表与数据看板"},
    {"name": "gmv", "description": "GMV 统计：交易金额与订单统计"},
    {"name": "connectors", "description": "连接器：外部系统集成适配器配置"},
    {"name": "webhooks", "description": "Webhook：事件订阅与推送管理"},
    {"name": "integration", "description": "系统集成：第三方平台对接配置"},
    {"name": "wecom-integrations", "description": "企业微信客户联系：接入状态、添加事件与联系入口"},
    {"name": "open-api", "description": "开放 API：对外提供的标准 API 接口（API Key 认证）"},
    {"name": "wechat-oauth", "description": "微信 OAuth：微信授权登录与用户信息获取"},
    {"name": "imports", "description": "数据导入：批量导入产品与码数据"},
    {"name": "files", "description": "文件管理：上传、存储、获取文件"},
    {"name": "ai", "description": "AI 服务：智能文案生成、内容优化"},
    {"name": "i18n", "description": "国际化：多语言内容管理"},
    {"name": "regional", "description": "区域管理：地理区域与渠道区域配置"},
    {"name": "risk", "description": "风控管理：风险规则与风险事件处理"},
    {"name": "risk-rules", "description": "风控规则：规则定义与配置"},
    {"name": "risk-evaluate", "description": "风控评估：风险评分与评估接口"},
    {"name": "risk-dashboard", "description": "风控仪表盘：风险数据可视化"},
    {"name": "risk-alerts", "description": "风控告警：风险告警与通知"},
    {"name": "tasks", "description": "异步任务：后台任务管理与状态查询"},
    {"name": "agency-auth", "description": "Agency 授权：品牌授权代运营访问管理"},
    {"name": "ops", "description": "运维任务：平台运维与租户运营任务"},
    {"name": "channels", "description": "渠道管理：营销渠道配置与管理"},
    {"name": "password", "description": "密码管理：密码重置与修改"},
    {"name": "private-domain", "description": "私域管理：私域流量运营工具"},
    {"name": "prd-compat", "description": "PRD 兼容：产品需求文档兼容接口"},
    {"name": "redpacket", "description": "红包活动：微信红包发放与管理"},
]

APP_DESCRIPTION = """
一码通（yimatong）面向食品、农产品及消费品品牌方提供包装扫码增长 SaaS 服务。

## 认证方式

### JWT Bearer Token（管理后台 / 大部分接口）
1. 调用 `POST /api/v1/auth/login` 获取 access_token 和 refresh_token
2. 在后续请求的 `Authorization` Header 中携带 `Bearer {access_token}`
3. Token 过期后使用 `POST /api/v1/auth/refresh` 换取新的 access_token

### API Key（开放 API）
1. 部分对外接口使用 `X-Api-Key` Header 认证
2. 适用于第三方系统服务器间调用

## 多租户说明

所有业务接口均基于**租户隔离**运行。JWT Token 中已包含 tenant_id，服务端会自动注入到数据库会话中（PostgreSQL RLS）。

## 错误格式

业务错误统一返回 JSON：
```json
{
  "error_code": "NOT_FOUND",
  "detail": "错误描述信息",
  "request_id": "a1b2c3d4e5f6"
}
```

字段说明：
- `error_code`：机器可读错误标识（如 `NOT_FOUND`、`VALIDATION_ERROR`、`HTTP_401`）
- `detail`：人类可读错误信息（验证错误时为数组）
- `request_id`：请求追踪 ID，与响应头 `X-Request-ID` 一致

状态码遵循 HTTP 语义：
- `400` 请求参数错误
- `401` 未认证或 Token 无效
- `403` 权限不足
- `404` 资源不存在
- `409` 资源冲突
- `422` 请求体校验失败
- `429` 请求过于频繁
- `500` 服务器内部错误
"""


@asynccontextmanager
async def lifespan(app):
    setup_logging(json_logs=(settings.log_format == "json"), level=settings.log_level)

    from app.utils.crypto import EnvKeyProvider, init_crypto

    if settings.secret_key == "dev-secret-key-change-in-production":
        import os

        if os.getenv("ENVIRONMENT", "development") != "development":
            raise RuntimeError(
                "SECRET_KEY must be changed from default value in non-development environments. "
                "Set the SECRET_KEY environment variable."
            )
    elif len(settings.secret_key) < 32:
        raise RuntimeError(
            f"SECRET_KEY is too short ({len(settings.secret_key)} chars). Minimum 32 characters required."
        )

    if not settings.aes_master_key_v1 or not settings.hmac_pepper:
        raise RuntimeError(
            "AES_MASTER_KEY_V1 and HMAC_PEPPER must be configured. Crypto module cannot start without encryption keys."
        )
    init_crypto(EnvKeyProvider())

    # 初始化 LLM Key Pool
    from app.services.llm_pool import init_pool

    init_pool()

    # 初始化 Webhook 事件调度器
    from app.services.webhook_dispatcher import init_webhook_dispatcher

    init_webhook_dispatcher()

    # 初始化风控自动评估处理器
    from app.services.risk_auto_handler import init_risk_auto_handler

    init_risk_auto_handler()

    # 初始化积分自动发放处理器
    from app.services.point_auto_handler import init_point_auto_handler

    init_point_auto_handler()

    # 注册连接器适配器 + 权益发放事件处理器
    import app.services.benefit_delivery_handler  # noqa: F401
    import app.services.connectors.coupon_pool  # noqa: F401
    import app.services.connectors.generic_http  # noqa: F401
    import app.services.connectors.wechat_pay_transfer  # noqa: F401

    yield

    # 清理 Redis 连接池
    from app.services.redis_cache import close_redis_pool

    await close_redis_pool()


app = FastAPI(
    title="一码通 API",
    description=APP_DESCRIPTION,
    version="0.1.0",
    openapi_tags=OPENAPI_TAGS,
    contact={
        "name": "一码通技术支持",
        "email": "support@yimatong.cn",
    },
    license_info={
        "name": "专有软件",
    },
    terms_of_service="https://yimatong.cn/terms",
    lifespan=lifespan,
    swagger_ui_parameters={"persistAuthorization": True},
)


# 自定义 OpenAPI：注入全局 Security Schemes（Bearer JWT + API Key）
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        openapi_version="3.1.0",
    )
    openapi_schema.setdefault("components", {})
    openapi_schema["components"].setdefault("securitySchemes", {})
    openapi_schema["components"]["securitySchemes"]["Bearer"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "JWT access_token，通过 `/api/v1/auth/login` 获取",
    }
    openapi_schema["components"]["securitySchemes"]["ApiKey"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-Api-Key",
        "description": "开放 API 密钥，通过平台管理后台创建",
    }
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi

# CORS — 生产环境禁止通配符
import os as _os

_cors_origins_str = settings.cors_origins.strip()
if _cors_origins_str == "*":
    if _os.getenv("ENVIRONMENT", "development") != "development":
        raise RuntimeError(
            "CORS_ORIGINS=* is not allowed in non-development environments. "
            "Set CORS_ORIGINS to a comma-separated list of allowed origins."
        )
    origins = ["*"]
else:
    origins = [o.strip() for o in _cors_origins_str.split(",") if o.strip()]
# Middleware order in FastAPI is LIFO (last added = outermost = first executed).
# CORSMiddleware MUST be added LAST so it becomes the outermost layer.
# Otherwise, inner middlewares (e.g., TenantScopeMiddleware returning 401) bypass
# CORS header injection, causing browsers to block responses as CORS failures.
app.add_middleware(LoggingMiddleware)
app.add_middleware(TenantScopeMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app, debug=app.debug)
app.include_router(tenants_router)
app.include_router(orgs_router)
app.include_router(auth_router)
app.include_router(password_router)
app.include_router(platform_router)
app.include_router(invite_codes_router)
app.include_router(brand_router)
app.include_router(product_router)
app.include_router(sku_router)
app.include_router(batch_router)
app.include_router(asset_router)
app.include_router(file_router)
app.include_router(code_batch_router)
app.include_router(code_item_router)
app.include_router(task_router)
app.include_router(page_template_router)
app.include_router(page_version_router)
app.include_router(resolver_router)
app.include_router(analytics_router)
app.include_router(campaign_router)
app.include_router(benefit_router)
app.include_router(agency_auth_router)
app.include_router(agency_switch_router)
app.include_router(ops_router)
app.include_router(risk_router)
app.include_router(risk_notification_router)
app.include_router(channel_router)
app.include_router(channel_analytics_router)
app.include_router(member_router)
app.include_router(risk_rule_router)
app.include_router(risk_dashboard_router)
app.include_router(regional_router)
app.include_router(gmv_router)
app.include_router(ai_router)
app.include_router(connector_router)
app.include_router(webhook_router)
app.include_router(wecom_integration_router)
app.include_router(open_api_router)
app.include_router(integration_router)
app.include_router(i18n_router)
app.include_router(template_router)

app.include_router(roles_router)
app.include_router(scan_event_router)
app.include_router(consumer_router)
app.include_router(benefit_claim_router)
app.include_router(consent_router)
app.include_router(wechat_oauth_router)
app.include_router(public_page_router)
app.include_router(import_router)
app.include_router(private_domain_router)
app.include_router(dashboard_router)
app.include_router(risk_evaluate_router)
app.include_router(prd_compat_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/health/detail")
async def health_detail():
    checks = {"postgres": "unknown", "redis": "unknown", "minio": "unknown"}
    try:
        from sqlalchemy import text

        from app.core.database import engine

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "error"

    try:
        import redis.asyncio as aioredis

        async with aioredis.from_url(settings.redis_url) as r:
            await r.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"

    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": status, "version": "0.1.0", "services": checks}
