"""既有码接管的公开业务流程测试。"""

import io
import uuid
from datetime import date
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.v1 import takeovers as takeovers_api
from app.core.database import get_db, get_db_with_bypass
from app.main import app
from app.models.code import CodeBatch, CodeBatchStatus, CodeItem, CodeItemStatus
from app.models.export_log import ExportLog
from app.models.product import SKU, Brand, Product, ProductionBatch
from app.models.takeover import TakeoverMode, TakeoverProject, TakeoverProjectStatus
from app.services.redis_cache import SharedSecurityCacheUnavailable
from app.services.takeover import (
    MAX_IMPORT_BYTES,
    _resolve_public_addresses,
    build_takeover_launch_gate_check,
)
from app.services.takeover_admission import _takeover_probe_rate_cache
from app.utils.security import create_access_token


@pytest.fixture
async def client(db):
    async def override_get_db():
        yield db

    async def override_get_db_with_bypass():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_with_bypass] = override_get_db_with_bypass
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def allow_takeover_probe_admission(monkeypatch):
    monkeypatch.setattr(
        _takeover_probe_rate_cache,
        "rate_limit_check_shared",
        AsyncMock(return_value=(True, 0)),
    )


def _headers(tenant_id: uuid.UUID, account_id: uuid.UUID, role: str = "admin"):
    token = create_access_token(str(tenant_id), str(account_id), role, tenant_type="brand")
    return {"Authorization": f"Bearer {token}"}


async def _create_project(client, tenant_id, account_id, **overrides):
    payload = {
        "name": "既有码接管基准项目",
        "source_system": "legacy-erp",
        "mode": "legacy_redirect",
        "source_domain": "legacy.example.com",
        "sample_url": "https://legacy.example.com/scan/OLD-001",
        "url_rule": {"kind": "path_tail"},
        "control_facts": {
            "domain_control": False,
            "old_system_control": True,
            "product_mapping": True,
            "batch_mapping": True,
            "channel_mapping": False,
        },
        "responsible_person": "运营负责人",
        "technical_owner": "旧系统负责人",
        "rollback_contact": "回退联系人",
        "fallback_url": "https://legacy.example.com/fallback",
        **overrides,
    }
    return await client.post("/api/v1/takeovers", json=payload, headers=_headers(tenant_id, account_id))


@pytest.mark.anyio
async def test_create_takeover_returns_fact_based_assessment(client):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()

    response = await _create_project(client, tenant_id, account_id)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["assessment"]["code_type"] == "unique"
    assert body["assessment"]["recommended_mode"] == "legacy_redirect"
    assert body["assessment"]["capabilities"]["marketing"]["level"] == "full"
    assert body["assessment"]["capabilities"]["diversion"]["level"] == "degraded"


@pytest.mark.anyio
async def test_takeover_collection_endpoints_are_bounded_and_report_total(client):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    for index in range(3):
        response = await _create_project(client, tenant_id, account_id, name=f"接管项目 {index}")
        assert response.status_code == 201

    projects = await client.get(
        "/api/v1/takeovers",
        params={"page": 2, "page_size": 1},
        headers=_headers(tenant_id, account_id),
    )

    assert projects.status_code == 200
    assert projects.json()["total"] == 3
    assert projects.json()["page"] == 2
    assert projects.json()["page_size"] == 1
    assert len(projects.json()["items"]) == 1


@pytest.mark.anyio
async def test_takeover_backend_role_matrix_denies_viewer_and_operator_lifecycle_actions(client):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    project = await _create_project(client, tenant_id, account_id)
    project_id = project.json()["id"]

    viewer_list = await client.get(
        "/api/v1/takeovers",
        headers=_headers(tenant_id, uuid.uuid4(), "viewer"),
    )
    operator_approve = await client.post(
        f"/api/v1/takeovers/{project_id}/confirm",
        headers=_headers(tenant_id, uuid.uuid4(), "operator"),
    )
    operator_execute = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{uuid.uuid4()}/cutover",
        params={"idempotency_key": "operator-cutover"},
        headers=_headers(tenant_id, uuid.uuid4(), "operator"),
    )
    operator_rollback = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{uuid.uuid4()}/rollback",
        json={"reason": "operator rollback", "idempotency_key": "operator-rollback"},
        headers=_headers(tenant_id, uuid.uuid4(), "operator"),
    )

    assert viewer_list.status_code == 403
    assert operator_approve.status_code == 403
    assert operator_execute.status_code == 403
    assert operator_rollback.status_code == 403


