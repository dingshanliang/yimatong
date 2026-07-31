"""页面模板服务层"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.page import PageTemplate, PageTemplateStatus, PageVersion, PageVersionStatus
from app.models.product import Product
from app.schemas.page_dsl import validate_page_dsl
from app.utils.html_sanitizer import sanitize_config_html

# ── 版本状态机合法转换 ──────────────────────────────────
VERSION_TRANSITIONS: dict[str, set[str]] = {
    PageVersionStatus.draft: {PageVersionStatus.published, PageVersionStatus.archived},
    PageVersionStatus.published: {PageVersionStatus.archived},
    PageVersionStatus.archived: set(),  # 终态
}


class VersionStateError(ValueError):
    """版本状态转换不合法"""


class VersionImmutableError(ValueError):
    """已发布/归档版本不可修改"""


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
    product_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    stmt = (
        select(PageTemplate, Product.name.label("product_name"))
        .outerjoin(
            Product,
            (Product.id == PageTemplate.product_id) & (Product.tenant_id == tenant_id),
        )
        .where(PageTemplate.tenant_id == tenant_id)
    )
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
    if product_id:
        stmt = stmt.where(PageTemplate.product_id == product_id)
        count_stmt = count_stmt.where(PageTemplate.product_id == product_id)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(PageTemplate.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return [], total

    # 批量查询所有模板的 published + draft 版本（替代 N+1 逐条查询）
    template_ids = [t.id for t, _ in rows]
    templates = [_template_to_dict_simple(t, product_name) for t, product_name in rows]

    versions_result = await db.execute(
        select(PageVersion)
        .where(
            PageVersion.page_template_id.in_(template_ids),
            PageVersion.tenant_id == tenant_id,
            PageVersion.status.in_([PageVersionStatus.published, PageVersionStatus.draft]),
        )
        .order_by(PageVersion.version.desc())
    )
    # 按 template_id 分组
    versions_by_template: dict[str, list[PageVersion]] = {}
    for v in versions_result.scalars().all():
        versions_by_template.setdefault(str(v.page_template_id), []).append(v)

    for tmpl_dict in templates:
        tmpl_versions = versions_by_template.get(tmpl_dict["id"], [])
        published = next((v for v in tmpl_versions if v.status == PageVersionStatus.published), None)
        draft = next((v for v in tmpl_versions if v.status == PageVersionStatus.draft), None)
        tmpl_dict["published_version"] = _version_to_dict(published) if published else None
        tmpl_dict["draft_version"] = _version_to_dict(draft) if draft else None
        if published and draft:
            tmpl_dict["display_status"] = "has_unpublished_draft"
        elif published:
            tmpl_dict["display_status"] = "published"
        else:
            tmpl_dict["display_status"] = "unpublished"
        if draft:
            tmpl_dict["updated_at"] = _format_dt(draft.updated_at)
        elif published:
            tmpl_dict["updated_at"] = _format_dt(published.updated_at)

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

    product_name = None
    if t.product_id:
        product_result = await db.execute(
            select(Product.name).where(Product.id == t.product_id, Product.tenant_id == tenant_id)
        )
        product_name = product_result.scalar_one_or_none()

    return await _template_to_dict_with_versions(db, t, product_name)


async def _latest_version_by_status(
    db: AsyncSession,
    template_id: uuid.UUID,
    status: str,
) -> PageVersion | None:
    result = await db.execute(
        select(PageVersion)
        .where(PageVersion.page_template_id == template_id, PageVersion.status == status)
        .order_by(PageVersion.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _template_to_dict_with_versions(
    db: AsyncSession,
    t: PageTemplate,
    product_name: str | None,
) -> dict:
    data = _template_to_dict(t)
    published_version = await _latest_version_by_status(db, t.id, PageVersionStatus.published)
    draft_version = await _latest_version_by_status(db, t.id, PageVersionStatus.draft)
    data["product_name"] = product_name
    data["published_version"] = _version_to_dict(published_version) if published_version else None
    data["draft_version"] = _version_to_dict(draft_version) if draft_version else None
    if published_version and draft_version:
        data["display_status"] = "has_unpublished_draft"
    elif published_version:
        data["display_status"] = "published"
    else:
        data["display_status"] = "unpublished"
    if draft_version:
        data["updated_at"] = _format_dt(draft_version.updated_at)
    elif published_version:
        data["updated_at"] = _format_dt(published_version.updated_at)
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
    # 消毒 custom_html 模块中的 HTML 内容
    sanitize_config_html(config_json)

    # 后端 DSL 校验
    validate_page_dsl(config_json)

    # 获取当前最大版本号（加行级锁防止并发重复）
    max_ver_result = await db.execute(
        select(func.max(PageVersion.version))
        .where(
            PageVersion.page_template_id == template_id,
        )
        .with_for_update()
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
    from app.services.launch import invalidate_launch_releases_for_template

    await invalidate_launch_releases_for_template(db, tenant_id, template_id, created_by)
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
    # 已发布/归档版本不可修改
    if v.status != PageVersionStatus.draft:
        raise VersionImmutableError(
            f"Cannot modify version in '{v.status}' status. Only draft versions can be updated."
        )

    # 消毒 custom_html 模块
    sanitize_config_html(config_json)

    # 后端 DSL 校验
    validate_page_dsl(config_json)

    v.config_json = config_json
    await db.flush()
    await db.refresh(v)
    # 失效公共解析缓存：页面配置变更后消费者页立即看到新 DSL（yimatong-zgb1.2 AC1）
    from app.services.resolver_response import invalidate_page_config_cache

    await invalidate_page_config_cache(v.page_template_id)
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

    # 状态机校验：仅 draft 可发布
    _validate_transition(v.status, PageVersionStatus.published)

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
    v.published_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(v)
    # 失效公共解析缓存：发布新版本后消费者页立即用新 DSL（yimatong-zgb1.2 AC1）
    from app.services.resolver_response import invalidate_page_config_cache

    await invalidate_page_config_cache(v.page_template_id)
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
    # 状态机校验
    _validate_transition(v.status, PageVersionStatus.archived)
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
        "created_at": _format_dt(t.created_at),
        "updated_at": _format_dt(t.updated_at),
    }


def _template_to_dict_simple(t: PageTemplate, product_name: str | None) -> dict:
    """模板转 dict（不含版本信息，用于批量列表）"""
    data = _template_to_dict(t)
    data["product_name"] = product_name
    return data


def _version_to_dict(v: PageVersion) -> dict:
    return {
        "id": str(v.id),
        "tenant_id": str(v.tenant_id),
        "page_template_id": str(v.page_template_id),
        "version": v.version,
        "config_json": v.config_json,
        "status": v.status,
        "created_by": str(v.created_by),
        "published_at": _format_dt(v.published_at),
        "created_at": _format_dt(v.created_at),
        "updated_at": _format_dt(v.updated_at),
    }


def _format_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _validate_transition(current_status: str, target_status: str) -> None:
    """校验版本状态转换是否合法，不合法则抛 VersionStateError"""
    allowed = VERSION_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise VersionStateError(
            f"Cannot transition version from '{current_status}' to '{target_status}'. "
            f"Allowed transitions from '{current_status}': {sorted(allowed) or 'none (terminal state)'}"
        )
