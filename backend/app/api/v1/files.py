"""文件上传 API"""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.storage import get_file_info, get_public_file, upload_file
from app.utils.auth_rbac import require_role

# Maximum upload size: 10 MB
MAX_UPLOAD_SIZE = 10 * 1024 * 1024

# Allowed MIME types for upload
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/csv",
}

# MIME 类型允许通过未认证的公开端点 /files/public/* 提供。只有不可执行且不含
# 敏感业务语义的图片类型可以公开访问；PDF（检测报告/证书）、Office、CSV、SVG
# （公开端点下的存储型 XSS 载体）必须走已认证的 /files/{file_id} 预签路径，
# 该路径使用短期 presigned URL 并受 admin/operator 上传授权保护。
PUBLIC_FILE_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}

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
    _role: str = Depends(require_role("admin", "operator")),
):
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        from fastapi import HTTPException

        raise HTTPException(status_code=413, detail=f"文件大小超过限制（最大 {MAX_UPLOAD_SIZE // 1024 // 1024} MB）")
    if file.content_type and file.content_type not in ALLOWED_CONTENT_TYPES:
        from fastapi import HTTPException

        raise HTTPException(status_code=415, detail=f"不支持的文件类型: {file.content_type}")
    result = await upload_file(
        tenant_id=str(tenant_id),
        module=module,
        filename=file.filename or "unknown.png",
        content=content,
        content_type=file.content_type or "image/png",
    )
    # 仅对公开可提供的图片类型返回 public_url；PDF/Office/CSV/SVG 等敏感或可执行
    # 类型必须通过已认证的 /files/{file_id} 预签路径访问（短期 URL + 授权保护）。
    response: dict = {**result}
    if (file.content_type or "image/png") in PUBLIC_FILE_CONTENT_TYPES:
        response["public_url"] = _build_public_file_url(request, result["file_url"])
    return response


@file_router.get("/public/{file_path:path}", summary="公开访问 文件")
async def public_file_endpoint(file_path: str):
    result = await get_public_file(file_path)
    # 公开端点无认证，只能提供不可执行的图片类型。PDF（检测报告/证书）、Office、
    # CSV、SVG（存储型 XSS 载体）必须走已认证的 /files/{file_id} 预签路径。
    content_type = result["content_type"]
    if content_type not in PUBLIC_FILE_CONTENT_TYPES:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="File not found")
    return StreamingResponse(_iter_file_body(result["body"]), media_type=content_type)


@file_router.get("/{file_id}", summary="获取 文件")
async def get_file_endpoint(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_file_info(str(tenant_id), file_id)
