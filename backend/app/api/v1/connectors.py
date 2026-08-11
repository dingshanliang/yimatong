"""外部权益连接器 API

L2: 券码池管理 + 库存同步
L3: API 发券 + 回调接收
L4: 失败重试
"""

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.connectors.coupon_pool  # noqa: F401
import app.services.connectors.generic_http  # noqa: F401
import app.services.connectors.wechat_pay_transfer  # noqa: F401
import app.services.connectors.wecom_crm  # noqa: F401
from app.core.database import bootstrap_tenant_row, get_db
from app.core.dependencies import get_current_tenant
from app.models.connector import BenefitDelivery, Connector
from app.services.connectors import get_adapter
from app.services.connectors.secrets import (
    connector_with_runtime_secrets,
    decrypt_secrets,
    encrypt_secrets,
    mask_secrets,
    public_connector_config,
    sensitive_config_keys,
)
from app.utils.auth_rbac import require_durable_session, require_permission

connector_router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])


# ---------------------------------------------------------------------------
# Request/Response schemas
# ---------------------------------------------------------------------------


ConnectorName = Annotated[str, Field(min_length=1, max_length=200)]
ConnectorType = Annotated[str, Field(min_length=1, max_length=50)]
ConsumerReference = Annotated[str, Field(min_length=1, max_length=100)]
CouponCodeValue = Annotated[str, Field(min_length=1, max_length=100)]


