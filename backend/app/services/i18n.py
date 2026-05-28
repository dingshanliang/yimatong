"""多语言服务"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.i18n import Translation


async def create_translation(
    db: AsyncSession, tenant_id: uuid.UUID, key: str, locale: str, value: str,
) -> Translation:
    t = Translation(tenant_id=tenant_id, key=key, locale=locale, value=value)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def list_translations(
    db: AsyncSession, tenant_id: uuid.UUID, locale: str | None = None,
) -> list[Translation]:
    stmt = select(Translation).where(Translation.tenant_id == tenant_id)
    if locale:
        stmt = stmt.where(Translation.locale == locale)
    result = await db.execute(stmt.order_by(Translation.key))
    return list(result.scalars().all())


async def batch_update_translations(
    db: AsyncSession, tenant_id: uuid.UUID, translations: list[dict],
) -> int:
    count = 0
    for t in translations:
        result = await db.execute(
            select(Translation).where(
                Translation.tenant_id == tenant_id,
                Translation.key == t["key"],
                Translation.locale == t["locale"],
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = t["value"]
        else:
            db.add(Translation(
                tenant_id=tenant_id, key=t["key"], locale=t["locale"], value=t["value"],
            ))
        count += 1
    await db.commit()
    return count


def detect_language(accept_language: str) -> str:
    """根据 Accept-Language 头检测语言"""
    if not accept_language:
        return "zh"
    primary = accept_language.split(",")[0].strip().lower()
    if primary.startswith("zh"):
        return "zh"
    elif primary.startswith("en"):
        return "en"
    elif primary.startswith("ja"):
        return "ja"
    elif primary.startswith("ko"):
        return "ko"
    return "zh"