@pytest.mark.anyio
async def test_takeover_import_rejects_non_csv_before_parsing(client):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    project = await _create_project(client, tenant_id, account_id)

    response = await client.post(
        f"/api/v1/takeovers/{project.json()['id']}/imports/dry-run",
        files={"file": ("legacy.txt", io.BytesIO(b"legacy_code,internal_public_id\n"), "text/plain")},
        headers=_headers(tenant_id, account_id),
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "Unsupported import file type"


@pytest.mark.anyio
async def test_takeover_import_rejects_empty_csv_without_creating_job(client):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    project = await _create_project(client, tenant_id, account_id)
    project_id = project.json()["id"]

    response = await client.post(
        f"/api/v1/takeovers/{project_id}/imports/dry-run",
        files={"file": ("legacy.csv", io.BytesIO(b" \r\n"), "text/csv")},
        headers=_headers(tenant_id, account_id),
    )
    imports = await client.get(
        f"/api/v1/takeovers/{project_id}/imports",
        headers=_headers(tenant_id, account_id),
    )

    assert response.status_code == 400
    assert imports.json()["total"] == 0
    assert imports.json()["page"] == 1
    assert imports.json()["page_size"] == 50


def test_takeover_probe_rejects_unbounded_dns_fanout(monkeypatch):
    monkeypatch.setattr(
        "app.services.takeover.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, (f"8.8.8.{index}", 443)) for index in range(1, 10)],
    )

    with pytest.raises(HTTPException) as exc_info:
        _resolve_public_addresses("legacy.example.com", 443)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Domain resolves to too many addresses"


@pytest.mark.anyio
async def test_public_gateway_rejects_malformed_hosts_without_server_error(client):
    for host in ("127.0.0.1", "bad_host.example.com", "not-a-domain"):
        response = await client.get(
            "/api/v1/takeover/gateway/scan/OLD-001",
            headers={"host": host},
        )
        assert response.status_code == 404


@pytest.mark.anyio
async def test_takeover_error_export_neutralizes_formulas_and_audits_actor(client, db):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    project = await _create_project(client, tenant_id, account_id)
    project_id = project.json()["id"]
    dry_run = await client.post(
        f"/api/v1/takeovers/{project_id}/imports/dry-run",
        files={
            "file": (
                "legacy.csv",
                io.BytesIO(b"legacy_code,internal_public_id\n=1+1,\n"),
                "text/csv",
            )
        },
        headers=_headers(tenant_id, account_id),
    )
    job_id = dry_run.json()["id"]

    response = await client.post(
        f"/api/v1/takeovers/{project_id}/imports/{job_id}/errors.csv",
        json={"reason": "复核既有码导入错误"},
        headers={**_headers(tenant_id, account_id), "Idempotency-Key": str(uuid.uuid4())},
    )
    export = await db.scalar(
        select(ExportLog).where(
            ExportLog.tenant_id == tenant_id,
            ExportLog.export_type == "takeover_import_errors_csv",
            ExportLog.resource_id == uuid.UUID(job_id),
        )
    )

    assert response.status_code == 200
    assert "'=1+1" in response.content.decode("utf-8-sig")
    assert export is not None
    assert export.reason == "复核既有码导入错误"
    assert export.scope_snapshot == {
        "errors_only": True,
        "job_id": job_id,
        "project_id": project_id,
        "row_limit": 50_000,
    }
    assert response.headers["X-Content-SHA256"] == export.checksum_sha256


@pytest.mark.anyio
@pytest.mark.parametrize("reason", [None, "   ", "x" * 501])
async def test_takeover_error_reason_validation_precedes_resource_query(client, monkeypatch, reason):
    project_lookup = AsyncMock()
    monkeypatch.setattr(takeovers_api, "_require_project", project_lookup)
    body = {} if reason is None else {"reason": reason}

    response = await client.post(
        f"/api/v1/takeovers/{uuid.uuid4()}/imports/{uuid.uuid4()}/errors.csv",
        json=body,
        headers={**_headers(uuid.uuid4(), uuid.uuid4()), "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 422
    project_lookup.assert_not_awaited()


@pytest.mark.anyio
async def test_takeover_import_rejects_oversized_csv_before_parsing(client):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    project = await _create_project(client, tenant_id, account_id)

    response = await client.post(
        f"/api/v1/takeovers/{project.json()['id']}/imports/dry-run",
        files={"file": ("legacy.csv", io.BytesIO(b"x" * (MAX_IMPORT_BYTES + 1)), "text/csv")},
        headers=_headers(tenant_id, account_id),
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Import file is too large"


@pytest.mark.anyio
async def test_takeover_import_rejects_missing_mime_type(client):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    project = await _create_project(client, tenant_id, account_id)

    response = await client.post(
        f"/api/v1/takeovers/{project.json()['id']}/imports/dry-run",
        files={"file": ("legacy.csv", io.BytesIO(b"legacy_code,internal_public_id\n"), "")},
        headers=_headers(tenant_id, account_id),
    )

    assert response.status_code == 415


@pytest.mark.anyio
async def test_takeover_import_accepts_browser_csv_mime_type(client):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    project = await _create_project(client, tenant_id, account_id)

    response = await client.post(
        f"/api/v1/takeovers/{project.json()['id']}/imports/dry-run",
        files={
            "file": (
                "legacy.csv",
                io.BytesIO(b"legacy_code,internal_public_id\nOLD-1,YM-NOT-FOUND\n"),
                "application/vnd.ms-excel",
            )
        },
        headers=_headers(tenant_id, account_id),
    )

    assert response.status_code == 201


@pytest.mark.anyio
async def test_takeover_probe_admission_is_rate_limited_before_network(client, monkeypatch):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    project = await _create_project(
        client,
        tenant_id,
        account_id,
        mode="cname",
        consumer_domain="scan.rate-limit.example",
    )
    monkeypatch.setattr(
        _takeover_probe_rate_cache,
        "rate_limit_check_shared",
        AsyncMock(return_value=(False, 10)),
    )

    response = await client.post(
        f"/api/v1/takeovers/{project.json()['id']}/domains/check",
        headers=_headers(tenant_id, account_id),
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"


@pytest.mark.anyio
async def test_takeover_probe_admission_fails_closed_when_shared_cache_is_unavailable(client, monkeypatch):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    project = await _create_project(
        client,
        tenant_id,
        account_id,
        mode="cname",
        consumer_domain="scan.cache-down.example",
    )
    monkeypatch.setattr(
        _takeover_probe_rate_cache,
        "rate_limit_check_shared",
        AsyncMock(side_effect=SharedSecurityCacheUnavailable("unavailable")),
    )

    response = await client.post(
        f"/api/v1/takeovers/{project.json()['id']}/domains/check",
        headers=_headers(tenant_id, account_id),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Takeover probe service is temporarily unavailable"


@pytest.mark.anyio
async def test_dry_run_submit_and_preview_keep_alias_staged_until_cutover(client, db):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    brand = Brand(tenant_id=tenant_id, name="基准品牌")
    product = Product(tenant_id=tenant_id, brand=brand, name="基准产品")
    sku = SKU(tenant_id=tenant_id, product=product, code="SKU-001", name="标准规格")
    production_batch = ProductionBatch(
        tenant_id=tenant_id,
        product=product,
        sku=sku,
        batch_code="BATCH-001",
        production_date=date(2026, 1, 1),
        expiry_date=date(2027, 1, 1),
    )
    db.add_all([brand, product, sku, production_batch])
    await db.flush()
    code_batch = CodeBatch(
        tenant_id=tenant_id,
        product_id=product.id,
        sku_id=sku.id,
        production_batch_id=production_batch.id,
        batch_code="INTERNAL-001",
        quantity=1,
        status=CodeBatchStatus.activated,
        created_by=account_id,
    )
    db.add(code_batch)
    await db.flush()
    code_item = CodeItem(
        tenant_id=tenant_id,
        code_batch_id=code_batch.id,
        public_id="YMTEST001",
        status=CodeItemStatus.activated,
    )
    db.add(code_item)
    await db.flush()

    project_response = await _create_project(client, tenant_id, account_id)
    project_id = project_response.json()["id"]
    csv_content = (
        "legacy_code,internal_public_id,product_code,sku_code,batch_code\n"
        f"OLD-001,{code_item.public_id},,SKU-001,BATCH-001\n"
    ).encode()

    dry_run = await client.post(
        f"/api/v1/takeovers/{project_id}/imports/dry-run",
        files={"file": ("legacy.csv", io.BytesIO(csv_content), "text/csv")},
        headers=_headers(tenant_id, account_id),
    )
    assert dry_run.status_code == 201
    assert dry_run.json()["counts"]["valid"] == 1
    assert dry_run.json()["counts"]["failed"] == 0

    submitted = await client.post(
        f"/api/v1/takeovers/{project_id}/imports/{dry_run.json()['id']}/submit",
        headers=_headers(tenant_id, account_id),
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "completed"

    aliases = await client.get(
        f"/api/v1/takeovers/{project_id}/aliases",
        params={"page": 1, "page_size": 1},
        headers=_headers(tenant_id, account_id),
    )
    assert aliases.status_code == 200
    assert aliases.json()["total"] == 1
    assert aliases.json()["page"] == 1
    assert aliases.json()["page_size"] == 1
    assert len(aliases.json()["items"]) == 1

    preview = await client.get(
        f"/api/v1/takeovers/{project_id}/aliases/preview",
        params={"url": "https://legacy.example.com/scan/OLD-001"},
        headers=_headers(tenant_id, account_id),
    )
    assert preview.status_code == 200
    assert preview.json()["target"]["public_id"] == code_item.public_id
    assert preview.json()["target"]["status"] == "staged"


@pytest.mark.anyio
async def test_takeover_isolation_and_cname_readiness_gate(client):
    owner_tenant = uuid.uuid4()
    other_tenant = uuid.uuid4()
    account_id = uuid.uuid4()
    created = await _create_project(
        client,
        owner_tenant,
        account_id,
        mode="cname",
        consumer_domain="scan.brand.example.com",
        control_facts={"domain_control": True, "old_system_control": False},
    )
    project_id = created.json()["id"]

    hidden = await client.get(
        f"/api/v1/takeovers/{project_id}",
        headers=_headers(other_tenant, uuid.uuid4()),
    )
    assert hidden.status_code == 404

    readiness = await client.get(
        f"/api/v1/takeovers/{project_id}/readiness",
        headers=_headers(owner_tenant, account_id),
    )
    assert readiness.status_code == 200
    checks = {item["key"]: item for item in readiness.json()["checks"]}
    assert checks["consumer_domain_verified"]["passed"] is False

    confirmation = await client.post(
        f"/api/v1/takeovers/{project_id}/confirm",
        headers=_headers(owner_tenant, account_id),
    )
    assert confirmation.status_code == 409


@pytest.mark.anyio
async def test_domain_check_rejects_private_resolution_without_opening_a_socket(client, monkeypatch):
    from app.services import takeover as takeover_service

    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    created = await _create_project(
        client,
        tenant_id,
        account_id,
        mode="cname",
        consumer_domain="scan.internal.example",
        control_facts={"domain_control": True, "old_system_control": False},
    )
    socket_attempts: list[tuple] = []
    monkeypatch.setattr(
        takeover_service.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    monkeypatch.setattr(
        takeover_service.socket,
        "create_connection",
        lambda *args, **kwargs: socket_attempts.append((args, kwargs)),
    )

    response = await client.post(
        f"/api/v1/takeovers/{created.json()['id']}/domains/check",
        headers=_headers(tenant_id, account_id),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Domain must resolve only to public IP addresses"
    assert socket_attempts == []


@pytest.mark.anyio
async def test_takeover_project_rejects_ip_literal_domain(client):
    response = await _create_project(
        client,
        uuid.uuid4(),
        uuid.uuid4(),
        mode="cname",
        consumer_domain="127.0.0.1",
        control_facts={"domain_control": True},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_legacy_redirect_waits_for_external_execution_then_observes_and_rolls_back(client, db, monkeypatch):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    brand = Brand(tenant_id=tenant_id, name="基准品牌")
    product = Product(tenant_id=tenant_id, brand=brand, name="基准产品")
    sku = SKU(tenant_id=tenant_id, product=product, code="SKU-001", name="标准规格")
    production_batch = ProductionBatch(
        tenant_id=tenant_id,
        product=product,
        sku=sku,
        batch_code="BATCH-001",
        production_date=date(2026, 1, 1),
        expiry_date=date(2027, 1, 1),
    )
    db.add_all([brand, product, sku, production_batch])
    await db.flush()
    code_batch = CodeBatch(
        tenant_id=tenant_id,
        product_id=product.id,
        sku_id=sku.id,
        production_batch_id=production_batch.id,
        batch_code="INTERNAL-001",
        quantity=1,
        status=CodeBatchStatus.activated,
        created_by=account_id,
    )
    db.add(code_batch)
    await db.flush()
    code_item = CodeItem(
        tenant_id=tenant_id,
        code_batch_id=code_batch.id,
        public_id="YMTEST002",
        status=CodeItemStatus.activated,
    )
    db.add(code_item)
    await db.flush()

    created = await _create_project(
        client,
        tenant_id,
        account_id,
        control_facts={
            "domain_control": False,
            "old_system_control": True,
            "product_mapping": True,
            "batch_mapping": True,
            "monitoring_ready": True,
        },
    )
    project_id = created.json()["id"]
    csv_content = (
        f"legacy_code,internal_public_id,sku_code,batch_code\nOLD-001,{code_item.public_id},SKU-001,BATCH-001\n"
    ).encode()
    dry_run = await client.post(
        f"/api/v1/takeovers/{project_id}/imports/dry-run",
        files={"file": ("legacy.csv", io.BytesIO(csv_content), "text/csv")},
        headers=_headers(tenant_id, account_id),
    )
    await client.post(
        f"/api/v1/takeovers/{project_id}/imports/{dry_run.json()['id']}/submit",
        headers=_headers(tenant_id, account_id),
    )
    readiness = await client.get(
        f"/api/v1/takeovers/{project_id}/readiness",
        headers=_headers(tenant_id, account_id),
    )
    assert readiness.json()["ready"] is True
    confirmed = await client.post(
        f"/api/v1/takeovers/{project_id}/confirm",
        headers=_headers(tenant_id, account_id),
    )
    assert confirmed.status_code == 200

    route = await client.post(
        f"/api/v1/takeovers/{project_id}/routes",
        json={
            "source_url": "https://legacy.example.com/scan/OLD-001",
            "target_url": "https://h5.example.com/c/{public_id}",
            "sample_codes": ["OLD-001"],
        },
        headers=_headers(tenant_id, account_id),
    )
    route_id = route.json()["id"]
    confirmed_route = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{route_id}/confirm",
        params={"idempotency_key": "confirm-001"},
        headers=_headers(tenant_id, account_id),
    )
    assert confirmed_route.status_code == 200
    blocked = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{route_id}/cutover",
        params={"idempotency_key": "cutover-before-external"},
        headers=_headers(tenant_id, account_id),
    )
    assert blocked.status_code == 409
    external = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{route_id}/external-execution",
        json={"execution_reference": "legacy-change-001", "idempotency_key": "external-001"},
        headers=_headers(tenant_id, account_id),
    )
    assert external.status_code == 200
    monkeypatch.setattr(
        "app.services.takeover._probe_https_redirect_sync",
        lambda _url: {
            "status_code": 307,
            "observed_target": "https://h5.example.com/c/YMTEST002",
            "latency_ms": 12.0,
        },
    )
    probe_before_cutover = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{route_id}/probe",
        json={"checked_url": "https://legacy.example.com/scan/OLD-001"},
        headers=_headers(tenant_id, account_id),
    )
    assert probe_before_cutover.status_code == 200
    assert probe_before_cutover.json()["observation"]["evidence_purpose"] == "pre_cutover"
    cutover = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{route_id}/cutover",
        params={"idempotency_key": "cutover-after-external"},
        headers=_headers(tenant_id, account_id),
    )
    assert cutover.status_code == 200
    forged = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{route_id}/probe",
        json={
            "checked_url": "https://legacy.example.com/scan/OLD-001",
            "success_rate": 1,
            "error_rate": 0,
            "h5_reach_rate": 1,
            "target_match": True,
        },
        headers=_headers(tenant_id, account_id),
    )
    assert forged.status_code == 422
    probe = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{route_id}/probe",
        json={"checked_url": "https://legacy.example.com/scan/OLD-001"},
        headers=_headers(tenant_id, account_id),
    )
    assert probe.status_code == 200
    assert probe.json()["observation"]["recommendation"] == "continue"
    gateway = await client.get(
        "/api/v1/takeover/gateway",
        params={"legacy_url": "https://legacy.example.com/scan/OLD-001"},
        headers={"host": "legacy.example.com"},
    )
    assert gateway.status_code == 200
    assert gateway.json()["action"] == "new_chain"
    redirect = await client.get(
        "/api/v1/takeover/gateway/scan/OLD-001",
        headers={"host": "legacy.example.com"},
    )
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "https://h5.example.com/c/YMTEST002"
    cross_tenant = await client.get(
        "/api/v1/takeover/gateway",
        params={"legacy_url": "https://legacy.example.com/scan/OLD-001"},
        headers={"host": "other-brand.example.com"},
    )
    assert cross_tenant.status_code == 404
    blank_rollback = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{route_id}/rollback",
        json={"reason": "   ", "idempotency_key": "rollback-blank"},
        headers=_headers(tenant_id, account_id),
    )
    extra_rollback = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{route_id}/rollback",
        json={"reason": "旧系统异常", "idempotency_key": "rollback-extra", "force": True},
        headers=_headers(tenant_id, account_id),
    )
    assert blank_rollback.status_code == 422
    assert extra_rollback.status_code == 422
    rollback = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{route_id}/rollback",
        json={"reason": "旧系统异常", "idempotency_key": "rollback-001"},
        headers=_headers(tenant_id, account_id),
    )
    assert rollback.status_code == 200
    assert rollback.json()["status"] == "rolling_back"
    monkeypatch.setattr(
        "app.services.takeover._probe_https_redirect_sync",
        lambda _url: {
            "status_code": 307,
            "observed_target": "https://attacker.example.com/not-fallback",
            "latency_ms": 12.0,
        },
    )
    failed_verification = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{route_id}/rollback/verify",
        params={"idempotency_key": "rollback-001"},
        headers=_headers(tenant_id, account_id),
    )
    assert failed_verification.status_code == 200
    assert failed_verification.json()["route"]["status"] == "rolling_back"
    assert failed_verification.json()["observation"]["status"] == "failed"
    durable_rollback = await client.get(
        f"/api/v1/takeovers/{project_id}/routes",
        headers=_headers(tenant_id, account_id),
    )
    assert durable_rollback.json()["items"][0]["status"] == "rolling_back"
    monkeypatch.setattr(
        "app.services.takeover._probe_https_redirect_sync",
        lambda _url: {
            "status_code": 307,
            "observed_target": "https://legacy.example.com/fallback",
            "latency_ms": 12.0,
        },
    )
    verified = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{route_id}/rollback/verify",
        params={"idempotency_key": "rollback-001"},
        headers=_headers(tenant_id, account_id),
    )
    assert verified.status_code == 200
    assert verified.json()["route"]["status"] == "rolled_back"
    fallback = await client.get(
        "/api/v1/takeover/gateway/scan/OLD-001",
        headers={"host": "legacy.example.com"},
    )
    assert fallback.status_code == 307
    assert fallback.headers["location"] == "https://legacy.example.com/fallback"