def _bounded_json(value: dict[str, object] | None, *, label: str) -> dict[str, object] | None:
    if value is None:
        return None
    nodes = 0

    def walk(item: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if depth > 6 or nodes > 512:
            raise ValueError(f"{label} is too deeply nested or complex")
        if isinstance(item, dict):
            if len(item) > 100:
                raise ValueError(f"{label} contains too many keys")
            for key, nested in item.items():
                if not isinstance(key, str) or not key or len(key) > 100:
                    raise ValueError(f"{label} contains an invalid key")
                walk(nested, depth + 1)
        elif isinstance(item, list):
            if len(item) > 200:
                raise ValueError(f"{label} contains too many items")
            for nested in item:
                walk(nested, depth + 1)
        elif isinstance(item, str) and len(item) > 32_768:
            raise ValueError(f"{label} contains an oversized value")
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError(f"{label} contains a non-JSON value")

    walk(value, 0)
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > 65_536:
        raise ValueError(f"{label} exceeds 65536 bytes")
    return value


class ConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CouponPoolCreate(ConnectorRequest):
    name: ConnectorName
    codes: list[CouponCodeValue] = Field(min_length=1, max_length=5_000)

    @field_validator("codes")
    @classmethod
    def validate_codes(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Coupon codes must be unique")
        if sum(len(value.encode()) for value in normalized) > 500_000:
            raise ValueError("Coupon codes exceed the request character budget")
        return normalized


class DistributeRequest(ConnectorRequest):
    consumer_id: ConsumerReference


class ConnectorCreate(ConnectorRequest):
    name: ConnectorName
    connector_type: ConnectorType
    config: dict[str, object] = Field(default_factory=dict)
    secrets: dict[str, object] | None = None

    _config_bounds = field_validator("config")(lambda value: _bounded_json(value, label="config"))
    _secret_bounds = field_validator("secrets")(lambda value: _bounded_json(value, label="secrets"))


class ConnectorUpdate(ConnectorRequest):
    name: ConnectorName | None = None
    config: dict[str, object] | None = None
    secrets: dict[str, object] | None = None
    enabled: bool | None = None

    _config_bounds = field_validator("config")(lambda value: _bounded_json(value, label="config"))
    _secret_bounds = field_validator("secrets")(lambda value: _bounded_json(value, label="secrets"))


class DeliverBenefitRequest(ConnectorRequest):
    consumer_id: ConsumerReference
    benefit_type: ConnectorType
    benefit_config: dict[str, object] = Field(default_factory=dict)

    _config_bounds = field_validator("benefit_config")(lambda value: _bounded_json(value, label="benefit_config"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_connector_or_404(db: AsyncSession, tenant_id: uuid.UUID, conn_id: uuid.UUID) -> Connector:
    result = await db.execute(select(Connector).where(Connector.id == conn_id, Connector.tenant_id == tenant_id))
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
    return connector


def _connector_to_dict(connector: Connector, include_secrets: bool = False) -> dict:
    data = {
        "id": str(connector.id),
        "tenant_id": str(connector.tenant_id),
        "name": connector.name,
        "connector_type": connector.connector_type,
        "config": public_connector_config(connector.config),
        "enabled": connector.enabled,
        "created_at": connector.created_at.isoformat() if connector.created_at else None,
        "updated_at": connector.updated_at.isoformat() if connector.updated_at else None,
    }
    if include_secrets:
        if connector.secrets_encrypted:
            secrets = decrypt_secrets(connector.secrets_encrypted)
            data["secrets"] = mask_secrets(secrets)
        else:
            data["secrets"] = {}
    return data


def _validated_connector_inputs(config: dict, secrets: dict | None) -> tuple[dict, dict]:
    leaked = sensitive_config_keys(config)
    if leaked:
        raise HTTPException(
            status_code=422,
            detail=f"Credentials must be supplied via secrets: {', '.join(sorted(leaked))}",
        )
    return public_connector_config(config), dict(secrets or {})


def _delivery_to_dict(delivery) -> dict:
    return {
        "id": str(delivery.id),
        "tenant_id": str(delivery.tenant_id),
        "connector_id": str(delivery.connector_id),
        "consumer_id": delivery.consumer_id,
        "benefit_id": str(delivery.benefit_id) if delivery.benefit_id else None,
        "claim_id": str(delivery.claim_id) if delivery.claim_id else None,
        "benefit_type": delivery.benefit_type,
        "status": delivery.status,
        "retry_count": delivery.retry_count,
        "max_retries": delivery.max_retries,
        "external_data": delivery.external_data,
        "next_retry_at": delivery.next_retry_at.isoformat() if delivery.next_retry_at else None,
        "created_at": delivery.created_at.isoformat() if delivery.created_at else None,
        "updated_at": delivery.updated_at.isoformat() if delivery.updated_at else None,
    }


# ---------------------------------------------------------------------------
# 券码池 CRUD
# ---------------------------------------------------------------------------


@connector_router.get("/coupon-pools", summary="券码池列表")
async def list_pools_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("campaign:manage")),
):
    from app.services.connector import list_coupon_pools

    pools, total = await list_coupon_pools(db, tenant_id, page=page, page_size=page_size)
    return {
        "items": [
            {
                "id": str(p.id),
                "name": p.name,
                "total_codes": p.total_codes,
                "remaining": p.remaining,
            }
            for p in pools
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@connector_router.post("/coupon-pools", status_code=201, summary="创建 pool")
async def create_pool_endpoint(
    body: CouponPoolCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_durable_session),
    __: None = Depends(require_permission("campaign:manage")),
):
    from app.services.connector import create_coupon_pool

    try:
        async with db.begin_nested():
            pool = await create_coupon_pool(db, tenant_id, body.name, body.codes)
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Coupon code already exists")
    return {
        "id": str(pool.id),
        "name": pool.name,
        "total_codes": pool.total_codes,
        "remaining": pool.remaining,
    }


@connector_router.get("/coupon-pools/{pool_id}/codes", summary="券码池码列表")
async def list_pool_codes_endpoint(
    pool_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("campaign:manage")),
):
    from app.services.connector import list_pool_codes

    codes, total = await list_pool_codes(db, tenant_id, pool_id, page=page, page_size=page_size)
    return {
        "items": [
            {
                "id": str(c.id),
                "code": c.code,
                "consumer_id": c.consumer_id,
                "distributed": c.distributed,
            }
            for c in codes
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@connector_router.post("/coupon-pools/{pool_id}/distribute")
async def distribute_endpoint(
    pool_id: uuid.UUID,
    body: DistributeRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_durable_session),
    __: None = Depends(require_permission("campaign:manage")),
):
    from app.services.connector import distribute_coupon

    code = await distribute_coupon(db, tenant_id, pool_id, body.consumer_id)
    if not code:
        raise HTTPException(status_code=400, detail="No available codes in pool")
    return {
        "code": code.code,
        "consumer_id": code.consumer_id,
        "distributed": True,
    }


# ---------------------------------------------------------------------------
# 连接器 CRUD
# ---------------------------------------------------------------------------


@connector_router.post("/connectors", status_code=201, summary="创建 连接器")
async def create_connector_endpoint(
    body: ConnectorCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_durable_session),
    __: None = Depends(require_permission("campaign:manage")),
):
    from app.services.connector import create_connector as _create

    # 验证适配器类型和配置
    public_config, supplied_secrets = _validated_connector_inputs(body.config, body.secrets)
    stub = Connector(
        tenant_id=tenant_id,
        name=body.name,
        connector_type=body.connector_type,
        config=public_config,
        enabled=True,
    )
    adapter = get_adapter(stub)
    is_valid, error = await adapter.validate_config({**public_config, **supplied_secrets})
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Invalid config: {error}")

    secrets_encrypted = encrypt_secrets(supplied_secrets) if supplied_secrets else None
    conn = await _create(db, tenant_id, body.name, body.connector_type, public_config)
    if secrets_encrypted:
        conn.secrets_encrypted = secrets_encrypted
        await db.flush()
        await db.refresh(conn)

    return _connector_to_dict(conn, include_secrets=True)


@connector_router.get("/connectors", summary="连接器 列表")
async def list_connectors_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    connector_type: str | None = Query(None, min_length=1, max_length=50),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("campaign:manage")),
):
    from app.services.connector import list_connectors

    conns, total = await list_connectors(
        db,
        tenant_id,
        page=page,
        page_size=page_size,
        connector_type=connector_type,
    )
    return {
        "items": [_connector_to_dict(c, include_secrets=True) for c in conns],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@connector_router.get("/connectors/types", summary="connector types 列表")
async def list_connector_types_endpoint(_: None = Depends(require_permission("campaign:manage"))):
    """返回系统支持的所有连接器类型。"""
    from app.services.connectors.registry import list_adapter_types

    return {"types": list_adapter_types()}


@connector_router.get("/connectors/{conn_id}", summary="获取 连接器")
async def get_connector_endpoint(
    conn_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("campaign:manage")),
):
    connector = await _get_connector_or_404(db, tenant_id, conn_id)
    return _connector_to_dict(connector, include_secrets=True)


