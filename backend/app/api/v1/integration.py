"""CRM/ERP/商城集成 API"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.integration import batch_import_products, sync_customers, sync_inventory
from app.utils.auth_rbac import require_permission

integration_router = APIRouter(prefix="/api/v1/integration", tags=["integration"])


class BatchImportRequest(BaseModel):
    type: str
    items: list[dict]


class InventorySyncRequest(BaseModel):
    records: list[dict]


class CustomerSyncRequest(BaseModel):
    customers: list[dict]


@integration_router.post(
    "/batch-import",
    summary="批量 import",
    # 目录写权限：与 /imports/excel 同码（admin/operator 均持有，erp_sync API Key 角色亦有）。
    dependencies=[Depends(require_permission("product:create"))],
)
async def batch_import_endpoint(
    body: BatchImportRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    if body.type == "products":
        count = await batch_import_products(db, tenant_id, body.items)
    else:
        count = 0
    return {"imported": count}


@integration_router.post(
    "/erp/inventory-sync",
    summary="ERP 进销存同步",
    # 商品目录更新权限：库存同步属于 ERP/WMS 主数据维护（erp_sync API Key 角色持有 product:update）。
    dependencies=[Depends(require_permission("product:update"))],
)
async def inventory_sync_endpoint(
    body: InventorySyncRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    count = await sync_inventory(db, tenant_id, body.records)
    return {"synced": count}


@integration_router.post(
    "/crm/customer-sync",
    summary="CRM 客户同步",
    # 消费者域权限：与 POST /members/consumers 同码，consumer 域仅 admin/operator 持有。
    dependencies=[Depends(require_permission("consumer:detail"))],
)
async def customer_sync_endpoint(
    body: CustomerSyncRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    count = await sync_customers(db, tenant_id, body.customers)
    return {"synced": count}
