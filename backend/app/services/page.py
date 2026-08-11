"""页面模板服务层"""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import bindparam, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.models.page import PageTemplate, PageTemplateStatus, PageVersion, PageVersionStatus
from app.models.product import Product
from app.schemas.page_dsl import validate_page_dsl
from app.services.audit import write_audit_log
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


def map_page_authority_db_error(exc: DBAPIError) -> HTTPException | None:
    """Translate only the page authority interface's documented SQLSTATEs."""
    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    if sqlstate == "42501":
        return HTTPException(status_code=403, detail="Page mutation authorization is no longer valid")
    if sqlstate == "23503":
        return HTTPException(status_code=404, detail="Page mutation target not found")
    if sqlstate == "22023":
        return HTTPException(status_code=422, detail="Page mutation request is invalid")
    if sqlstate in {"23514", "23505"}:
        return HTTPException(status_code=409, detail="Page mutation conflicts with the current state")
    if sqlstate == "55P03":
        return HTTPException(
            status_code=409,
            detail="Page authority is busy; retry the request",
            headers={"Retry-After": "1"},
        )
    return None


async def _execute_page_authority(db: AsyncSession, statement, parameters: dict):
    try:
        return await db.execute(statement, parameters)
    except DBAPIError as exc:
        mapped = map_page_authority_db_error(exc)
        if mapped is not None:
            raise mapped from exc
        raise


def _page_auth_session_id() -> uuid.UUID:
    from fastapi import HTTPException

    from app.core.database import get_request_security_credential

    credential = get_request_security_credential()
    if credential is None or credential[0] != "auth_session":
        raise HTTPException(status_code=401, detail="Live login session required for page mutations")
    try:
        return uuid.UUID(credential[1])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid login session") from exc


async def _load_page_version_dict(db: AsyncSession, tenant_id: uuid.UUID, version_id: uuid.UUID) -> dict | None:
    version = await db.scalar(
        select(PageVersion).where(PageVersion.tenant_id == tenant_id, PageVersion.id == version_id)
    )
    return _version_to_dict(version) if version else None


def _config_digest(config_json: dict) -> str:
    payload = json.dumps(config_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


async def _audit_sqlite_page_mutation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    resource: str,
    details: dict,
) -> None:
    from app.core.database import _session_uses_postgresql

    if _session_uses_postgresql(db):
        return
    await write_audit_log(db, str(actor_id), str(tenant_id), action, resource, details)


def _required_actor_id(actor_id: uuid.UUID | None) -> uuid.UUID:
    if actor_id is None:
        raise ValueError("actor_id is required for page mutations")
    return actor_id