@pytest.mark.anyio
async def test_cname_domain_check_and_deterministic_gateway(client, db, monkeypatch):
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    project_response = await _create_project(
        client,
        tenant_id,
        account_id,
        mode="cname",
        source_domain="legacy-origin.example.com",
        consumer_domain="scan.brand.example.com",
        sample_url="https://scan.brand.example.com/scan/OLD-001",
        fallback_url="https://legacy-origin.example.com/fallback",
        control_facts={
            "domain_control": True,
            "old_system_control": False,
            "monitoring_ready": True,
        },
    )
    project_id = project_response.json()["id"]

    from app.services import takeover as takeover_service

    monkeypatch.setattr(
        takeover_service,
        "_inspect_dns_and_tls_sync",
        lambda domain, expected, token: {
            "status": "passed",
            "observed_cnames": [expected],
            "observed_ips": ["203.0.113.10"],
            "observed_ownership_tokens": [f"yimatong-verification={token}"],
            "ownership_verified": True,
            "ttl": 60,
            "tls_status": "active",
            "certificate_expires_at": None,
            "failure_reason": None,
            "raw_observation": {"source": "test"},
        },
    )
    checked = await client.post(
        f"/api/v1/takeovers/{project_id}/domains/check",
        headers=_headers(tenant_id, account_id),
    )
    assert checked.status_code == 200
    assert checked.json()["status"] == "passed"
    assert checked.json()["observed_cnames"] == ["cname.yimatong.cn"]
    assert checked.json()["ownership_verified"] is True

    fixed = await _create_project(
        client,
        tenant_id,
        account_id,
        url_rule={"kind": "fixed"},
        control_facts={"code_type": "fixed", "product_mapping": True, "batch_mapping": True},
    )
    assert fixed.status_code == 201
    assert fixed.json()["assessment"]["code_type"] == "shared"
    assert fixed.json()["assessment"]["capabilities"]["light_verification"]["level"] == "degraded"


