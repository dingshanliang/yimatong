"""消费者相关端点（H5 前端使用，scan_token 鉴权）"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_consumer_tenant_id
from app.core.database import get_db_for_consumer
from app.models.member import ConsumerProfile
from app.schemas.common import PaginatedResponse
from app.schemas.member import ExchangeRequest as PointsExchangeRequest
from app.schemas.member import LeadCaptureRequest
from app.services.member import get_consumer_profile, list_point_transactions
from app.services.point_shop import exchange_product, list_consumer_point_products
from app.services.resolver import resolve_public_code
from app.services.scan_token import verify_scan_token
from app.utils.client_ip import compute_ip_hash, get_client_ip
from app.utils.crypto import encrypt_phone, hash_phone

consumer_router = APIRouter(prefix="/api/v1/consumers", tags=["consumers"])


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="scan_token required")
    return auth_header[7:]


async def _resolve_scan_context(request: Request, db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Resolve tenant_id and optional bound consumer_id from scan_token."""
    token = _extract_bearer_token(request)
    payload = verify_scan_token(token)
    if not payload or not payload.get("public_id"):
        raise HTTPException(status_code=401, detail="invalid token")

    # 优先从 token payload 获取 tenant_id（减少 DB 查询）
    tid = payload.get("tenant_id")
    tenant_uuid: uuid.UUID
    if tid:
        tenant_uuid = uuid.UUID(tid)
    else:
        code_data = await resolve_public_code(db, payload["public_id"])
        if not code_data:
            raise HTTPException(status_code=404, detail="code not found")
        tenant_uuid = uuid.UUID(code_data["tenant_id"])

    set_consumer_tenant_id(str(tenant_uuid))

    cid_str = payload.get("consumer_id")
    bound_consumer_id = uuid.UUID(cid_str) if cid_str else None
    return tenant_uuid, bound_consumer_id


def _verify_consumer_ownership(bound_consumer_id: uuid.UUID | None, requested_consumer_id: uuid.UUID) -> None:
    """If scan_token has bound consumer_id, verify request matches."""
    if bound_consumer_id and bound_consumer_id != requested_consumer_id:
        raise HTTPException(status_code=403, detail="consumer_id mismatch with token")


@consumer_router.post("/lead-capture", status_code=201)
async def lead_capture(
    request: Request,
    body: LeadCaptureRequest,
    db: AsyncSession = Depends(get_db_for_consumer),
):
    """消费者留资（姓名+手机号），需要 scan_token 鉴权。

    yimatong-zgb1.5 AC3：采集手机号（PII）前必须存在 granted 的 privacy consent；
    撤回后停止采集。非 PII 字段（region/intention）不强制 consent。
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")

    token = auth_header[7:]
    client_ip = get_client_ip(request)
    ip_hash = compute_ip_hash(client_ip)
    payload = verify_scan_token(token, body.public_id, expected_ip_hash=ip_hash)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid_token")

    # yimatong-zgb1.5：采集手机号前检查 consent
    encrypted_phone = None
    phone_hash = None
    profile = None
    if body.phone:
        # 先反查 tenant_id（consent 检查 + 存储都需要）
        code_data = await resolve_public_code(db, body.public_id)
        if not code_data:
            raise HTTPException(status_code=404, detail="code not found")
        tenant_id = uuid.UUID(code_data["tenant_id"])

        # consent gating：privacy 类型必须 granted 且未撤回
        from app.models.consent import ConsentType
        from app.services.consent import has_active_consent

        consent_ok = await has_active_consent(
            db,
            tenant_id=tenant_id,
            consent_type=ConsentType.privacy,
            public_id=body.public_id,
        )
        if not consent_ok:
            # 区分"从未同意"和"已撤回"
            from sqlalchemy import select as sa_select

            from app.models.consent import ConsentRecord, ConsentStatus

            any_consent = (
                await db.execute(
                    sa_select(ConsentRecord.status)
                    .where(
                        ConsentRecord.tenant_id == tenant_id,
                        ConsentRecord.consent_type == ConsentType.privacy,
                        ConsentRecord.public_id == body.public_id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if any_consent == ConsentStatus.withdrawn:
                raise HTTPException(status_code=403, detail="consent_withdrawn")
            raise HTTPException(status_code=403, detail="consent_required")

        encrypted_phone = encrypt_phone(body.phone)
        phone_hash = hash_phone(body.phone)

        # 存储到 consumer_profile（通过 member 服务）
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

    return {"status": "ok", "consumer_id": str(profile.id) if phone_hash and profile else None}


@consumer_router.get("/me")
async def get_consumer_me(
    request: Request,
    consumer_id: str | None = None,
    db: AsyncSession = Depends(get_db_for_consumer),
):
    """查询当前消费者信息（积分、等级），必须结合 scan_token 与 consumer_id。"""
    tenant_id, bound_cid = await _resolve_scan_context(request, db)
    if consumer_id:
        try:
            cid = uuid.UUID(consumer_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid consumer_id")

        _verify_consumer_ownership(bound_cid, cid)

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
    db: AsyncSession = Depends(get_db_for_consumer),
):
    tenant_id, bound_cid = await _resolve_scan_context(request, db)
    if not consumer_id:
        return {
            "consumer_id": None,
            "member_level": "normal",
            "total_points": 0,
            "recent_transactions": [],
        }

    _verify_consumer_ownership(bound_cid, consumer_id)

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
    db: AsyncSession = Depends(get_db_for_consumer),
):
    tenant_id, bound_cid = await _resolve_scan_context(request, db)
    _verify_consumer_ownership(bound_cid, consumer_id)

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
    db: AsyncSession = Depends(get_db_for_consumer),
):
    tenant_id, bound_cid = await _resolve_scan_context(request, db)
    _verify_consumer_ownership(bound_cid, consumer_id)

    try:
        return {"items": await list_consumer_point_products(db, tenant_id, consumer_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@consumer_router.post("/points/exchanges")
async def create_consumer_points_exchange(
    request: Request,
    body: PointsExchangeRequest,
    db: AsyncSession = Depends(get_db_for_consumer),
):
    tenant_id, bound_cid = await _resolve_scan_context(request, db)
    _verify_consumer_ownership(bound_cid, body.consumer_id)

    try:
        return await exchange_product(db, tenant_id, body.consumer_id, body.product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
