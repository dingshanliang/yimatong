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
    region: str | None = None
    intention: str | None = None
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

        extra = {}
        if body.region:
            extra["region"] = body.region
        if body.intention:
            extra["intention"] = body.intention

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
                nickname=body.name,
                extra_data=extra or None,
            )
            db.add(profile)
        else:
            if body.name:
                profile.nickname = body.name
            if extra:
                existing = profile.extra_data or {}
                existing.update(extra)
                profile.extra_data = existing
        await db.commit()

        await db.flush()

    return {"status": "ok", "consumer_id": str(profile.id) if phone_hash and profile else None}


@consumer_router.get("/me")
async def get_consumer_me(
    request: Request,
    consumer_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """查询当前消费者信息（积分、等级），通过 scan_token 或 consumer_id 鉴权"""
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    if not token and not consumer_id:
        raise HTTPException(status_code=401, detail="scan_token or consumer_id required")

    # 优先用 consumer_id 直接查
    if consumer_id:
        try:
            cid = uuid.UUID(consumer_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid consumer_id")

        from app.models.member import ConsumerProfile

        result = await db.execute(select(ConsumerProfile).where(ConsumerProfile.id == cid))
        profile = result.scalar_one_or_none()
        if not profile:
            raise HTTPException(status_code=404, detail="consumer not found")

        return {
            "consumer_id": str(profile.id),
            "member_level": profile.member_level,
            "total_points": profile.total_points,
            "nickname": profile.nickname,
        }

    # 通过 scan_token + public_id 查（需传 public_id query param）
    if token:
        import jwt

        from app.core.config import settings

        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        except jwt.exceptions.DecodeError:
            raise HTTPException(status_code=401, detail="invalid token")
        except jwt.exceptions.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="token expired")

        if payload.get("type") != "scan_token":
            raise HTTPException(status_code=401, detail="invalid token type")

        return {
            "consumer_id": None,
            "member_level": "normal",
            "total_points": 0,
        }

    raise HTTPException(status_code=401, detail="unauthorized")
