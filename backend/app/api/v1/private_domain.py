"""私域承接配置 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import JSON, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.models.base import Base

private_domain_router = APIRouter(prefix="/api/v1/private-domain-configs", tags=["private-domain"])


class PrivateDomainConfig(Base):
    __tablename__ = "private_domain_configs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    config_type: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


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
    result = await db.execute(
        select(PrivateDomainConfig).where(PrivateDomainConfig.tenant_id == tenant_id)
    )
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
    await db.commit()
    await db.refresh(config)
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
    await db.commit()
    return {
        "id": str(config.id),
        "config_type": config.config_type,
        "name": config.name,
        "config": config.config,
    }
