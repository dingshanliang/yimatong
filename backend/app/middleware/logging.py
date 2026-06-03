"""Request/response logging middleware."""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import get_request_tenant_id
from app.core.request_id import get_request_id

logger = logging.getLogger(__name__)

_SKIP_PATHS = {"/health", "/health/detail"}


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000

        logger.info(
            "%s %s %d %.1fms tenant=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            get_request_tenant_id() or "-",
            extra={
                "extra_data": {
                    "method": request.method,
                    "path": str(request.url.path),
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 1),
                    "request_id": get_request_id(),
                    "tenant_id": get_request_tenant_id(),
                }
            },
        )

        return response