@connector_router.post("/connectors/{conn_id}/test")
async def test_connection_endpoint(
    conn_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_durable_session),
    __: None = Depends(require_permission("campaign:manage")),
):
    from app.services.connector import test_connection

    connector = await _get_connector_or_404(db, tenant_id, conn_id)
    return await test_connection(connector)


@connector_router.patch("/connectors/{conn_id}", summary="更新 连接器")
async def update_connector_endpoint(
    conn_id: uuid.UUID,
    body: ConnectorUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_durable_session),
    __: None = Depends(require_permission("campaign:manage")),
):
    from app.services.connector import update_connector

    conn = await _get_connector_or_404(db, tenant_id, conn_id)
    updates = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.config is not None:
        public_config, _ = _validated_connector_inputs(body.config, body.secrets)
        updates["config"] = public_config
    if body.enabled is not None:
        updates["enabled"] = body.enabled

    existing_secrets = decrypt_secrets(conn.secrets_encrypted) if conn.secrets_encrypted else {}
    supplied_secrets = {**existing_secrets, **dict(body.secrets)} if body.secrets is not None else existing_secrets
    candidate_config = updates.get("config", public_connector_config(conn.config))
    adapter = get_adapter(conn)
    is_valid, error = await adapter.validate_config({**candidate_config, **supplied_secrets})
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Invalid config: {error}")
    if body.secrets is not None:
        updates["secrets_encrypted"] = encrypt_secrets(supplied_secrets)

    try:
        conn = await update_connector(db, tenant_id, conn_id, **updates)
    except ValueError:
        raise HTTPException(status_code=404, detail="Connector not found")
    return _connector_to_dict(conn, include_secrets=True)


# ---------------------------------------------------------------------------
# L2: 库存同步
# ---------------------------------------------------------------------------


@connector_router.post("/connectors/{conn_id}/sync-stock")
async def sync_stock_endpoint(
    conn_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_durable_session),
    __: None = Depends(require_permission("campaign:manage")),
):
    connector = await _get_connector_or_404(db, tenant_id, conn_id)
    if not connector.enabled:
        raise HTTPException(status_code=400, detail="Connector is disabled")

    runtime_connector = connector_with_runtime_secrets(connector)
    adapter = get_adapter(runtime_connector)
    try:
        available = await adapter.sync_stock(runtime_connector)
        connector.config = {**public_connector_config(connector.config), "stock": {"available": available}}
        await db.flush()
        await db.refresh(connector)
        return {"connector_id": str(connector.id), "available": available}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Stock sync failed: {exc}")


# ---------------------------------------------------------------------------
# L3: 发放权益
# ---------------------------------------------------------------------------


