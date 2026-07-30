"""Tests for Request ID middleware."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.request_id import get_request_id
from app.middleware.request_id import RequestIDMiddleware


@pytest.fixture
def app():
    _app = FastAPI()

    @_app.get("/test")
    async def test_endpoint():
        return {"request_id": get_request_id()}

    _app.add_middleware(RequestIDMiddleware)
    return _app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestRequestIDMiddleware:
    def test_generates_id_when_missing(self, client):
        resp = client.get("/test")
        assert resp.status_code == 200
        body = resp.json()
        assert body["request_id"] is not None
        assert len(body["request_id"]) == 32
        # Response header should have the same ID
        assert resp.headers["X-Request-ID"] == body["request_id"]

    def test_accepts_custom_id(self, client):
        custom_id = "my-custom-request-id"
        resp = client.get("/test", headers={"X-Request-ID": custom_id})
        body = resp.json()
        assert body["request_id"] == custom_id
        assert resp.headers["X-Request-ID"] == custom_id

    def test_truncates_long_id(self, client):
        long_id = "a" * 100
        resp = client.get("/test", headers={"X-Request-ID": long_id})
        body = resp.json()
        assert len(body["request_id"]) == 64
