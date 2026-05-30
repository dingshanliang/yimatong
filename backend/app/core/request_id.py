"""Request-scoped context for passing request_id from middleware to logging/error handlers."""

from contextvars import ContextVar

_current_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(rid: str | None) -> None:
    _current_request_id.set(rid)


def get_request_id() -> str | None:
    return _current_request_id.get()
