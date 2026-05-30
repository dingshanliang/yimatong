"""Request ID middleware — generate or accept X-Request-ID for tracing."""

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.request_id import get_request_id, set_request_id


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID", "").strip()[:64]
        if not request_id:
            request_id = uuid.uuid4().hex[:32]

        set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            set_request_id(None)

        response.headers["X-Request-ID"] = request_id
        return response
