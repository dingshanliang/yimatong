"""A1-010: API healthcheck 与集成验证"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_ok(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_health_detail_exists(self):
        # 需要注册 /health/detail 路由
        resp = client.get("/health/detail")
        assert resp.status_code in (200, 503)  # 服务不可用时返回 503
