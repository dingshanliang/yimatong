"""多语言 API"""

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.i18n import (
    batch_update_translations,
    create_translation,
    detect_language,
    list_translations,
)

i18n_router = APIRouter(prefix="/api/v1/i18n", tags=["i18n"])


class TranslationCreate(BaseModel):
    key: str
    locale: str
    value: str


class BatchTranslationRequest(BaseModel):
    translations: list[TranslationCreate]


class DetectRequest(BaseModel):
    accept_language: str


@i18n_router.post("/translations", status_code=201)
async def create_translation_endpoint(
    body: TranslationCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    t = await create_translation(db, tenant_id, body.key, body.locale, body.value)
    return {"id": str(t.id), "key": t.key, "locale": t.locale, "value": t.value}


@i18n_router.get("/translations")
async def list_translations_endpoint(
    locale: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    translations = await list_translations(db, tenant_id, locale=locale)
    return [{"id": str(t.id), "key": t.key, "locale": t.locale, "value": t.value} for t in translations]


@i18n_router.post("/translations/batch")
async def batch_endpoint(
    body: BatchTranslationRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items = [t.model_dump() for t in body.translations]
    count = await batch_update_translations(db, tenant_id, items)
    return {"updated": count}


@i18n_router.post("/detect")
async def detect_endpoint(
    body: DetectRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return {"detected": detect_language(body.accept_language)}