@pytest.mark.anyio
async def test_takeover_closed_loop_is_required_by_generic_launch_gate(db):
    tenant_id = uuid.uuid4()
    common = {
        "tenant_id": tenant_id,
        "name": "双模式闭环基准项目",
        "source_system": "legacy-erp",
        "source_domain": "legacy.example.com",
        "consumer_domain": "scan.brand.example.com",
        "expected_cname": "cname.yimatong.cn",
        "sample_url": "https://legacy.example.com/scan/OLD-001",
        "url_rule": {"kind": "path_tail"},
        "code_scope": {},
        "control_facts": {},
        "responsible_person": "运营负责人",
        "technical_owner": "技术负责人",
        "rollback_contact": "回退联系人",
        "fallback_url": "https://legacy-origin.example.com/fallback",
        "assessment": {"version": 1},
        "readiness_snapshot": {},
        "created_by": uuid.uuid4(),
    }
    db.add_all(
        [
            TakeoverProject(
                **common,
                mode=TakeoverMode.legacy_redirect,
                status=TakeoverProjectStatus.completed,
            ),
            TakeoverProject(
                **{
                    **common,
                    "name": "CNAME 闭环基准项目",
                    "consumer_domain": "cname.brand.example.com",
                },
                mode=TakeoverMode.cname,
                status=TakeoverProjectStatus.needs_fix,
            ),
        ]
    )
    await db.flush()

    blocked = await build_takeover_launch_gate_check(db, tenant_id)
    assert blocked and blocked["passed"] is False

    cname_project = await db.scalar(
        select(TakeoverProject).where(
            TakeoverProject.tenant_id == tenant_id,
            TakeoverProject.mode == TakeoverMode.cname,
        )
    )
    cname_project.status = TakeoverProjectStatus.completed
    await db.flush()
    passed = await build_takeover_launch_gate_check(db, tenant_id)
    assert passed and passed["passed"] is True
