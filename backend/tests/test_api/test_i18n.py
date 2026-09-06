"""W22: 高级多语言与自动切换测试"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    from app.utils.security import create_access_token

    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=test-platform-csrf",
        "Origin": "http://localhost:3002",
        "X-Platform-CSRF": "test-platform-csrf",
    }


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
async def setup_tenant(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "多语言测试租户",
            "admin_email": "i18n@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


class TestI18nTemplate:
    """W22-001: 多语言模板管理"""

    @pytest.mark.anyio
    async def test_create_translation(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/i18n/translations",
            json={"key": "welcome_text", "locale": "zh", "value": "欢迎扫码"},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["value"] == "欢迎扫码"

    @pytest.mark.anyio
    async def test_get_translation(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/i18n/translations",
            json={"key": "welcome_text", "locale": "en", "value": "Welcome to scan"},
            headers=headers,
        )

        resp = await client.get(
            "/api/v1/i18n/translations",
            params={"locale": "en"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()) >= 1


class TestLanguageDetection:
    """W22-002: 浏览器语言检测"""

    @pytest.mark.anyio
    async def test_detect_language(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/i18n/detect",
            json={"accept_language": "zh-CN,zh;q=0.9,en;q=0.8"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["detected"] == "zh"


class TestTranslationWorkflow:
    """W22-003: 翻译工作流"""

    @pytest.mark.anyio
    async def test_batch_update_translations(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/i18n/translations/batch",
            json={
                "translations": [
                    {"key": "product_name", "locale": "zh", "value": "产品名称"},
                    {"key": "product_name", "locale": "en", "value": "Product Name"},
                ],
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2


class TestI18nWriteGuard:
    """i18n 写路由需要 tenant:manage 权限并写入审计。"""

    @pytest.mark.anyio
    async def test_write_requires_tenant_manage_permission(self, client: AsyncClient, setup_tenant):
        tid, _headers = setup_tenant
        operator_token = create_access_token(tid, "00000000-0000-0000-0000-000000000002", "operator")
        operator_headers = {"Authorization": f"Bearer {operator_token}"}

        create_resp = await client.post(
            "/api/v1/i18n/translations",
            json={"key": "welcome_text", "locale": "zh", "value": "欢迎扫码"},
            headers=operator_headers,
        )
        assert create_resp.status_code == 403

        batch_resp = await client.post(
            "/api/v1/i18n/translations/batch",
            json={"translations": [{"key": "k", "locale": "zh", "value": "v"}]},
            headers=operator_headers,
        )
        assert batch_resp.status_code == 403

        # 读取保持任意登录角色可用
        list_resp = await client.get("/api/v1/i18n/translations", headers=operator_headers)
        assert list_resp.status_code == 200

    @pytest.mark.anyio
    async def test_create_and_delete_write_audit_log(self, client: AsyncClient, setup_tenant, db_session):
        from app.models.audit import PlatformAuditLog

        tid, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/i18n/translations",
            json={"key": "audit_key", "locale": "zh", "value": "审计"},
            headers=headers,
        )
        assert create_resp.status_code == 201
        translation_id = create_resp.json()["id"]

        delete_resp = await client.delete(
            f"/api/v1/i18n/translations/{translation_id}",
            headers=headers,
        )
        assert delete_resp.status_code == 204

        actions = (
            (
                await db_session.execute(
                    select(PlatformAuditLog.action).where(
                        PlatformAuditLog.target_tenant_id == tid,
                        PlatformAuditLog.action.in_(["translation_create", "translation_delete"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(actions) == {"translation_create", "translation_delete"}
