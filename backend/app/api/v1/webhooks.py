"""Webhook / Open API 管理"""

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable
from app.services.webhook import (
    ApiKeyActiveLimitReached,
    ApiKeyLifecycleConflict,
    WebhookEndpointConflict,
    create_api_key,
    create_webhook_endpoint,
    delete_webhook_endpoint,
    get_webhook_delivery,
    list_api_keys,
    list_deliveries,
    list_webhook_endpoints,
    revoke_api_key,
    rotate_api_key,
    update_webhook_endpoint,
)
from app.services.webhook_sender import validate_webhook_destination, validate_webhook_url
from app.utils.auth_rbac import require_api_key_admin, require_durable_session, require_permission

webhook_router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])
_api_key_lifecycle_cache = AsyncRedisCache(prefix="api_key_lifecycle", default_ttl=60)
_webhook_management_cache = AsyncRedisCache(prefix="webhook_management", default_ttl=60)
_API_KEY_LIFECYCLE_WINDOW_SECONDS = 60

CanonicalIdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    ),
]
_MAX_API_KEY_LIFETIME = timedelta(days=365)
WebhookConfigVersion = Annotated[int, Header(alias="If-Match", ge=1)]
_WEBHOOK_EVENTS = frozenset(
    {
        "scan.created",
        "claim.created",
        "risk.alert",
        "campaign.active",
        "campaign.paused",
        "campaign.ended",
    }
)


async def require_brand_webhook_principal(request: Request) -> None:
    """Allow only a live direct-brand JWT; API keys and acting tenants are excluded."""

    await require_durable_session(request)
    if (
        getattr(request.state, "tenant_type", None) != "brand"
        or getattr(request.state, "auth_method", None) != "jwt"
        or not getattr(request.state, "session_id", None)
        or getattr(request.state, "acting_tenant_id", None)
    ):
        raise HTTPException(status_code=403, detail="Direct brand webhook access required")


def _webhook_dependencies(permission: str) -> list:
    return [Depends(require_brand_webhook_principal), Depends(require_permission(permission))]


