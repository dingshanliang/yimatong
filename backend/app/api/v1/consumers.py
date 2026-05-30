"""消费者相关端点（H5 前端使用，scan_token 鉴权）"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.scan_token import verify_scan_token

consumer_router = APIRouter(prefix="/api/v1/consumers", tags=["consumers"])


class LeadCaptureRequest(BaseModel):
    name: str | None = None
    phone: str | None = None
    public_id: str


@consumer_router.post("/lead-capture", status_code=201)
async def lead_capture(
    request: Request,
    body: LeadCaptureRequest,
    db: AsyncSession = Depends(get_db),
):
    """消费者留资（姓名+手机号），需要 scan_token 鉴权"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")

    token = auth_header[7:]
    payload = verify_scan_token(token, body.public_id)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid_token")

    # 加密存储手机号
    encrypted_phone = None
    phone_hash = None
    if body.phone:
        from app.utils.crypto import encrypt_phone, hash_phone

        encrypted_phone = encrypt_phone(body.phone)
        phone_hash = hash_phone(body.phone)

    # 存储到 consumer_profile（通过 member 服务）
    if phone_hash:
        from app.models.member import ConsumerProfile

        # scan_token 不含 tenant_id，通过 public_id 反查码数据获取
        from app.services.resolver import resolve_public_code

        code_data = await resolve_public_code(db, body.public_id)
        if not code_data:
            raise HTTPException(status_code=404, detail="code not found")
        tenant_id = uuid.UUID(code_data["tenant_id"])

        result = await db.execute(
            select(ConsumerProfile)
            .where(
                ConsumerProfile.tenant_id == tenant_id,
                ConsumerProfile.phone_hash == phone_hash,
            )
            .limit(1)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            profile = ConsumerProfile(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                phone_hash=phone_hash,
                phone_encrypted=encrypted_phone,
                display_name=body.name,
            )
            db.add(profile)
            await db.commit()

    return {"status": "ok"}
