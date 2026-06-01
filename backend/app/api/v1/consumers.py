"""消费者相关端点（H5 前端使用，scan_token 鉴权）"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_with_bypass
from app.services.scan_token import verify_scan_token

consumer_router = APIRouter(prefix="/api/v1/consumers", tags=["consumers"])


class LeadCaptureRequest(BaseModel):
    name: str | None = None
    phone: str | None = None
    region: str | None = None
    intention: str | None = None
    public_id: str


class PointsExchangeRequest(BaseModel):
    consumer_id: uuid.UUID
    product_id: uuid.UUID


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="scan_token required")
    return auth_header[7:]


async def _resolve_scan_tenant(request: Request, db: AsyncSession) -> uuid.UUID:
    token = _extract_bearer_token(request)
    import jwt

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.exceptions.DecodeError:
        raise HTTPException(status_code=401, detail="invalid token")
    except jwt.exceptions.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")

    if payload.get("type") != "scan_token" or not payload.get("public_id"):
        raise HTTPException(status_code=401, detail="invalid token type")

    from app.services.resolver import resolve_public_code

    code_data = await resolve_public_code(db, payload["public_id"])
    if not code_data:
        raise HTTPException(status_code=404, detail="code not found")
    return uuid.UUID(code_data["tenant_id"])


@consumer_router.post("/lead-capture", status_code=201)
async def lead_capture(
    request: Request,
    body: LeadCaptureRequest,
    db: AsyncSession = Depends(get_db_with_bypass),
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
    db: AsyncSession = Depends(get_db_with_bypass),
):
    """查询当前消费者信息（积分、等级），必须结合 scan_token 与 consumer_id。"""
    tenant_id = await _resolve_scan_tenant(request, db)
    if consumer_id:
        try:
            cid = uuid.UUID(consumer_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid consumer_id")

        from app.models.member import ConsumerProfile

        result = await db.execute(
            select(ConsumerProfile).where(
                ConsumerProfile.id == cid,
                ConsumerProfile.tenant_id == tenant_id,
            )
        )
        profile = result.scalar_one_or_none()
        if not profile:
            raise HTTPException(status_code=404, detail="consumer not found")

        return {
            "consumer_id": str(profile.id),
            "member_level": profile.member_level,
            "total_points": profile.total_points,
            "nickname": profile.nickname,
        }

    return {
        "consumer_id": None,
        "member_level": "normal",
        "total_points": 0,
    }


@consumer_router.get("/points/me")
async def get_consumer_points_me(
    request: Request,
    consumer_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db_with_bypass),
):
    tenant_id = await _resolve_scan_tenant(request, db)
    if not consumer_id:
        return {
            "consumer_id": None,
            "member_level": "normal",
            "total_points": 0,
            "recent_transactions": [],
        }

    from app.services.member import get_consumer_profile

    profile = await get_consumer_profile(db, tenant_id, consumer_id)
    if not profile:
        raise HTTPException(status_code=404, detail="consumer not found")
    return profile


@consumer_router.get("/points/transactions")
async def list_consumer_points_transactions(
    request: Request,
    consumer_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db_with_bypass),
):
    tenant_id = await _resolve_scan_tenant(request, db)
    from app.schemas.common import PaginatedResponse
    from app.services.member import list_point_transactions

    txns, total = await list_point_transactions(db, tenant_id, consumer_id, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[
            {
                "id": str(t.id),
                "amount": t.amount,
                "balance_after": t.balance_after,
                "txn_type": t.txn_type,
                "reason": t.reason,
                "expires_at": t.expires_at.isoformat() if t.expires_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in txns
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@consumer_router.get("/points/products")
async def list_consumer_points_products(
    request: Request,
    consumer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_bypass),
):
    tenant_id = await _resolve_scan_tenant(request, db)
    from app.services.point_shop import list_consumer_point_products

    try:
        return {"items": await list_consumer_point_products(db, tenant_id, consumer_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@consumer_router.post("/points/exchanges")
async def create_consumer_points_exchange(
    request: Request,
    body: PointsExchangeRequest,
    db: AsyncSession = Depends(get_db_with_bypass),
):
    tenant_id = await _resolve_scan_tenant(request, db)
    from app.services.point_shop import exchange_product

    try:
        return await exchange_product(db, tenant_id, body.consumer_id, body.product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
