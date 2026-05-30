"""W9: 外码/内码双码系统测试"""

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
async def setup_tenant(client: AsyncClient):
    """创建租户+品牌+产品+SKU，返回 tenant_id 和 headers"""
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "双码测试租户",
            "admin_email": "dualcode@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post(
        "/api/v1/brands",
        json={"name": "测试品牌"},
        headers=headers,
    )
    product = await client.post(
        "/api/v1/products",
        json={"brand_id": brand.json()["id"], "name": "测试产品"},
        headers=headers,
    )
    sku = await client.post(
        "/api/v1/skus",
        json={
            "product_id": product.json()["id"],
            "code": "SKU-001",
            "name": "默认规格",
            "specifications": {},
        },
        headers=headers,
    )
    return tid, headers, brand.json()["id"], product.json()["id"], sku.json()["id"]


class TestDualCodeModel:
    """W9-001: CodeItem 扩展 — outer/inner 配对模型"""

    @pytest.mark.anyio
    async def test_create_paired_batch(self, client: AsyncClient, setup_tenant):
        """创建配对码批次：外码+内码配对生成"""
        tid, headers, brand_id, product_id, sku_id = setup_tenant
        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "batch_code": "DUAL-001",
                "quantity": 5,
                "code_type": "paired",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["code_type"] == "paired"
        # 配对批次应生成 2x quantity 个码（外码+内码各 5 个）
        assert data["generated_count"] == 10

    @pytest.mark.anyio
    async def test_code_item_has_type_and_pair(self, client: AsyncClient, setup_tenant, db_session: AsyncSession):
        """码项应包含 code_type(outer/inner) 和 pair_id 字段"""
        tid, headers, brand_id, product_id, sku_id = setup_tenant
        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "batch_code": "DUAL-002",
                "quantity": 3,
                "code_type": "paired",
            },
            headers=headers,
        )
        batch_id = resp.json()["id"]

        # 查询码项
        items_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}",
            headers=headers,
        )
        items = items_resp.json()["items"]
        assert len(items) == 6  # 3 outer + 3 inner

        outer_codes = [i for i in items if i["code_type"] == "outer"]
        inner_codes = [i for i in items if i["code_type"] == "inner"]
        assert len(outer_codes) == 3
        assert len(inner_codes) == 3

        # 每个 outer 应有 pair_id 对应一个 inner
        outer_pair_ids = {i["pair_id"] for i in outer_codes}
        inner_pair_ids = {i["pair_id"] for i in inner_codes}
        assert outer_pair_ids == inner_pair_ids

    @pytest.mark.anyio
    async def test_single_code_batch_still_works(self, client: AsyncClient, setup_tenant):
        """非配对批次（默认 single 类型）仍正常工作"""
        tid, headers, brand_id, product_id, sku_id = setup_tenant
        resp = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "batch_code": "SINGLE-001",
                "quantity": 5,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["code_type"] == "single"
        assert data["generated_count"] == 5


class TestOuterCodeResolve:
    """W9-003: 外码解析 — 引流页"""

    @pytest.mark.anyio
    async def test_outer_code_shows_landing_page(self, client: AsyncClient, setup_tenant):
        """外码扫码展示引流页"""
        tid, headers, brand_id, product_id, sku_id = setup_tenant
        # 创建配对码批次并激活
        batch = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "batch_code": "DUAL-LAND",
                "quantity": 2,
                "code_type": "paired",
            },
            headers=headers,
        )
        batch_id = batch.json()["id"]
        await client.post(
            f"/api/v1/code-batches/{batch_id}/activate",
            headers=headers,
        )

        # 获取外码 public_id
        items_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}",
            headers=headers,
        )
        items = items_resp.json()["items"]
        outer_code = next(i for i in items if i["code_type"] == "outer")

        # 通过 resolver 访问外码
        resolve_resp = await client.get(f"/c/{outer_code['public_id']}")
        assert resolve_resp.status_code == 200
        assert "引流" in resolve_resp.text or "内码" in resolve_resp.text


class TestInnerCodeResolve:
    """W9-004: 内码解析 — 验真+领奖"""

    @pytest.mark.anyio
    async def test_inner_code_verifies_authentic(self, client: AsyncClient, setup_tenant):
        """内码扫码展示验真结果"""
        tid, headers, brand_id, product_id, sku_id = setup_tenant
        batch = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "batch_code": "DUAL-VERIFY",
                "quantity": 2,
                "code_type": "paired",
            },
            headers=headers,
        )
        batch_id = batch.json()["id"]
        await client.post(
            f"/api/v1/code-batches/{batch_id}/activate",
            headers=headers,
        )

        items_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}",
            headers=headers,
        )
        items = items_resp.json()["items"]
        inner_code = next(i for i in items if i["code_type"] == "inner")

        resolve_resp = await client.get(f"/c/{inner_code['public_id']}")
        assert resolve_resp.status_code == 200
        assert "验真" in resolve_resp.text or "正品" in resolve_resp.text


class TestPairQuery:
    """W9-005: 配对码查询 API"""

    @pytest.mark.anyio
    async def test_get_pair_info(self, client: AsyncClient, setup_tenant):
        """查询配对信息：给定一个码，返回其配对码"""
        tid, headers, brand_id, product_id, sku_id = setup_tenant
        batch = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "batch_code": "DUAL-PAIR",
                "quantity": 2,
                "code_type": "paired",
            },
            headers=headers,
        )
        batch_id = batch.json()["id"]

        items_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}",
            headers=headers,
        )
        items = items_resp.json()["items"]
        outer_code = next(i for i in items if i["code_type"] == "outer")

        # 查询配对信息
        pair_resp = await client.get(
            f"/api/v1/code-items/{outer_code['id']}/pair",
            headers=headers,
        )
        assert pair_resp.status_code == 200
        pair_data = pair_resp.json()
        assert pair_data["code_type"] == "inner"
        assert pair_data["pair_id"] == outer_code["pair_id"]
