"""Global exception handlers for FastAPI.

Registers four handlers to produce a uniform JSON error response:
  {error_code, detail, request_id}

All existing raise HTTPException(...) calls are automatically wrapped
by http_exception_handler — no code changes required.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.core.exceptions import AppException
from app.core.request_id import get_request_id

logger = logging.getLogger(__name__)


def _error_body(error_code: str, detail, request_id: str | None) -> dict:
    return {
        "error_code": error_code,
        "detail": detail,
        "request_id": request_id,
    }


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    request_id = get_request_id()
    logger.warning(
        "AppException: %s detail=%s path=%s",
        exc.error_code,
        exc.detail,
        request.url.path,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.error_code, exc.detail, request_id),
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    request_id = get_request_id()
    error_code = f"HTTP_{exc.status_code}"
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    logger.info(
        "HTTPException: %s detail=%s path=%s",
        error_code,
        detail,
        request.url.path,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(error_code, detail, request_id),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    import json

    request_id = get_request_id()
    # Pydantic V2 errors() 可能含不可序列化对象（如 ctx.error=ValueError）
    # 用 json.loads(json.dumps(..., default=str)) 做深度清理
    raw_errors = exc.errors()
    try:
        clean_errors = json.loads(json.dumps(raw_errors, default=str))
    except (TypeError, ValueError):
        clean_errors = json.loads(json.dumps(raw_errors, default=lambda o: str(o)))
    logger.warning(
        "ValidationError: %d errors path=%s",
        len(clean_errors),
        request.url.path,
    )
    return JSONResponse(
        status_code=422,
        content=_error_body("VALIDATION_ERROR", clean_errors, request_id),
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    request_id = get_request_id()
    logger.exception(
        "Unhandled exception: %s path=%s method=%s request_id=%s",
        type(exc).__name__,
        request.url.path,
        request.method,
        request_id,
    )
    return JSONResponse(
        status_code=500,
        content=_error_body(
            "INTERNAL_ERROR", "Internal server error", request_id
        ),
    )


def register_exception_handlers(app: FastAPI, *, debug: bool = False) -> None:
    """Register all global exception handlers on the FastAPI app.

    In debug mode, the catch-all Exception handler is skipped so that
    Starlette's ServerErrorMiddleware can render interactive tracebacks.
    """
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    if not debug:
        app.add_exception_handler(Exception, unhandled_exception_handler)
