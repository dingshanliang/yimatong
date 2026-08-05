"""A1-007: Tenant scope 中间件验收测试"""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.tenant import TenantScopeMiddleware
from app.utils.security import create_access_token

app = FastAPI()
app.add_middleware(TenantScopeMiddleware)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/v1/test")
async def protected(request: Request):
    return {"tenant_id": request.state.tenant_id}


@app.post("/api/v1/test")
async def protected_write(request: Request):
    return {"tenant_id": request.state.tenant_id}


@app.post("/api/v1/products")
async def acting_write(request: Request):
    return {"tenant_id": request.state.tenant_id}


client = TestClient(app)


class TestPublicRoutes:
    def test_health_no_auth(self):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_public_resolve_route(self):
        """公开解析路由 /c/{public_id} 不需要认证"""

        @app.get("/c/{public_id}")
        async def resolve(public_id: str):
            return {"public_id": public_id}

        resp = client.get("/c/test123")
        assert resp.status_code == 200


class TestProtectedRoutes:
    def test_no_token_returns_401(self):
        resp = client.get("/api/v1/test")
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self):
        resp = client.get("/api/v1/test", headers={"Authorization": "Bearer invalid"})
        assert resp.status_code == 401

    def test_valid_token_passes(self):
        token = create_access_token("t-001", "a-001", "admin")
        resp = client.get("/api/v1/test", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_tenant_id_injected(self):
        token = create_access_token("t-001", "a-001", "admin")
        resp = client.get("/api/v1/test", headers={"Authorization": f"Bearer {token}"})
        assert resp.json()["tenant_id"] == "t-001"

    def test_authorization_header_takes_precedence_over_cookie(self):
        header_token = create_access_token("header-tenant", "a-001", "admin")
        cookie_token = create_access_token("cookie-tenant", "a-002", "admin")

        client.cookies.set("access_token", cookie_token)
        try:
            resp = client.get(
                "/api/v1/test",
                headers={"Authorization": f"Bearer {header_token}"},
            )
        finally:
            client.cookies.delete("access_token")

        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "header-tenant"

    def test_expired_plan_allows_read_but_blocks_write_with_stable_contract(self, monkeypatch):
        async def blocks_write(_self, _tenant_id):
            return True

        monkeypatch.setattr(TenantScopeMiddleware, "_tenant_plan_blocks_write", blocks_write)
        token = create_access_token("t-001", "a-001", "admin")
        headers = {"Authorization": f"Bearer {token}"}

        assert client.get("/api/v1/test", headers=headers).status_code == 200
        response = client.post("/api/v1/test", headers=headers)

        assert response.status_code == 403
        assert response.json() == {
            "code": "TENANT_PLAN_EXPIRED",
            "detail": "租户套餐已过期，当前仅支持查看；请联系平台续期",
        }

    def test_agency_acting_write_uses_client_tenant_plan(self, monkeypatch):
        checked_tenants: list[str | None] = []

        async def live_authorization(_self, _agency_id, _client_id):
            return ["products"]

        async def blocks_write(_self, tenant_id):
            checked_tenants.append(tenant_id)
            return True

        monkeypatch.setattr(TenantScopeMiddleware, "_load_acting_authorization", live_authorization)
        monkeypatch.setattr(TenantScopeMiddleware, "_tenant_plan_blocks_write", blocks_write)
        token = create_access_token(
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            "admin",
            tenant_type="agency",
            extra={"acting_tenant_id": "33333333-3333-3333-3333-333333333333"},
        )

        response = client.post(
            "/api/v1/products",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
        assert checked_tenants == ["33333333-3333-3333-3333-333333333333"]
