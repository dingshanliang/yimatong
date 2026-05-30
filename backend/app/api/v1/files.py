"""文件上传 API"""

import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.storage import get_file_info, upload_file

file_router = APIRouter(prefix="/api/v1/files", tags=["files"])


@file_router.post("/upload", status_code=201, summary="上传 文件")
async def upload_file_endpoint(
    file: UploadFile = File(...),
    module: str = Form("general"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    content = await file.read()
    result = await upload_file(
        tenant_id=str(tenant_id),
        module=module,
        filename=file.filename or "unknown.png",
        content=content,
        content_type=file.content_type or "image/png",
    )
    return result


@file_router.get("/{file_id}", summary="获取 文件")
async def get_file_endpoint(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_file_info(str(tenant_id), file_id)
