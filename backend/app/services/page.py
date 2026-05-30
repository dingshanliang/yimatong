"""页面模板服务层"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.page import PageTemplate, PageTemplateStatus, PageVersion, PageVersionStatus


async def create_page_template(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    template_type: str,
    description: str | None = None,
    product_id: uuid.UUID | None = None,
) -> dict:
    t = PageTemplate(
        tenant_id=tenant_id,
        name=name,
        template_type=template_type,
        description=description,
        product_id=product_id,
    )
    db.add(t)
    await db.flush()
    await db.refresh(t)
    return _template_to_dict(t)


async def list_page_templates(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_type: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    stmt = select(PageTemplate).where(PageTemplate.tenant_id == tenant_id)
    count_stmt = (
        select(func.count())
        .select_from(PageTemplate)
        .where(
            PageTemplate.tenant_id == tenant_id,
        )
    )

    if template_type:
        stmt = stmt.where(PageTemplate.template_type == template_type)
        count_stmt = count_stmt.where(PageTemplate.template_type == template_type)
    if status:
        stmt = stmt.where(PageTemplate.status == status)
        count_stmt = count_stmt.where(PageTemplate.status == status)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(PageTemplate.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    templates = [_template_to_dict(t) for t in result.scalars().all()]
    return templates, total


async def get_page_template(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
) -> dict | None:
    result = await db.execute(
        select(PageTemplate).where(
            PageTemplate.id == template_id,
            PageTemplate.tenant_id == tenant_id,
        ),
    )
    t = result.scalar_one_or_none()
    if not t:
        return None

    data = _template_to_dict(t)

    # 获取当前发布版本
    ver_result = await db.execute(
        select(PageVersion)
        .where(
            PageVersion.page_template_id == template_id,
            PageVersion.status == PageVersionStatus.published,
        )
        .limit(1)
    )
    published_version = ver_result.scalar_one_or_none()
    if published_version:
        data["published_version"] = _version_to_dict(published_version)
    else:
        data["published_version"] = None

    return data


async def update_page_template(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    name: str | None = None,
    description: str | None = None,
) -> dict | None:
    result = await db.execute(
        select(PageTemplate).where(
            PageTemplate.id == template_id,
            PageTemplate.tenant_id == tenant_id,
        ),
    )
    t = result.scalar_one_or_none()
    if not t:
        return None
    if name is not None:
        t.name = name
    if description is not None:
        t.description = description
    await db.flush()
    await db.refresh(t)
    return _template_to_dict(t)


async def delete_page_template(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        select(PageTemplate).where(
            PageTemplate.id == template_id,
            PageTemplate.tenant_id == tenant_id,
        ),
    )
    t = result.scalar_one_or_none()
    if not t:
        return False
    t.status = PageTemplateStatus.archived
    await db.flush()
    return True


async def create_page_version(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    config_json: dict,
    created_by: uuid.UUID,
) -> dict:
    # 获取当前最大版本号
    max_ver_result = await db.execute(
        select(func.max(PageVersion.version)).where(
            PageVersion.page_template_id == template_id,
        )
    )
    max_ver = max_ver_result.scalar() or 0

    v = PageVersion(
        tenant_id=tenant_id,
        page_template_id=template_id,
        version=max_ver + 1,
        config_json=config_json,
        created_by=created_by,
    )
    db.add(v)
    await db.flush()
    await db.refresh(v)
    return _version_to_dict(v)


async def update_page_version(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    version_id: uuid.UUID,
    config_json: dict,
) -> dict | None:
    result = await db.execute(
        select(PageVersion).where(
            PageVersion.id == version_id,
            PageVersion.tenant_id == tenant_id,
        ),
    )
    v = result.scalar_one_or_none()
    if not v:
        return None
    v.config_json = config_json
    await db.flush()
    await db.refresh(v)
    return _version_to_dict(v)


async def publish_page_version(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    version_id: uuid.UUID,
) -> dict | None:
    result = await db.execute(
        select(PageVersion).where(
            PageVersion.id == version_id,
            PageVersion.tenant_id == tenant_id,
        ),
    )
    v = result.scalar_one_or_none()
    if not v:
        return None

    # 归档同模板下其他已发布版本
    old_published = await db.execute(
        select(PageVersion).where(
            PageVersion.page_template_id == v.page_template_id,
            PageVersion.status == PageVersionStatus.published,
        )
    )
    for old in old_published.scalars().all():
        old.status = PageVersionStatus.archived

    v.status = PageVersionStatus.published
    await db.flush()
    await db.refresh(v)
    return _version_to_dict(v)


async def archive_page_version(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    version_id: uuid.UUID,
) -> dict | None:
    result = await db.execute(
        select(PageVersion).where(
            PageVersion.id == version_id,
            PageVersion.tenant_id == tenant_id,
        ),
    )
    v = result.scalar_one_or_none()
    if not v:
        return None
    v.status = PageVersionStatus.archived
    await db.flush()
    await db.refresh(v)
    return _version_to_dict(v)


async def rollback_page_version(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    target_version_id: uuid.UUID,
    created_by: uuid.UUID,
) -> dict | None:
    """回滚到指定历史版本：创建新版本，config_json 复制自目标版本"""
    target = await db.execute(
        select(PageVersion).where(
            PageVersion.id == target_version_id,
            PageVersion.tenant_id == tenant_id,
            PageVersion.page_template_id == template_id,
        )
    )
    target_ver = target.scalar_one_or_none()
    if not target_ver:
        return None

    return await create_page_version(
        db,
        tenant_id,
        template_id,
        dict(target_ver.config_json),
        created_by,
    )


async def list_page_versions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
) -> list[dict]:
    result = await db.execute(
        select(PageVersion)
        .where(
            PageVersion.page_template_id == template_id,
            PageVersion.tenant_id == tenant_id,
        )
        .order_by(PageVersion.version.desc())
    )
    return [_version_to_dict(v) for v in result.scalars().all()]


def _template_to_dict(t: PageTemplate) -> dict:
    return {
        "id": str(t.id),
        "tenant_id": str(t.tenant_id),
        "product_id": str(t.product_id) if t.product_id else None,
        "name": t.name,
        "template_type": t.template_type,
        "status": t.status,
        "description": t.description,
    }


def _version_to_dict(v: PageVersion) -> dict:
    return {
        "id": str(v.id),
        "tenant_id": str(v.tenant_id),
        "page_template_id": str(v.page_template_id),
        "version": v.version,
        "config_json": v.config_json,
        "status": v.status,
        "created_by": str(v.created_by),
    }
