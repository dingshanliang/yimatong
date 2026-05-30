"""外部权益连接器 API

L2: 券码池管理 + 库存同步
L3: API 发券 + 回调接收
L4: 失败重试
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.models.connector import Connector
from app.services.connectors import get_adapter
from app.services.connectors.secrets import decrypt_secrets, encrypt_secrets, mask_secrets

connector_router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])


# ---------------------------------------------------------------------------
# Request/Response schemas
# ---------------------------------------------------------------------------


class CouponPoolCreate(BaseModel):
    name: str
    codes: list[str]


class DistributeRequest(BaseModel):
    consumer_id: str


class ConnectorCreate(BaseModel):
    name: str
    connector_type: str
    config: dict = {}
    secrets: dict | None = None


class ConnectorUpdate(BaseModel):
    name: str | None = None
    config: dict | None = None
    secrets: dict | None = None
    enabled: bool | None = None


class DeliverBenefitRequest(BaseModel):
    consumer_id: str
    benefit_type: str
    benefit_config: dict = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_connector_or_404(
    db: AsyncSession, tenant_id: uuid.UUID, conn_id: uuid.UUID
) -> Connector:
    result = await db.execute(
        select(Connector).where(Connector.id == conn_id, Connector.tenant_id == tenant_id)
    )
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
        "config": connector.config,
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


def _inject_secrets(connector: Connector) -> None:
    """将加密凭证解密后注入 connector.config。"""
    if connector.secrets_encrypted:
        secrets = decrypt_secrets(connector.secrets_encrypted)
        connector.config = {**connector.config, **secrets}


def _delivery_to_dict(delivery) -> dict:
    return {
        "id": str(delivery.id),
        "tenant_id": str(delivery.tenant_id),
        "connector_id": str(delivery.connector_id),
        "consumer_id": delivery.consumer_id,
        "benefit_type": delivery.benefit_type,
        "status": delivery.status,
        "retry_count": delivery.retry_count,
        "max_retries": delivery.max_retries,
        "external_data": delivery.external_data,
        "next_retry_at": delivery.next_retry_at.isoformat() if delivery.next_retry_at else None,
    }


# ---------------------------------------------------------------------------
# 券码池 CRUD
# ---------------------------------------------------------------------------


@connector_router.post("/coupon-pools", status_code=201)
async def create_pool_endpoint(
    body: CouponPoolCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from app.services.connector import create_coupon_pool

    pool = await create_coupon_pool(db, tenant_id, body.name, body.codes)
    return {
        "id": str(pool.id),
        "name": pool.name,
        "total_codes": pool.total_codes,
        "remaining": pool.remaining,
    }


@connector_router.post("/coupon-pools/{pool_id}/distribute")
async def distribute_endpoint(
    pool_id: uuid.UUID,
    body: DistributeRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from app.services.connector import distribute_coupon

    code = await distribute_coupon(db, pool_id, body.consumer_id)
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


@connector_router.post("/connectors", status_code=201)
async def create_connector_endpoint(
    body: ConnectorCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from app.services.connector import create_connector as _create

    # 验证适配器类型和配置
    stub = Connector(
        tenant_id=tenant_id, name=body.name,
        connector_type=body.connector_type, config=body.config, enabled=True,
    )
    adapter = get_adapter(stub)
    is_valid, error = await adapter.validate_config(body.config)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Invalid config: {error}")

    secrets_encrypted = encrypt_secrets(body.secrets) if body.secrets else None
    conn = await _create(db, tenant_id, body.name, body.connector_type, body.config)
    if secrets_encrypted:
        conn.secrets_encrypted = secrets_encrypted
        await db.flush()
        await db.refresh(conn)

    return _connector_to_dict(conn, include_secrets=True)


@connector_router.get("/connectors")
async def list_connectors_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from app.services.connector import list_connectors

    conns = await list_connectors(db, tenant_id)
    return [_connector_to_dict(c, include_secrets=True) for c in conns]


@connector_router.get("/connectors/types")
async def list_connector_types_endpoint():
    """返回系统支持的所有连接器类型。"""
    from app.services.connectors.registry import list_adapter_types
    return {"types": list_adapter_types()}


@connector_router.get("/connectors/{conn_id}")
async def get_connector_endpoint(
    conn_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    connector = await _get_connector_or_404(db, tenant_id, conn_id)
    return _connector_to_dict(connector, include_secrets=True)


@connector_router.post("/connectors/{conn_id}/test")
async def test_connection_endpoint(
    conn_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from app.services.connector import test_connection

    connector = await _get_connector_or_404(db, tenant_id, conn_id)
    return await test_connection(connector)


@connector_router.patch("/connectors/{conn_id}")
async def update_connector_endpoint(
    conn_id: uuid.UUID,
    body: ConnectorUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from app.services.connector import update_connector

    updates = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.config is not None:
        updates["config"] = body.config
    if body.enabled is not None:
        updates["enabled"] = body.enabled

    if body.secrets is not None:
        updates["secrets_encrypted"] = encrypt_secrets(body.secrets)

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
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    connector = await _get_connector_or_404(db, tenant_id, conn_id)
    if not connector.enabled:
        raise HTTPException(status_code=400, detail="Connector is disabled")

    adapter = get_adapter(connector)
    try:
        _inject_secrets(connector)
        available = await adapter.sync_stock(connector)
        connector.config = {**connector.config, "stock": {"available": available}}
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
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from app.services.benefit_delivery_handler import _do_deliver

    connector = await _get_connector_or_404(db, tenant_id, conn_id)
    if not connector.enabled:
        raise HTTPException(status_code=400, detail="Connector is disabled")

    _inject_secrets(connector)
    try:
        await _do_deliver(db, tenant_id, connector, body.consumer_id, body.benefit_config)
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
    db: AsyncSession = Depends(get_db),
):
    """外部系统回调端点 — 不走 JWT 认证，由适配器签名验证保护。"""
    conn_result = await db.execute(
        select(Connector).where(Connector.id == conn_id)
    )
    connector = conn_result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")

    adapter = get_adapter(connector)

    body = await request.body()
    headers = dict(request.headers)

    # 验证回调签名（必须由适配器实现）
    if not await adapter.verify_callback(connector, body, headers):
        raise HTTPException(status_code=403, detail="Invalid callback signature")

    # 解析回调
    result = await adapter.parse_callback(connector, body, headers)

    # 查找匹配的 BenefitDelivery 并更新状态
    if result.external_id:
        from sqlalchemy import func as sa_func

        from app.models.connector import BenefitDelivery

        # 优先按 out_bill_no 精确匹配（适用于微信转账等带唯一单号的场景）
        delivery = None
        delivery_stmt = (
            select(BenefitDelivery)
            .where(
                BenefitDelivery.tenant_id == connector.tenant_id,
                BenefitDelivery.connector_id == conn_id,
                BenefitDelivery.status == "pending",
                sa_func.jsonb_extract_path_text(BenefitDelivery.benefit_config, "out_bill_no") == result.external_id,
            )
        )
        delivery_row = await db.execute(delivery_stmt)
        delivery = delivery_row.scalar_one_or_none()

        # 回退：按最新 pending 匹配
        if not delivery:
            delivery_stmt = (
                select(BenefitDelivery)
                .where(
                    BenefitDelivery.tenant_id == connector.tenant_id,
                    BenefitDelivery.connector_id == conn_id,
                    BenefitDelivery.status == "pending",
                )
                .order_by(BenefitDelivery.created_at.desc())
                .limit(1)
            )
            delivery_row = await db.execute(delivery_stmt)
            delivery = delivery_row.scalar_one_or_none()

        if delivery:
            delivery.status = result.status
            delivery.external_data = result.external_data
            if result.status == "success":
                delivery.next_retry_at = None

                # 同步更新关联 BenefitClaim 状态
                from sqlalchemy import update as sa_update

                from app.models.campaign import BenefitClaim

                out_bill_no = delivery.benefit_config.get("out_bill_no") if delivery.benefit_config else None
                if out_bill_no:
                    try:
                        claim_id = uuid.UUID(out_bill_no)
                        await db.execute(
                            sa_update(BenefitClaim)
                            .where(BenefitClaim.id == claim_id)
                            .values(status="delivered")
                        )
                    except ValueError:
                        pass

            await db.flush()

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# L4: 重试
# ---------------------------------------------------------------------------


@connector_router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery_endpoint(
    delivery_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from app.models.connector import BenefitDelivery
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

    if delivery.status in ("success", "failed"):
        return _delivery_to_dict(delivery)

    conn_result = await db.execute(select(Connector).where(Connector.id == delivery.connector_id))
    connector = conn_result.scalar_one_or_none()
    if not connector:
        delivery.status = "failed"
        await db.flush()
        return _delivery_to_dict(delivery)

    _inject_secrets(connector)
    await _do_deliver(db, tenant_id, connector, delivery.consumer_id, delivery.benefit_config)
    return _delivery_to_dict(delivery)


@connector_router.get("/deliveries/pending-retries")
async def pending_retries_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from datetime import UTC, datetime

    from app.models.connector import BenefitDelivery

    now = datetime.now(UTC)
    result = await db.execute(
        select(BenefitDelivery)
        .where(
            BenefitDelivery.tenant_id == tenant_id,
            BenefitDelivery.status == "pending",
            BenefitDelivery.retry_count < BenefitDelivery.max_retries,
            BenefitDelivery.next_retry_at <= now,
        )
        .order_by(BenefitDelivery.created_at)
    )
    return [_delivery_to_dict(d) for d in result.scalars().all()]