def _lifecycle_rate_bucket(kind: str, identifier: uuid.UUID) -> str:
    digest = hmac.new(
        settings.hmac_pepper.encode(),
        f"api-key-lifecycle:{kind}:{identifier}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{kind}:{digest}"


async def _enforce_api_key_lifecycle_rate_limit(tenant_id: uuid.UUID, actor_id: uuid.UUID) -> None:
    try:
        tenant_allowed, _ = await _api_key_lifecycle_cache.rate_limit_check_shared(
            _lifecycle_rate_bucket("tenant", tenant_id),
            max_attempts=60,
            window_seconds=_API_KEY_LIFECYCLE_WINDOW_SECONDS,
        )
        if not tenant_allowed:
            raise HTTPException(
                status_code=429,
                detail="API key lifecycle requests are too frequent",
                headers={"Retry-After": str(_API_KEY_LIFECYCLE_WINDOW_SECONDS)},
            )
        principal_allowed, _ = await _api_key_lifecycle_cache.rate_limit_check_shared(
            _lifecycle_rate_bucket("principal", actor_id),
            max_attempts=20,
            window_seconds=_API_KEY_LIFECYCLE_WINDOW_SECONDS,
        )
        if not principal_allowed:
            raise HTTPException(
                status_code=429,
                detail="API key lifecycle requests are too frequent",
                headers={"Retry-After": str(_API_KEY_LIFECYCLE_WINDOW_SECONDS)},
            )
    except SharedSecurityCacheUnavailable as exc:
        raise HTTPException(status_code=503, detail="API key lifecycle service is temporarily unavailable") from exc


def _webhook_rate_bucket(kind: str, identifier: uuid.UUID) -> str:
    digest = hmac.new(
        settings.hmac_pepper.encode(),
        f"webhook-management:{kind}:{identifier}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{kind}:{digest}"


async def _enforce_webhook_management_rate_limit(tenant_id: uuid.UUID, actor_id: uuid.UUID) -> None:
    try:
        for kind, identifier, limit in (("tenant", tenant_id, 60), ("principal", actor_id, 20)):
            allowed, _ = await _webhook_management_cache.rate_limit_check_shared(
                _webhook_rate_bucket(kind, identifier),
                max_attempts=limit,
                window_seconds=_API_KEY_LIFECYCLE_WINDOW_SECONDS,
            )
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail="Webhook management requests are too frequent",
                    headers={"Retry-After": str(_API_KEY_LIFECYCLE_WINDOW_SECONDS)},
                )
    except SharedSecurityCacheUnavailable as exc:
        raise HTTPException(status_code=503, detail="Webhook management service is temporarily unavailable") from exc


def _api_key_lifecycle_error(exc: DBAPIError) -> HTTPException:
    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    response_by_sqlstate = {
        "22023": (400, "Invalid API key lifecycle request"),
        "28000": (401, "Authentication state is no longer valid"),
        "42501": (403, "Insufficient permissions"),
        "40001": (409, "API key lifecycle conflict"),
        "54000": (409, "API key active limit reached"),
        "23505": (409, "API key lifecycle collision"),
    }
    response = response_by_sqlstate.get(sqlstate)
    if response is None:
        raise exc
    status_code, detail = response
    return HTTPException(status_code=status_code, detail=detail)


def _webhook_authority_error(exc: DBAPIError) -> HTTPException:
    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    response_by_sqlstate = {
        "22023": (400, "Invalid webhook endpoint"),
        "28000": (401, "Authentication state is no longer valid"),
        "42501": (403, "Insufficient permissions"),
        "55P03": (409, "Webhook endpoint is busy"),
        "40001": (409, "Webhook endpoint version conflict"),
        "23505": (409, "Webhook endpoint conflict"),
        "23503": (409, "Webhook endpoint has retained delivery history; disable it instead"),
    }
    response = response_by_sqlstate.get(sqlstate)
    if response is None:
        raise exc
    status_code, detail = response
    return HTTPException(status_code=status_code, detail=detail)


class WebhookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=500)
    events: list[str] = Field(min_length=1, max_length=len(_WEBHOOK_EVENTS))
    description: str | None = Field(default=None, max_length=500)
    batch_mode: bool = False
    batch_size: int = Field(default=100, ge=1, le=1000)

    @field_validator("url")
    @classmethod
    def validate_url_shape(cls, value: str) -> str:
        validate_webhook_url(value)
        return value

    @field_validator("events")
    @classmethod
    def validate_events(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(event not in _WEBHOOK_EVENTS for event in value):
            raise ValueError("Webhook events must be unique supported event names")
        return value


class WebhookUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str | None = Field(default=None, min_length=1, max_length=500)
    events: list[str] | None = Field(default=None, min_length=1, max_length=len(_WEBHOOK_EVENTS))
    description: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    batch_mode: bool | None = None
    batch_size: int | None = Field(default=None, ge=1, le=1000)

    @field_validator("url")
    @classmethod
    def validate_url_shape(cls, value: str | None) -> str | None:
        if value is not None:
            validate_webhook_url(value)
        return value

    @field_validator("events")
    @classmethod
    def validate_events(cls, value: list[str] | None) -> list[str] | None:
        if value is not None:
            return WebhookCreate.validate_events(value)
        return value

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one webhook setting must be provided")
        return self


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: Literal["data_reader", "coupon_operator", "webhook_admin", "erp_sync", "full_access"] = "data_reader"
    expires_at: AwareDatetime | None = None
    permanent_acknowledged: bool = False
    permanent_reason: str | None = Field(default=None, min_length=10, max_length=200)

    @field_validator("name", "permanent_reason", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_expiry_contract(self) -> Self:
        if self.expires_at is None:
            if not self.permanent_acknowledged or self.permanent_reason is None:
                raise ValueError("Permanent API keys require acknowledgement and a reason")
        else:
            request_time = datetime.now(UTC)
            if self.expires_at <= request_time:
                raise ValueError("API key expiry must be in the future")
            if self.expires_at - request_time > _MAX_API_KEY_LIFETIME:
                raise ValueError("Finite API key expiry must not exceed 365 days")
            if self.permanent_acknowledged or self.permanent_reason is not None:
                raise ValueError("Permanent acknowledgement is only valid for permanent API keys")
        return self


class ApiKeyListItem(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    role: Literal["data_reader", "coupon_operator", "webhook_admin", "erp_sync", "full_access"]
    permissions: list[str]
    revoked: bool
    expires_at: datetime | None
    last_used_at: datetime | None


class ApiKeyListPage(BaseModel):
    items: list[ApiKeyListItem]
    total: int
    page: int
    page_size: int


@webhook_router.post("/endpoints", status_code=201, dependencies=_webhook_dependencies("webhook:manage"))
async def create_endpoint(
    body: WebhookCreate,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    actor_id = uuid.UUID(request.state.account_id)
    await _enforce_webhook_management_rate_limit(tenant_id, actor_id)
    try:
        safe_url = await validate_webhook_destination(body.url)
        ep = await create_webhook_endpoint(
            db,
            tenant_id,
            uuid.UUID(request.state.session_id),
            safe_url,
            body.events,
            actor_id=actor_id,
            description=body.description,
            batch_mode=body.batch_mode,
            batch_size=body.batch_size,
        )
    except DBAPIError as exc:
        raise _webhook_authority_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": str(ep.id),
        "url": ep.url,
        "events": ep.events,
        "description": ep.description,
        "enabled": ep.enabled,
        "config_version": ep.config_version,
        "batch_mode": ep.batch_mode,
        "batch_size": ep.batch_size,
        "secret": ep.secret,
    }


@webhook_router.get("/endpoints", dependencies=_webhook_dependencies("webhook:read"))
async def list_endpoints(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    eps = await list_webhook_endpoints(db, tenant_id)
    return [
        {
            "id": str(e.id),
            "url": e.url,
            "events": e.events,
            "description": e.description,
            "enabled": e.enabled,
            "config_version": e.config_version,
            "batch_mode": e.batch_mode,
            "batch_size": e.batch_size,
        }
        for e in eps
    ]


@webhook_router.patch("/endpoints/{endpoint_id}", dependencies=_webhook_dependencies("webhook:manage"))
async def update_endpoint(
    endpoint_id: uuid.UUID,
    body: WebhookUpdate,
    request: Request,
    expected_version: WebhookConfigVersion,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    actor_id = uuid.UUID(request.state.account_id)
    await _enforce_webhook_management_rate_limit(tenant_id, actor_id)
    try:
        safe_url = await validate_webhook_destination(body.url) if body.url is not None else None
        ep = await update_webhook_endpoint(
            db,
            tenant_id,
            uuid.UUID(request.state.session_id),
            endpoint_id,
            actor_id=actor_id,
            expected_version=expected_version,
            url=safe_url,
            events=body.events,
            description=body.description,
            enabled=body.enabled,
            batch_mode=body.batch_mode,
            batch_size=body.batch_size,
        )
    except DBAPIError as exc:
        raise _webhook_authority_error(exc) from exc
    except WebhookEndpointConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ep:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    return {
        "id": str(ep.id),
        "url": ep.url,
        "events": ep.events,
        "description": ep.description,
        "enabled": ep.enabled,
        "config_version": ep.config_version,
        "batch_mode": ep.batch_mode,
        "batch_size": ep.batch_size,
    }


@webhook_router.delete("/endpoints/{endpoint_id}", dependencies=_webhook_dependencies("webhook:manage"))
async def delete_endpoint(
    endpoint_id: uuid.UUID,
    request: Request,
    expected_version: WebhookConfigVersion,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    actor_id = uuid.UUID(request.state.account_id)
    await _enforce_webhook_management_rate_limit(tenant_id, actor_id)
    try:
        deleted = await delete_webhook_endpoint(
            db,
            tenant_id,
            uuid.UUID(request.state.session_id),
            endpoint_id,
            actor_id=actor_id,
            expected_version=expected_version,
        )
    except DBAPIError as exc:
        raise _webhook_authority_error(exc) from exc
    except WebhookEndpointConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    return {"deleted": True}


@webhook_router.post("/api-keys", status_code=201)
async def create_key(
    body: ApiKeyCreate,
    request: Request,
    idempotency_key: CanonicalIdempotencyKey,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_api_key_admin),
):
    actor_id = uuid.UUID(request.state.account_id)
    await _enforce_api_key_lifecycle_rate_limit(tenant_id, actor_id)
    try:
        key = await create_api_key(
            db,
            tenant_id,
            actor_id,
            uuid.UUID(request.state.session_id),
            body.name,
            role=body.role,
            expires_at=body.expires_at,
            idempotency_key=idempotency_key,
            permanent_reason=body.permanent_reason,
        )
    except DBAPIError as exc:
        raise _api_key_lifecycle_error(exc) from exc
    except (ApiKeyActiveLimitReached, ApiKeyLifecycleConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": str(key.id),
        "name": key.name,
        "key": key.key,
        "key_prefix": key.key_prefix,
        "role": key.role,
        "permissions": key.permissions,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
    }


@webhook_router.post("/api-keys/{key_id}/rotate", status_code=201)
async def rotate_key(
    key_id: uuid.UUID,
    request: Request,
    idempotency_key: CanonicalIdempotencyKey,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_api_key_admin),
):
    actor_id = uuid.UUID(request.state.account_id)
    await _enforce_api_key_lifecycle_rate_limit(tenant_id, actor_id)
    try:
        key = await rotate_api_key(
            db,
            tenant_id,
            key_id,
            actor_id,
            uuid.UUID(request.state.session_id),
            idempotency_key=idempotency_key,
        )
    except DBAPIError as exc:
        raise _api_key_lifecycle_error(exc) from exc
    except ApiKeyLifecycleConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="API key not found") from exc
    return {
        "id": str(key.id),
        "name": key.name,
        "key": key.key,
        "key_prefix": key.key_prefix,
        "role": key.role,
        "permissions": key.permissions,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
    }


@webhook_router.get("/api-keys", response_model=ApiKeyListPage)
async def list_keys(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_api_key_admin),
):
    keys, total = await list_api_keys(db, tenant_id, page, page_size)
    return {
        "items": [
            {
                "id": str(k.id),
                "name": k.name,
                "key_prefix": k.key_prefix,
                "role": k.role,
                "permissions": k.permissions,
                "revoked": k.revoked,
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            }
            for k in keys
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@webhook_router.delete("/api-keys/{key_id}")
async def revoke_key(
    key_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_api_key_admin),
):
    try:
        await revoke_api_key(
            db,
            tenant_id,
            key_id,
            uuid.UUID(request.state.account_id),
            uuid.UUID(request.state.session_id),
        )
    except DBAPIError as exc:
        raise _api_key_lifecycle_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="API key not found") from exc
    return {"revoked": True}


@webhook_router.get(
    "/deliveries",
    summary="deliveries 列表",
    dependencies=_webhook_dependencies("webhook:read"),
)
async def list_deliveries_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Literal["pending", "delivering", "retrying", "delivered", "failed"] | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    deliveries, total = await list_deliveries(db, tenant_id, page=page, page_size=page_size, status=status)
    return PaginatedResponse(
        items=[
            {
                "id": str(d.id),
                "endpoint_id": str(d.endpoint_id),
                "event_id": d.event_id,
                "event_type": d.event_type,
                "status": d.status,
                "retry_count": d.retry_count,
                "last_response_code": d.last_response_code,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in deliveries
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@webhook_router.get("/deliveries/{delivery_id}", dependencies=_webhook_dependencies("webhook:read"))
async def get_delivery_detail(
    delivery_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    d = await get_webhook_delivery(db, tenant_id, delivery_id)
    if not d:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return {
        "id": str(d.id),
        "endpoint_id": str(d.endpoint_id),
        "event_id": d.event_id,
        "event_type": d.event_type,
        "payload": d.payload,
        "status": d.status,
        "retry_count": d.retry_count,
        "next_retry_at": d.next_retry_at.isoformat() if d.next_retry_at else None,
        "last_response_code": d.last_response_code,
        "last_response_body": d.last_response_body,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }
