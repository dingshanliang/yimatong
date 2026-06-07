"""A3-005: 文件上传服务 验收测试"""

from collections.abc import AsyncGenerator
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.services.storage import MAX_FILE_SIZE
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal

def _platform_admin_headers() -> dict:
    from app.utils.security import create_access_token
    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {"Authorization": f"Bearer {token}"}




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
async def tenant_with_auth(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "上传测试",
            "admin_email": "upload@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return tid, {"Authorization": f"Bearer {token}"}


class TestFileUpload:
    @pytest.mark.anyio
    async def test_upload_image_success(self, client: AsyncClient, tenant_with_auth):
        tid, headers = tenant_with_auth
        with patch("app.services.storage.get_storage_client") as mock_client:
            mock_s3 = MagicMock()
            mock_s3.put_object.return_value = None
            mock_client.return_value = mock_s3

            files = {"file": ("test.png", b"fake-png-content", "image/png")}
            resp = await client.post(
                "/api/v1/files/upload",
                files=files,
                data={"module": "brands"},
                headers=headers,
            )
            assert resp.status_code == 201
            data = resp.json()
            assert "file_url" in data
            assert "file_id" in data
            assert "public_url" in data
            assert str(tid) in data["file_url"]
            assert "brands" in data["file_url"]
            assert data["public_url"].startswith("http://test/api/v1/files/public/")

    @pytest.mark.anyio
    async def test_upload_invalid_format(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        files = {"file": ("test.txt", b"fake-text-content", "text/plain")}
        resp = await client.post(
            "/api/v1/files/upload",
            files=files,
            data={"module": "brands"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert "not allowed" in resp.json()["detail"].lower()

    @pytest.mark.anyio
    async def test_upload_pdf_success(self, client: AsyncClient, tenant_with_auth):
        tid, headers = tenant_with_auth
        with patch("app.services.storage.get_storage_client") as mock_client:
            mock_s3 = MagicMock()
            mock_s3.put_object.return_value = None
            mock_client.return_value = mock_s3

            files = {"file": ("report.pdf", b"%PDF-1.4 fake", "application/pdf")}
            resp = await client.post(
                "/api/v1/files/upload",
                files=files,
                data={"module": "product-document"},
                headers=headers,
            )

            assert resp.status_code == 201
            data = resp.json()
            assert str(tid) in data["file_url"]
            assert "product-document" in data["file_url"]
            assert data["public_url"].endswith(".pdf")

    @pytest.mark.anyio
    async def test_upload_file_too_large(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        large_content = b"x" * (MAX_FILE_SIZE + 1)
        files = {"file": ("big.png", large_content, "image/png")}
        resp = await client.post(
            "/api/v1/files/upload",
            files=files,
            data={"module": "brands"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert "size" in resp.json()["detail"].lower()

    @pytest.mark.anyio
    async def test_get_file_metadata(self, client: AsyncClient, tenant_with_auth):
        tid, headers = tenant_with_auth
        with (
            patch("app.services.storage.get_storage_client") as mock_client,
            patch("app.services.storage.get_file_record") as mock_get,
        ):
            file_id = "019e6bce-0000-0000-0000-000000000001"
            mock_get.return_value = {
                "id": file_id,
                "tenant_id": str(tid),
                "file_url": f"{tid}/brands/test.png",
                "module": "brands",
                "filename": "test.png",
                "content_type": "image/png",
                "size": 1024,
            }
            mock_s3 = MagicMock()
            mock_s3.generate_presigned_url.return_value = "https://minio.example.com/presigned"
            mock_client.return_value = mock_s3

            resp = await client.get(f"/api/v1/files/{file_id}", headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert "download_url" in data
            assert data["filename"] == "test.png"

    @pytest.mark.anyio
    async def test_public_file_endpoint_streams_uploaded_object(self, client: AsyncClient, tenant_with_auth):
        tid, _ = tenant_with_auth
        file_key = f"{tid}/brand-logo/test.png"
        with patch("app.services.storage.get_storage_client") as mock_client:
            mock_s3 = MagicMock()
            mock_s3.get_object.return_value = {
                "Body": BytesIO(b"logo-bytes"),
                "ContentType": "image/png",
            }
            mock_client.return_value = mock_s3

            resp = await client.get(f"/api/v1/files/public/{file_key}")

            assert resp.status_code == 200
            assert resp.content == b"logo-bytes"
            assert resp.headers["content-type"].startswith("image/png")
            mock_s3.get_object.assert_called_once_with(Bucket="yimatong", Key=file_key)
