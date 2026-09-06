"""多语言 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.services.audit import write_audit_log
from app.services.i18n import (
    batch_update_translations,
    create_translation,
    delete_translation,
    detect_language,
    list_translations,
)
from app.utils.auth_rbac import require_permission

i18n_router = APIRouter(prefix="/api/v1/i18n", tags=["i18n"])


class TranslationCreate(BaseModel):
    key: str
    locale: str
    value: str


class BatchTranslationRequest(BaseModel):
    translations: list[TranslationCreate]


class DetectRequest(BaseModel):
    accept_language: str


@i18n_router.post("/translations", status_code=201, summary="创建 translation")
async def create_translation_endpoint(
    body: TranslationCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _permission: None = Depends(require_permission("tenant:manage")),
):
    t = await create_translation(db, tenant_id, body.key, body.locale, body.value)
    await write_audit_log(
        db,
        operator_id=str(account_id),
        target_tenant_id=str(tenant_id),
        action="translation_create",
        resource=f"translation:{t.id}",
        details={"key": t.key, "locale": t.locale},
    )
    return {"id": str(t.id), "key": t.key, "locale": t.locale, "value": t.value}


@i18n_router.get("/translations", summary="translations 列表")
async def list_translations_endpoint(
    locale: str | None = Query(None),
    key: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    translations = await list_translations(db, tenant_id, locale=locale, key_prefix=key)
    return [{"id": str(t.id), "key": t.key, "locale": t.locale, "value": t.value} for t in translations]


@i18n_router.delete("/translations/{translation_id}", status_code=204, summary="删除 translation")
async def delete_translation_endpoint(
    translation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _permission: None = Depends(require_permission("tenant:manage")),
):
    if not await delete_translation(db, tenant_id, translation_id):
        raise HTTPException(status_code=404, detail="Translation not found")
    await write_audit_log(
        db,
        operator_id=str(account_id),
        target_tenant_id=str(tenant_id),
        action="translation_delete",
        resource=f"translation:{translation_id}",
    )


@i18n_router.post("/translations/batch")
async def batch_endpoint(
    body: BatchTranslationRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _permission: None = Depends(require_permission("tenant:manage")),
):
    items = [t.model_dump() for t in body.translations]
    count = await batch_update_translations(db, tenant_id, items)
    await write_audit_log(
        db,
        operator_id=str(account_id),
        target_tenant_id=str(tenant_id),
        action="translation_batch_update",
        resource="translations:batch",
        details={"count": count},
    )
    return {"updated": count}


@i18n_router.post("/detect")
async def detect_endpoint(
    body: DetectRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return {"detected": detect_language(body.accept_language)}
