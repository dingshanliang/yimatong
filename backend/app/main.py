from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.ai import ai_router
from app.api.v1.analytics import analytics_router
from app.api.v1.analytics_dashboard import dashboard_router
from app.api.v1.auth import router as auth_router
from app.api.v1.benefit_claims import benefit_claim_router
from app.api.v1.benefits import benefit_router
from app.api.v1.campaigns import campaign_router
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
from app.api.v1.products import batch_router, brand_router, product_router, sku_router
from app.api.v1.public_pages import public_page_router
from app.api.v1.redpacket import redpacket_router
from app.api.v1.regional import regional_router
from app.api.v1.resolver import resolver_router
from app.api.v1.risk import risk_router
from app.api.v1.risk_dashboard import risk_dashboard_router
from app.api.v1.risk_evaluate import risk_evaluate_router
from app.api.v1.risk_rules import risk_rule_router
from app.api.v1.roles import router as roles_router
from app.api.v1.scan_events import scan_event_router
from app.api.v1.tasks import task_router
from app.api.v1.tenants import router as tenants_router
from app.api.v1.webhooks import webhook_router
from app.core.config import settings
from app.middleware.tenant import TenantScopeMiddleware


@asynccontextmanager
async def lifespan(app):
    from app.utils.crypto import EnvKeyProvider, init_crypto

    if settings.secret_key == "dev-secret-key-change-in-production":
        import os

        if os.getenv("ENVIRONMENT", "development") != "development":
            raise RuntimeError(
                "SECRET_KEY must be changed from default value in non-development environments. "
                "Set the SECRET_KEY environment variable."
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

    # 注册连接器适配器 + 权益发放事件处理器
    import app.services.connectors.generic_http  # noqa: F401
    import app.services.connectors.coupon_pool  # noqa: F401
    import app.services.benefit_delivery_handler  # noqa: F401

    yield


app = FastAPI(title="一码通", version="0.1.0", lifespan=lifespan)

# CORS — 开发环境默认允许所有，生产环境通过 CORS_ORIGINS 限制
origins = [o.strip() for o in settings.cors_origins.split(",")] if settings.cors_origins != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(TenantScopeMiddleware)
app.include_router(tenants_router)
app.include_router(orgs_router)
app.include_router(auth_router)
app.include_router(password_router)
app.include_router(platform_router)
app.include_router(brand_router)
app.include_router(product_router)
app.include_router(sku_router)
app.include_router(batch_router)
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
app.include_router(ops_router)
app.include_router(risk_router)
app.include_router(channel_router)
app.include_router(member_router)
app.include_router(risk_rule_router)
app.include_router(risk_dashboard_router)
app.include_router(regional_router)
app.include_router(gmv_router)
app.include_router(ai_router)
app.include_router(connector_router)
app.include_router(webhook_router)
app.include_router(open_api_router)
app.include_router(integration_router)
app.include_router(i18n_router)
app.include_router(template_router)
app.include_router(redpacket_router)
app.include_router(roles_router)
app.include_router(scan_event_router)
app.include_router(consumer_router)
app.include_router(benefit_claim_router)
app.include_router(consent_router)
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