@connector_router.post("/connectors/{conn_id}/deliver")
async def deliver_benefit_endpoint(
    conn_id: uuid.UUID,
    body: DeliverBenefitRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("campaign:manage")),
):
    from app.services.benefit_delivery_handler import _do_deliver

    connector = await _get_connector_or_404(db, tenant_id, conn_id)
    if not connector.enabled:
        raise HTTPException(status_code=400, detail="Connector is disabled")
    if db.get_bind().dialect.name == "postgresql":
        raise HTTPException(status_code=409, detail="Benefit delivery requires an authoritative campaign claim")

    runtime_connector = connector_with_runtime_secrets(connector)
    try:
        await _do_deliver(db, tenant_id, runtime_connector, body.consumer_id, body.benefit_config)
        return {"status": "processing"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Deliver failed: {exc}")


# ---------------------------------------------------------------------------
# L3: 外部回调（按连接器路由）
# ---------------------------------------------------------------------------


@connector_router.post("/connectors/{conn_id}/callback")
async def delivery_callback_endpoint(
    conn_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """外部系统回调端点 — 不走 JWT 认证，由适配器签名验证保护。"""
    connector = await bootstrap_tenant_row(
        db,
        select(Connector).where(Connector.id == conn_id),
    )
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")

    adapter = get_adapter(connector)

    body = await request.body()
    if len(body) > 1_048_576:
        raise HTTPException(status_code=413, detail="Callback payload too large")
    headers = dict(request.headers)

    # 验证回调签名（必须由适配器实现）
    if not await adapter.verify_callback(connector, body, headers):
        raise HTTPException(status_code=403, detail="Invalid callback signature")

    # 解析回调
    result = await adapter.parse_callback(connector, body, headers)

    if not result.external_id or result.status not in {"success", "failed"}:
        raise HTTPException(status_code=422, detail="Callback result is not settleable")

    deliveries = list(
        (
            await db.execute(
                select(BenefitDelivery)
                .where(
                    BenefitDelivery.tenant_id == connector.tenant_id,
                    BenefitDelivery.connector_id == conn_id,
                    BenefitDelivery.external_id == result.external_id,
                )
                .order_by(BenefitDelivery.id)
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    if len(deliveries) != 1 or deliveries[0].claim_id is None:
        raise HTTPException(status_code=404, detail="Callback delivery not found")
    delivery = deliveries[0]

    if db.get_bind().dialect.name == "postgresql":
        from app.services.campaign_callback_authority import settle_campaign_claim_callback

        await settle_campaign_claim_callback(
            connector.tenant_id,
            conn_id,
            delivery.id,
            delivery.claim_id,
            result.external_id,
            result.status,
            result.external_data,
        )
    else:
        # SQLite-only contract adapter. PostgreSQL never grants direct mutation.
        delivery.status = result.status
        delivery.external_data = {
            **(delivery.external_data or {}),
            **result.external_data,
            "_callback_external_id": result.external_id,
            "_callback_status": result.status,
        }
        if result.status == "success":
            delivery.next_retry_at = None
        await db.flush()

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# L4: 重试
# ---------------------------------------------------------------------------


@connector_router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery_endpoint(
    delivery_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("campaign:manage")),
):
    from app.services.benefit_delivery_handler import _do_deliver

    result = await db.execute(
        select(BenefitDelivery).where(
            BenefitDelivery.id == delivery_id,
            BenefitDelivery.tenant_id == tenant_id,
        )
    )
    delivery = result.scalar_one_or_none()
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")

    if delivery.status == "success":
        return _delivery_to_dict(delivery)
    if db.get_bind().dialect.name == "postgresql":
        raise HTTPException(status_code=409, detail="Campaign claim delivery retries are managed automatically")

    conn_result = await db.execute(
        select(Connector).where(
            Connector.id == delivery.connector_id,
            Connector.tenant_id == tenant_id,
        )
    )
    connector = conn_result.scalar_one_or_none()
    if not connector:
        delivery.status = "failed"
        await db.flush()
        return _delivery_to_dict(delivery)

    runtime_connector = connector_with_runtime_secrets(connector)
    next_delivery = await _do_deliver(
        db,
        tenant_id,
        runtime_connector,
        delivery.consumer_id,
        delivery.benefit_config,
        benefit_id=delivery.benefit_id,
        claim_id=delivery.claim_id,
    )
    return _delivery_to_dict(next_delivery)


@connector_router.get("/deliveries/pending-retries")
async def pending_retries_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("campaign:manage")),
):
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    filters = (
        BenefitDelivery.tenant_id == tenant_id,
        BenefitDelivery.status == "pending",
        BenefitDelivery.retry_count < BenefitDelivery.max_retries,
        BenefitDelivery.next_retry_at <= now,
    )
    total_result = await db.execute(select(func.count()).select_from(BenefitDelivery).where(*filters))
    result = await db.execute(
        select(BenefitDelivery)
        .where(*filters)
        .order_by(BenefitDelivery.created_at, BenefitDelivery.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [_delivery_to_dict(d) for d in result.scalars().all()],
        "total": total_result.scalar_one(),
        "page": page,
        "page_size": page_size,
    }


@connector_router.get("/deliveries/{delivery_id}")
async def get_benefit_delivery_endpoint(
    delivery_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("campaign:manage")),
):
    result = await db.execute(
        select(BenefitDelivery).where(
            BenefitDelivery.id == delivery_id,
            BenefitDelivery.tenant_id == tenant_id,
        )
    )
    delivery = result.scalar_one_or_none()
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return _delivery_to_dict(delivery)
