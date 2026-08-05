"""Request-scoped context for passing tenant_id from middleware to DB layer."""

from contextvars import ContextVar, Token

# Set by TenantScopeMiddleware, consumed by get_db()
_current_tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)

# Set by consumer endpoints (scan_token resolved), consumed by get_db()
_consumer_tenant_id: ContextVar[str | None] = ContextVar("consumer_tenant_id", default=None)


def set_request_tenant_id(tenant_id: str | None) -> Token[str | None]:
    return _current_tenant_id.set(tenant_id)


def reset_request_tenant_id(token: Token[str | None]) -> None:
    _current_tenant_id.reset(token)


def get_request_tenant_id() -> str | None:
    # Priority: JWT middleware > consumer scan_token
    return _current_tenant_id.get() or _consumer_tenant_id.get()


def set_consumer_tenant_id(tenant_id: str | None) -> Token[str | None]:
    return _consumer_tenant_id.set(tenant_id)


def reset_consumer_tenant_id(token: Token[str | None]) -> None:
    _consumer_tenant_id.reset(token)