async def create_page_template(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    template_type: str,
    description: str | None = None,
    product_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> dict:
    from app.core.database import _session_uses_postgresql

    if _session_uses_postgresql(db):
        template_id = uuid7()
        await _execute_page_authority(
            db,
            text(
                "SELECT * FROM public.mutate_page_template("
                ":tenant_id,:auth_session_id,:audit_id,'create',:template_id,:product_id,:name,:template_type,:description)"
            ),
            {
                "tenant_id": tenant_id,
                "auth_session_id": _page_auth_session_id(),
                "audit_id": uuid7(),
                "template_id": template_id,
                "product_id": product_id,
                "name": name,
                "template_type": template_type,
                "description": description,
            },
        )
        template = await db.scalar(
            select(PageTemplate).where(PageTemplate.tenant_id == tenant_id, PageTemplate.id == template_id)
        )
        if template is None:  # pragma: no cover - authoritative function returned an impossible reference
            raise RuntimeError("Page template authority did not persist its result")
        return _template_to_dict(template)
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
    await _audit_sqlite_page_mutation(
        db,
        tenant_id,
        _required_actor_id(actor_id),
        "page_template_created",
        f"page_template:{t.id}",
        {"after": t.status, "product_id": str(product_id) if product_id else None, "template_type": template_type},
    )
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
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    status: str,
) -> PageVersion | None:
    result = await db.execute(
        select(PageVersion)
        .where(
            PageVersion.tenant_id == tenant_id,
            PageVersion.page_template_id == template_id,
            PageVersion.status == status,
        )
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
    published_version = await _latest_version_by_status(db, t.tenant_id, t.id, PageVersionStatus.published)
    draft_version = await _latest_version_by_status(db, t.tenant_id, t.id, PageVersionStatus.draft)
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
    actor_id: uuid.UUID | None = None,
) -> dict | None:
    from app.core.database import _session_uses_postgresql

    if _session_uses_postgresql(db):
        row = (
            (
                await _execute_page_authority(
                    db,
                    text(
                        "SELECT * FROM public.mutate_page_template("
                        ":tenant_id,:auth_session_id,:audit_id,'update',:template_id,:product_id,:name,:template_type,:description)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "auth_session_id": _page_auth_session_id(),
                        "audit_id": uuid7(),
                        "template_id": template_id,
                        "product_id": None,
                        "name": name,
                        "template_type": None,
                        "description": description,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        template = await db.scalar(
            select(PageTemplate).where(PageTemplate.tenant_id == tenant_id, PageTemplate.id == template_id)
        )
        return _template_to_dict(template) if template else None
    result = await db.execute(
        select(PageTemplate).where(
            PageTemplate.id == template_id,
            PageTemplate.tenant_id == tenant_id,
        ),
    )
    t = result.scalar_one_or_none()
    if not t:
        return None
    before = {"name": t.name, "description": t.description, "status": t.status}
    if name is not None:
        t.name = name
    if description is not None:
        t.description = description
    await db.flush()
    await db.refresh(t)
    await _audit_sqlite_page_mutation(
        db,
        tenant_id,
        _required_actor_id(actor_id),
        "page_template_updated",
        f"page_template:{t.id}",
        {"before": before, "after": {"name": t.name, "description": t.description, "status": t.status}},
    )
    return _template_to_dict(t)


async def delete_page_template(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> bool:
    from app.core.database import _session_uses_postgresql

    if _session_uses_postgresql(db):
        row = (
            (
                await _execute_page_authority(
                    db,
                    text(
                        "SELECT * FROM public.mutate_page_template("
                        ":tenant_id,:auth_session_id,:audit_id,'archive',:template_id,NULL,NULL,NULL,NULL)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "auth_session_id": _page_auth_session_id(),
                        "audit_id": uuid7(),
                        "template_id": template_id,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        return row is not None
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
    await _audit_sqlite_page_mutation(
        db,
        tenant_id,
        _required_actor_id(actor_id),
        "page_template_archived",
        f"page_template:{t.id}",
        {"before": PageTemplateStatus.active, "after": PageTemplateStatus.archived},
    )
    return True


async def create_page_version(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    config_json: dict,
    created_by: uuid.UUID,
    *,
    _audit_action: str = "page_version_created",
    _audit_source_version_id: uuid.UUID | None = None,
) -> dict | None:
    # 消毒 custom_html 模块中的 HTML 内容
    sanitize_config_html(config_json)

    # 后端 DSL 校验
    validate_page_dsl(config_json)

    from app.core.database import _session_uses_postgresql

    if _session_uses_postgresql(db):
        version_id = uuid7()
        statement = text(
            "SELECT * FROM public.create_page_version("
            ":tenant_id,:auth_session_id,:audit_id,:version_id,:template_id,:config)"
        ).bindparams(bindparam("config", type_=JSONB))
        row = (
            (
                await _execute_page_authority(
                    db,
                    statement,
                    {
                        "tenant_id": tenant_id,
                        "auth_session_id": _page_auth_session_id(),
                        "audit_id": uuid7(),
                        "version_id": version_id,
                        "template_id": template_id,
                        "config": config_json,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        from app.services.launch import invalidate_launch_releases_for_template

        await invalidate_launch_releases_for_template(db, tenant_id, template_id, created_by)
        return await _load_page_version_dict(db, tenant_id, version_id)

    # Lock the tenant-scoped parent row so concurrent creates for one template
    # serialize before calculating the next version number. PostgreSQL forbids
    # FOR UPDATE on aggregate queries, so the max query itself must stay unlocked.
    template_result = await db.execute(
        select(PageTemplate)
        .where(
            PageTemplate.id == template_id,
            PageTemplate.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if template_result.scalar_one_or_none() is None:
        return None

    max_ver_result = await db.execute(
        select(func.max(PageVersion.version)).where(
            PageVersion.page_template_id == template_id,
            PageVersion.tenant_id == tenant_id,
        )
    )
    max_ver = max_ver_result.scalar() or 0

    v = PageVersion(
        tenant_id=tenant_id,
        page_template_id=template_id,
        version=max_ver + 1,
        config_json=config_json,
        created_by_tenant_id=tenant_id,
        created_by=created_by,
    )
    db.add(v)
    await db.flush()
    await db.refresh(v)
    from app.services.launch import invalidate_launch_releases_for_template

    await invalidate_launch_releases_for_template(db, tenant_id, template_id, created_by)
    await _audit_sqlite_page_mutation(
        db,
        tenant_id,
        created_by,
        _audit_action,
        f"page_version:{v.id}",
        {
            "template_id": str(template_id),
            "source_version_id": str(_audit_source_version_id) if _audit_source_version_id else None,
            "version": v.version,
            "config_sha256": _config_digest(config_json),
        },
    )
    return _version_to_dict(v)


async def update_page_version(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    version_id: uuid.UUID,
    config_json: dict,
    actor_id: uuid.UUID | None = None,
) -> dict | None:
    sanitize_config_html(config_json)
    validate_page_dsl(config_json)
    from app.core.database import _session_uses_postgresql

    if _session_uses_postgresql(db):
        statement = text(
            "SELECT * FROM public.update_page_version(:tenant_id,:auth_session_id,:audit_id,:version_id,:config)"
        ).bindparams(bindparam("config", type_=JSONB))
        row = (
            (
                await _execute_page_authority(
                    db,
                    statement,
                    {
                        "tenant_id": tenant_id,
                        "auth_session_id": _page_auth_session_id(),
                        "audit_id": uuid7(),
                        "version_id": version_id,
                        "config": config_json,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        return await _load_page_version_dict(db, tenant_id, row["page_version_id"]) if row else None
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

    before_digest = _config_digest(v.config_json)
    v.config_json = config_json
    await db.flush()
    await db.refresh(v)
    await _audit_sqlite_page_mutation(
        db,
        tenant_id,
        _required_actor_id(actor_id),
        "page_version_updated",
        f"page_version:{v.id}",
        {
            "template_id": str(v.page_template_id),
            "before_sha256": before_digest,
            "after_sha256": _config_digest(config_json),
        },
    )
    return _version_to_dict(v)


async def publish_page_version(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    version_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> dict | None:
    from app.core.database import _session_uses_postgresql

    if _session_uses_postgresql(db):
        row = (
            (
                await _execute_page_authority(
                    db,
                    text(
                        "SELECT * FROM public.publish_page_version(:tenant_id,:auth_session_id,:audit_id,:version_id)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "auth_session_id": _page_auth_session_id(),
                        "audit_id": uuid7(),
                        "version_id": version_id,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        return await _load_page_version_dict(db, tenant_id, row["page_version_id"]) if row else None
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
    await _audit_sqlite_page_mutation(
        db,
        tenant_id,
        _required_actor_id(actor_id),
        "page_version_published",
        f"page_version:{v.id}",
        {"template_id": str(v.page_template_id), "before": "draft", "after": "published"},
    )
    return _version_to_dict(v)


async def archive_page_version(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    version_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> dict | None:
    from app.core.database import _session_uses_postgresql

    if _session_uses_postgresql(db):
        row = (
            (
                await _execute_page_authority(
                    db,
                    text(
                        "SELECT * FROM public.archive_page_version(:tenant_id,:auth_session_id,:audit_id,:version_id)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "auth_session_id": _page_auth_session_id(),
                        "audit_id": uuid7(),
                        "version_id": version_id,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        return await _load_page_version_dict(db, tenant_id, row["page_version_id"]) if row else None
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
    prior_status = v.status
    _validate_transition(v.status, PageVersionStatus.archived)
    v.status = PageVersionStatus.archived
    await db.flush()
    await db.refresh(v)
    await _audit_sqlite_page_mutation(
        db,
        tenant_id,
        _required_actor_id(actor_id),
        "page_version_archived",
        f"page_version:{v.id}",
        {"template_id": str(v.page_template_id), "before": prior_status, "after": "archived"},
    )
    return _version_to_dict(v)


async def rollback_page_version(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    target_version_id: uuid.UUID,
    created_by: uuid.UUID,
) -> dict | None:
    """回滚到指定历史版本：创建新版本，config_json 复制自目标版本"""
    from app.core.database import _session_uses_postgresql

    if _session_uses_postgresql(db):
        new_version_id = uuid7()
        row = (
            (
                await _execute_page_authority(
                    db,
                    text(
                        "SELECT * FROM public.rollback_page_version("
                        ":tenant_id,:auth_session_id,:audit_id,:new_version_id,:template_id,:source_version_id)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "auth_session_id": _page_auth_session_id(),
                        "audit_id": uuid7(),
                        "new_version_id": new_version_id,
                        "template_id": template_id,
                        "source_version_id": target_version_id,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        from app.services.launch import invalidate_launch_releases_for_template

        await invalidate_launch_releases_for_template(db, tenant_id, template_id, created_by)
        return await _load_page_version_dict(db, tenant_id, new_version_id)
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

    created = await create_page_version(
        db,
        tenant_id,
        template_id,
        dict(target_ver.config_json),
        created_by,
        _audit_action="page_version_rolled_back",
        _audit_source_version_id=target_version_id,
    )
    return created


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
