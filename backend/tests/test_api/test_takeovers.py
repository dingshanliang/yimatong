"""既有码接管的公开业务流程测试。"""

import io
import uuid
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import get_db, get_db_with_bypass
from app.main import app
from app.models.code import CodeBatch, CodeBatchStatus, CodeItem, CodeItemStatus
from app.models.product import SKU, Brand, Product, ProductionBatch
from app.models.takeover import TakeoverMode, TakeoverProject, TakeoverProjectStatus
from app.services.takeover import build_takeover_launch_gate_check
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
async def test_legacy_redirect_waits_for_external_execution_then_observes_and_rolls_back(client, db):
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
        json={"execution_reference": "legacy-change-001"},
        headers=_headers(tenant_id, account_id),
    )
    assert external.status_code == 200
    probe = await client.post(
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
    assert probe.status_code == 200
    assert probe.json()["observation"]["recommendation"] == "continue"
    gateway = await client.get(
        "/api/v1/takeover/gateway",
        params={"project_id": project_id, "legacy_url": "https://legacy.example.com/scan/OLD-001"},
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
        params={"project_id": project_id, "legacy_url": "https://legacy.example.com/scan/OLD-001"},
        headers={"host": "other-brand.example.com"},
    )
    assert cross_tenant.status_code == 404
    rollback = await client.post(
        f"/api/v1/takeovers/{project_id}/routes/{route_id}/rollback",
        json={"reason": "旧系统异常", "idempotency_key": "rollback-001"},
        headers=_headers(tenant_id, account_id),
    )
    assert rollback.status_code == 200
    assert rollback.json()["status"] == "rolled_back"
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
        lambda domain, expected: {
            "status": "passed",
            "observed_cnames": [expected],
            "observed_ips": ["203.0.113.10"],
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
