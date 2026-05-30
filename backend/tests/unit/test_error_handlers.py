"""Tests for global exception handlers."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.error_handlers import register_exception_handlers
from app.core.exceptions import (
    AppException,
    BadRequestError,
    BusinessError,
    ConflictError,
    ExternalServiceError,
    ForbiddenError,
    NotFoundError,
    QuotaExceededError,
    UnauthorizedError,
)
from app.core.request_id import set_request_id


@pytest.fixture
def app():
    _app = FastAPI()

    @_app.get("/raise-app")
    async def raise_app():
        raise NotFoundError("Resource not found")

    @_app.get("/raise-http")
    async def raise_http():
        raise HTTPException(status_code=403, detail="Forbidden action")

    @_app.get("/raise-bare")
    async def raise_bare():
        raise RuntimeError("unexpected")

    @_app.post("/validate")
    async def validate(body: dict):
        return body

    @_app.get("/raise-conflict")
    async def raise_conflict():
        raise ConflictError("Name already exists")

    @_app.get("/raise-quota")
    async def raise_quota():
        raise QuotaExceededError("Daily limit reached")

    @_app.get("/raise-external")
    async def raise_external():
        raise ExternalServiceError("AI service unavailable", service_name="deepseek")

    register_exception_handlers(_app)
    return _app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestAppExceptionHandler:
    def test_not_found(self, client):
        resp = client.get("/raise-app")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error_code"] == "NOT_FOUND"
        assert body["detail"] == "Resource not found"
        assert "request_id" in body

    def test_conflict(self, client):
        resp = client.get("/raise-conflict")
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "CONFLICT"

    def test_quota_exceeded(self, client):
        resp = client.get("/raise-quota")
        assert resp.status_code == 429
        assert resp.json()["error_code"] == "QUOTA_EXCEEDED"

    def test_external_service(self, client):
        resp = client.get("/raise-external")
        assert resp.status_code == 502
        assert resp.json()["error_code"] == "EXTERNAL_SERVICE"


class TestHttpExceptionHandler:
    def test_http_exception_wrapped(self, client):
        resp = client.get("/raise-http")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error_code"] == "HTTP_403"
        assert body["detail"] == "Forbidden action"
        assert "request_id" in body


class TestValidationExceptionHandler:
    def test_validation_error(self, client):
        resp = client.post("/validate", json="not an object")
        assert resp.status_code == 422
        body = resp.json()
        assert body["error_code"] == "VALIDATION_ERROR"
        assert isinstance(body["detail"], list)


class TestUnhandledExceptionHandler:
    def test_unhandled_returns_500(self, client):
        # raise_server_exceptions=False prevents TestClient from re-raising
        # the RuntimeError that our handler already caught and converted to 500
        with TestClient(client.app, raise_server_exceptions=False) as c:
            resp = c.get("/raise-bare")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error_code"] == "INTERNAL_ERROR"
        assert body["detail"] == "Internal server error"
        # Must NOT leak traceback
        assert "RuntimeError" not in str(body)


class TestErrorCodeGeneration:
    def test_auto_error_code(self):
        exc = NotFoundError("test")
        assert exc.error_code == "NOT_FOUND"

    def test_custom_error_code(self):
        exc = BadRequestError("test", error_code="CUSTOM_CODE")
        assert exc.error_code == "CUSTOM_CODE"

    def test_business_error_code(self):
        exc = BusinessError("test")
        assert exc.error_code == "BUSINESS"

    def test_quota_exceeded_code(self):
        exc = QuotaExceededError("test")
        assert exc.error_code == "QUOTA_EXCEEDED"

    def test_unauthorized_code(self):
        exc = UnauthorizedError("test")
        assert exc.error_code == "UNAUTHORIZED"

    def test_forbidden_code(self):
        exc = ForbiddenError("test")
        assert exc.error_code == "FORBIDDEN"
