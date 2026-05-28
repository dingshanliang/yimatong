"""页面渲染引擎"""

import uuid
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.page import PageTemplate, PageVersion, PageVersionStatus

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "pages"

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

# 简单内存缓存（生产环境用 Redis）
_render_cache: dict[str, str] = {}


async def render_page(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    context: dict | None = None,
) -> str | None:
    """渲染页面模板为 HTML"""
    # 检查缓存
    cache_key = f"page:{template_id}"
    if context is None and cache_key in _render_cache:
        return _render_cache[cache_key]

    # 获取已发布版本
    ver_result = await db.execute(
        select(PageVersion).where(
            PageVersion.page_template_id == template_id,
            PageVersion.tenant_id == tenant_id,
            PageVersion.status == PageVersionStatus.published,
        ).limit(1)
    )
    version = ver_result.scalar_one_or_none()
    if not version:
        return None

    # 获取模板信息
    tmpl_result = await db.execute(
        select(PageTemplate).where(PageTemplate.id == template_id)
    )
    template = tmpl_result.scalar_one_or_none()
    if not template:
        return None

    # 构建渲染上下文
    render_ctx = await _build_context(db, tenant_id, version.config_json, context)

    # 渲染模板
    template_file = f"{template.template_type}.html"
    try:
        tmpl = _jinja_env.get_template(template_file)
    except Exception:
        # 模板文件不存在，使用 fallback 简单渲染
        tmpl = _jinja_env.from_string(DEFAULT_TEMPLATE)
        render_ctx["template_type"] = template.template_type

    html = tmpl.render(**render_ctx)

    # 缓存（无自定义上下文时）
    if context is None:
        _render_cache[cache_key] = html

    return html


def invalidate_cache(template_id: uuid.UUID) -> None:
    """模板变更后失效缓存"""
    cache_key = f"page:{template_id}"
    _render_cache.pop(cache_key, None)


async def _build_context(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    config_json: dict,
    extra_context: dict | None = None,
) -> dict:
    """构建渲染上下文"""
    ctx = {"config": config_json}

    # 从 config_json 中提取关联信息并查询数据库
    if extra_context:
        ctx.update(extra_context)

    return ctx


DEFAULT_TEMPLATE = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{{ config.get('brand_name', '') }}</title></head>
<body>
<h1>{{ config.get('brand_name', '') }}</h1>
<h2>{{ config.get('product_name', '') }}</h2>
<div>{{ config.get('story_content', '') }}</div>
<p>Template: {{ template_type }}</p>
</body>
</html>"""
