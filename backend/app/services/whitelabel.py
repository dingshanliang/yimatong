"""白标与自定义域名管理服务。"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.regional import TenantDomain, WhitelabelConfig

logger = logging.getLogger(__name__)


# ── 白标配置 ──────────────────────────────────────


async def get_whitelabel_config(db: AsyncSession, org_id: uuid.UUID) -> WhitelabelConfig | None:
    result = await db.execute(select(WhitelabelConfig).where(WhitelabelConfig.org_id == org_id))
    return result.scalar_one_or_none()


async def update_whitelabel_config(
    db: AsyncSession,
    org_id: uuid.UUID,
    **kwargs,
) -> WhitelabelConfig:
    result = await db.execute(select(WhitelabelConfig).where(WhitelabelConfig.org_id == org_id))
    config = result.scalar_one_or_none()
    if config:
        for key, value in kwargs.items():
            if hasattr(config, key) and value is not None:
                setattr(config, key, value)
    else:
        config = WhitelabelConfig(org_id=org_id, **kwargs)
        db.add(config)
    await db.flush()
    await db.refresh(config)
    return config


async def get_whitelabel_by_domain(db: AsyncSession, domain: str) -> WhitelabelConfig | None:
    """通过域名查找关联的白标配置（用于中间件路由）。"""
    domain_result = await db.execute(
        select(TenantDomain).where(TenantDomain.domain == domain, TenantDomain.verified.is_(True))
    )
    tenant_domain = domain_result.scalar_one_or_none()
    if not tenant_domain:
        return None

    # 通过 tenant_id 查找关联的 org（RegionalOrg）
    from app.models.regional import RegionalOrg

    org_result = await db.execute(select(RegionalOrg).where(RegionalOrg.tenant_id == tenant_domain.tenant_id))
    org = org_result.scalar_one_or_none()
    if not org:
        return None

    return await get_whitelabel_config(db, org.id)


# ── 域名管理 ──────────────────────────────────────


async def add_domain(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    domain: str,
) -> TenantDomain:
    td = TenantDomain(
        tenant_id=tenant_id,
        domain=domain.lower().strip(),
    )
    db.add(td)
    await db.flush()
    await db.refresh(td)
    return td


async def list_domains(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[TenantDomain]:
    result = await db.execute(
        select(TenantDomain).where(TenantDomain.tenant_id == tenant_id).order_by(TenantDomain.id.desc())
    )
    return list(result.scalars().all())


async def verify_domain(
    db: AsyncSession,
    domain_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> bool:
    """验证域名（简化版：标记为已验证）。生产环境应检查 CNAME 记录。"""
    result = await db.execute(
        select(TenantDomain).where(TenantDomain.id == domain_id, TenantDomain.tenant_id == tenant_id)
    )
    td = result.scalar_one_or_none()
    if not td:
        return False
    td.verified = True
    td.ssl_status = "active"
    await db.flush()
    return True


async def remove_domain(
    db: AsyncSession,
    domain_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        select(TenantDomain).where(TenantDomain.id == domain_id, TenantDomain.tenant_id == tenant_id)
    )
    td = result.scalar_one_or_none()
    if not td:
        return False
    await db.delete(td)
    await db.flush()
    return True
