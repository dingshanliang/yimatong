"""B2-005/006: 行业模板 API 测试"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def auth_setup(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "行业模板测试",
            "admin_email": "tpl@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return tid, {"Authorization": f"Bearer {token}"}


class TestIndustryTemplates:
    @pytest.mark.anyio
    async def test_list_industry_templates(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        resp = await client.get(
            "/api/v1/page-templates/industry-templates",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        names = [t["name"] for t in data]
        assert "食品溯源页" in names
        assert "农产品溯源页" in names
        assert "礼盒页" in names

    @pytest.mark.anyio
    async def test_clone_industry_template(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        resp = await client.post(
            "/api/v1/page-templates/industry-templates/0/clone",
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["template"]["name"] == "食品溯源页"
        assert data["version"]["status"] == "draft"
        assert data["version"]["config_json"]["dsl_version"] == "1.0"

    @pytest.mark.anyio
    async def test_clone_invalid_index_404(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        resp = await client.post(
            "/api/v1/page-templates/industry-templates/99/clone",
            headers=headers,
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_food_template_has_required_modules(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        resp = await client.get(
            "/api/v1/page-templates/industry-templates",
            headers=headers,
        )
        food = resp.json()[0]
        module_types = [m["type"] for m in food["config_json"]["modules"]]
        assert "product_card" in module_types
        assert "traceability_timeline" in module_types
        assert "inspection_report" in module_types
