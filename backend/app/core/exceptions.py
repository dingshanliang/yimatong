"""Centralized exception hierarchy for the application.

All domain exceptions inherit from AppException, which carries status_code,
error_code, and detail for consistent error responses.
"""

import re


def _class_to_error_code(cls_name: str) -> str:
    """Convert CamelCase class name to UPPER_SNAKE_CASE error code."""
    # Insert underscore before uppercase letters that follow lowercase/digits
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", cls_name)
    # Remove trailing "Error" or "Exception" suffix for brevity
    s = re.sub(r"_?(Error|Exception)$", "", s)
    return s.upper()


class AppException(Exception):
    """Base exception for all application errors."""

    status_code: int = 500
    detail: str

    def __init__(self, detail: str, *, error_code: str | None = None) -> None:
        self.detail = detail
        self.error_code = error_code or _class_to_error_code(type(self).__name__)
        super().__init__(detail)


class NotFoundError(AppException):
    status_code = 404


class ConflictError(AppException):
    status_code = 409


class UnauthorizedError(AppException):
    status_code = 401


class ForbiddenError(AppException):
    status_code = 403


class BadRequestError(AppException):
    status_code = 400


class BusinessError(AppException):
    status_code = 422


class ExternalServiceError(AppException):
    status_code = 502

    def __init__(
        self,
        detail: str,
        *,
        service_name: str | None = None,
        error_code: str | None = None,
    ) -> None:
        self.service_name = service_name
        super().__init__(detail, error_code=error_code)


class QuotaExceededError(AppException):
    status_code = 429


class InvalidStateTransitionError(AppException):
    status_code = 409
