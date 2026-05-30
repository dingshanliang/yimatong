"""Request ID middleware — generate or accept X-Request-ID for tracing."""

import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.request_id import set_request_id

_SAFE_REQUEST_ID = re.compile(r"[^A-Za-z0-9\-._~]")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        raw = request.headers.get("X-Request-ID", "").strip()[:64]
        request_id = _SAFE_REQUEST_ID.sub("", raw) if raw else uuid.uuid4().hex[:32]

        set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            set_request_id(None)

        response.headers["X-Request-ID"] = request_id
        return response
