"""文件上传 API"""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.storage import get_file_info, get_public_file, upload_file

file_router = APIRouter(prefix="/api/v1/files", tags=["files"])


def _build_public_file_url(request: Request, file_key: str) -> str:
    base_url = str(request.base_url).rstrip("/")
    return f"{base_url}/api/v1/files/public/{quote(file_key, safe='/')}"


def _iter_file_body(body):
    while chunk := body.read(8192):
        yield chunk


@file_router.post("/upload", status_code=201, summary="上传 文件")
async def upload_file_endpoint(
    request: Request,
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
    return {**result, "public_url": _build_public_file_url(request, result["file_url"])}


@file_router.get("/public/{file_path:path}", summary="公开访问 文件")
async def public_file_endpoint(file_path: str):
    result = await get_public_file(file_path)
    return StreamingResponse(_iter_file_body(result["body"]), media_type=result["content_type"])


@file_router.get("/{file_id}", summary="获取 文件")
async def get_file_endpoint(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_file_info(str(tenant_id), file_id)
