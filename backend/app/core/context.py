"""Request-scoped context for passing tenant_id from middleware to DB layer."""

from contextvars import ContextVar

# Set by TenantScopeMiddleware, consumed by get_db()
_current_tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)


def set_request_tenant_id(tenant_id: str | None) -> None:
    _current_tenant_id.set(tenant_id)


def get_request_tenant_id() -> str | None:
    return _current_tenant_id.get()
