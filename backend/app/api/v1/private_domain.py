"""私域承接配置 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.models.private_domain import PrivateDomainConfig
from app.services.audit import write_audit_log
from app.utils.auth_rbac import require_permission

private_domain_router = APIRouter(prefix="/api/v1/private-domain-configs", tags=["private-domain"])


class PrivateDomainConfigCreate(BaseModel):
    config_type: str
    name: str
    config: dict = {}


class PrivateDomainConfigUpdate(BaseModel):
    name: str | None = None
    config: dict | None = None


@private_domain_router.get("")
async def list_configs(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    result = await db.execute(select(PrivateDomainConfig).where(PrivateDomainConfig.tenant_id == tenant_id))
    configs = result.scalars().all()
    return {
        "items": [
            {
                "id": str(c.id),
                "config_type": c.config_type,
                "name": c.name,
                "config": c.config,
            }
            for c in configs
        ],
        "total": len(configs),
    }


@private_domain_router.post("", status_code=201)
async def create_config(
    body: PrivateDomainConfigCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _permission: None = Depends(require_permission("tenant:manage")),
):
    from uuid6 import uuid7

    config = PrivateDomainConfig(
        id=uuid7(),
        tenant_id=tenant_id,
        config_type=body.config_type,
        name=body.name,
        config=body.config,
    )
    db.add(config)
    await db.flush()
    await write_audit_log(
        db,
        operator_id=str(account_id),
        target_tenant_id=str(tenant_id),
        action="private_domain_config_create",
        resource=f"private_domain_config:{config.id}",
        details={"config_type": config.config_type, "name": config.name},
    )
    return {
        "id": str(config.id),
        "config_type": config.config_type,
        "name": config.name,
        "config": config.config,
    }


@private_domain_router.patch("/{config_id}")
async def update_config(
    config_id: uuid.UUID,
    body: PrivateDomainConfigUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _permission: None = Depends(require_permission("tenant:manage")),
):
    result = await db.execute(
        select(PrivateDomainConfig).where(
            PrivateDomainConfig.id == config_id,
            PrivateDomainConfig.tenant_id == tenant_id,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    if body.name is not None:
        config.name = body.name
    if body.config is not None:
        config.config = body.config
    await db.flush()
    await write_audit_log(
        db,
        operator_id=str(account_id),
        target_tenant_id=str(tenant_id),
        action="private_domain_config_update",
        resource=f"private_domain_config:{config.id}",
    )
    return {
        "id": str(config.id),
        "config_type": config.config_type,
        "name": config.name,
        "config": config.config,
    }
