"""码管理服务层测试

使用 SQLite 内存数据库 + AsyncSession 测试 app/services/code.py 中的核心函数：
- create_code_batch: 正常创建、产品不存在、SKU 不匹配
- activate_batch: 正常激活、状态校验
- freeze_batch / void_batch: 正常操作
- 状态转换校验（非法转换抛 InvalidStateTransitionError）
"""

import inspect
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.exceptions import BadRequestError, ConflictError
from app.models.base import Base
from app.models.code import (
    CodeBatch,
    CodeBatchSource,
    CodeBatchStatus,
    CodeGenerationMode,
    CodeItem,
    CodeItemStatus,
    CodeType,
)
from app.models.plan import QuotaRolloutPhase, QuotaRolloutState, TenantQuotaUsage
from app.models.product import SKU, BatchStatus, Brand, Product, ProductionBatch
from app.models.tenant import Tenant
from app.services import code as code_service
from app.services.code import (
    activate_batch,
    bind_code_item,
    create_code_batch,
    freeze_batch,
    list_code_batches,
    list_code_items,
    mark_delivered,
    mark_printing,
    update_batch,
    void_batch,
)
from app.services.code_export import generate_code_csv
from app.services.code_state import InvalidStateTransitionError
from app.services.quota import QUOTA_RECONCILIATION_SOURCE_REVISION, QuotaExceededError

engine = create_async_engine("sqlite+aiosqlite://")
TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class _DatabaseOriginalError(Exception):
    def __init__(self, sqlstate: str | None, *, constraint_name: str | None = None) -> None:
        self.sqlstate = sqlstate
        self.diag = SimpleNamespace(constraint_name=constraint_name)
        super().__init__(sqlstate)


@pytest.mark.parametrize(
    ("sqlstate", "error_code"),
    [
        ("22023", "CODE_BATCH_REQUEST_INVALID"),
        ("23514", "CODE_BATCH_CONTRACT_CONFLICT"),
        ("55P03", "CODE_BATCH_BUSY"),
        ("55000", "CODE_BATCH_IMMUTABLE"),
    ],
)
def test_code_batch_db_error_maps_only_declared_contract_states(sqlstate: str, error_code: str) -> None:
    error = DBAPIError("statement", {}, _DatabaseOriginalError(sqlstate))

    mapped = code_service.map_code_batch_db_error(error)

    assert mapped is not None
    assert mapped.status_code == 409
    assert mapped.error_code == error_code


@pytest.mark.parametrize("sqlstate", ["23505", "99999", None])
def test_code_batch_db_error_does_not_swallow_unknown_database_failures(sqlstate: str | None) -> None:
    error = DBAPIError("statement", {}, _DatabaseOriginalError(sqlstate))

    assert code_service.map_code_batch_db_error(error) is None


def test_code_batch_db_error_maps_only_the_declared_batch_code_unique_constraint() -> None:
    error = DBAPIError(
        "statement",
        {},
        _DatabaseOriginalError("23505", constraint_name="uq_code_batches_tenant_batch_code"),
    )

    mapped = code_service.map_code_batch_db_error(error)

    assert mapped is not None
    assert mapped.status_code == 409
    assert mapped.error_code == "CODE_BATCH_CODE_CONFLICT"


def test_code_batch_db_error_does_not_swallow_an_unrelated_unique_constraint() -> None:
    error = DBAPIError("statement", {}, _DatabaseOriginalError("23505", constraint_name="unrelated_unique"))

    assert code_service.map_code_batch_db_error(error) is None


@pytest.fixture(autouse=True)
async def setup_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def _uuid() -> uuid.UUID:
    from uuid6 import uuid7

    return uuid7()


@pytest.mark.anyio
async def test_code_mutation_audit_failure_is_mandatory(monkeypatch):
    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("app.services.audit.write_audit_log", fail_audit)
    async with TestSession() as db:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await code_service._audit_code_op(
                db,
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                "code_batch_updated",
                f"code_batch:{uuid.uuid4()}",
            )


async def _activate_current_quota_epoch(db: AsyncSession) -> None:
    """Opt an enforcement-specific test into the current rollout epoch."""

    now = datetime.now(UTC)
    db.add(
        QuotaRolloutState(
            id=1,
            source_revision=QUOTA_RECONCILIATION_SOURCE_REVISION,
            phase=QuotaRolloutPhase.active,
            started_at=now,
            drained_at=now,
            drained_by="code-service-test",
            activated_at=now,
            activated_by="code-service-test",
        )
    )
    await db.flush()


async def _create_prerequisites(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """创建租户、品牌、产品、SKU、生产批次并返回所有 ID"""
    tenant_id = _uuid()
    db.add(Tenant(id=tenant_id, name="测试租户", slug=f"test-{tenant_id.hex}"))
    await db.flush()
    db.add(
        TenantQuotaUsage(
            tenant_id=tenant_id,
            reconciled_at=datetime.now(UTC),
            source_revision=QUOTA_RECONCILIATION_SOURCE_REVISION,
            enforcement_ready=True,
        )
    )
    brand = Brand(tenant_id=tenant_id, name="测试品牌")
    db.add(brand)
    await db.flush()

    product = Product(tenant_id=tenant_id, brand_id=brand.id, name="测试产品")
    db.add(product)
    await db.flush()

    sku = SKU(tenant_id=tenant_id, product_id=product.id, code="SKU-001", name="测试SKU")
    db.add(sku)
    await db.flush()

    prod_batch = ProductionBatch(
        tenant_id=tenant_id,
        product_id=product.id,
        sku_id=sku.id,
        batch_code="PB-001",
        production_date=date(2026, 1, 1),
        expiry_date=date(2027, 1, 1),
        origin="测试产地",
        status=BatchStatus.active,
    )
    db.add(prod_batch)
    await db.flush()

    return tenant_id, brand.id, product.id, sku.id, prod_batch.id


async def _prepare_delivered_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> None:
    await generate_code_csv(db, tenant_id, batch_id, actor_id)
    await mark_printing(db, tenant_id, batch_id, actor_id=str(actor_id))
    await mark_delivered(
        db,
        tenant_id,
        batch_id,
        actor_id=str(actor_id),
        reason="test handoff",
        recipient="test recipient",
        confirm="deliver",
    )


class TestCreateCodeBatch:
    """create_code_batch 测试"""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("quantity", "code_type", "generation_mode"),
        [
            (0, CodeType.single, CodeGenerationMode.item_level),
            (10_001, CodeType.single, CodeGenerationMode.item_level),
            (5_001, CodeType.paired, CodeGenerationMode.item_level),
            (1, CodeType.outer, CodeGenerationMode.item_level),
            (1, "unsupported", CodeGenerationMode.item_level),
            (1, CodeType.single, "unsupported"),
        ],
    )
    async def test_create_batch_defends_type_mode_and_physical_item_limit(
        self,
        quantity,
        code_type,
        generation_mode,
    ):
        async with TestSession() as db:
            with pytest.raises(BadRequestError):
                await create_code_batch(
                    db,
                    _uuid(),
                    _uuid(),
                    _uuid(),
                    _uuid(),
                    quantity=quantity,
                    created_by=_uuid(),
                    code_type=code_type,
                    generation_mode=generation_mode,
                )

    @pytest.mark.anyio
    async def test_create_batch_success(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=10,
                created_by=created_by,
            )

            assert result["quantity"] == 10
            assert result["generated_count"] == 10
            assert result["status"] == CodeBatchStatus.completed
            assert result["code_type"] == CodeType.single
            assert result["generation_mode"] == CodeGenerationMode.item_level
            assert result["batch_code"] == "PB-001"
            assert result["product_name"] == "测试产品"
            assert result["sku_name"] == "测试SKU"
            assert result["sku_code"] == "SKU-001"

    @pytest.mark.anyio
    async def test_create_batch_generates_code_items(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=5,
                created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])

            # 验证 CodeItem 数量
            items_result = await db.execute(select(CodeItem).where(CodeItem.code_batch_id == batch_id))
            items = list(items_result.scalars().all())
            assert len(items) == 5

            # 每个 CodeItem 应有 public_id 和正确的初始状态
            for item in items:
                assert item.public_id is not None
                assert len(item.public_id) > 0
                assert item.status == CodeItemStatus.created
                assert item.tenant_id == tenant_id

    @pytest.mark.anyio
    async def test_create_batch_retries_historical_public_id_collision(self, monkeypatch):
        generated_ids = iter(("HISTORICAL-ID", "HISTORICAL-ID", "RETRY-SUCCEEDED"))
        monkeypatch.setattr(code_service, "generate_public_id", lambda: next(generated_ids))

        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=1,
                created_by=_uuid(),
            )
            second = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=1,
                created_by=_uuid(),
            )

            public_ids = set(
                await db.scalars(select(CodeItem.public_id).where(CodeItem.code_batch_id == uuid.UUID(second["id"])))
            )
            assert public_ids == {"RETRY-SUCCEEDED"}

    @pytest.mark.anyio
    async def test_create_batch_reports_stable_public_id_exhaustion(self, monkeypatch):
        monkeypatch.setattr(code_service, "generate_public_id", lambda: "ALWAYS-DUPLICATE")

        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            with pytest.raises(ConflictError) as exc_info:
                await create_code_batch(
                    db,
                    tenant_id,
                    product_id,
                    sku_id,
                    production_batch_id,
                    quantity=2,
                    created_by=_uuid(),
                )

            assert exc_info.value.error_code == "PUBLIC_ID_ALLOCATION_EXHAUSTED"

    @pytest.mark.anyio
    async def test_create_batch_idempotency_replays_without_new_items(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()
            key = "11111111-1111-4111-8111-111111111111"
            first = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=2,
                created_by=created_by,
                idempotency_key=key,
            )
            replay = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=2,
                created_by=created_by,
                idempotency_key=key,
            )

            assert replay == first
            assert await db.scalar(select(func.count()).select_from(CodeBatch)) == 1
            assert await db.scalar(select(func.count()).select_from(CodeItem)) == 2

    @pytest.mark.anyio
    async def test_create_batch_idempotency_rejects_payload_conflict(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()
            key = "11111111-1111-4111-8111-111111111111"
            await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=1,
                created_by=created_by,
                idempotency_key=key,
            )

            with pytest.raises(ConflictError) as exc_info:
                await create_code_batch(
                    db,
                    tenant_id,
                    product_id,
                    sku_id,
                    production_batch_id,
                    quantity=2,
                    created_by=created_by,
                    idempotency_key=key,
                )

            assert exc_info.value.error_code == "CODE_BATCH_IDEMPOTENCY_CONFLICT"
            assert await db.scalar(select(func.count()).select_from(CodeBatch)) == 1

    @pytest.mark.anyio
    async def test_create_imported_batch_stages_authoritative_empty_target(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=3,
                created_by=_uuid(),
                idempotency_key="11111111-1111-4111-8111-111111111111",
                source=CodeBatchSource.imported,
            )

            assert result["source"] == CodeBatchSource.imported
            assert result["status"] == CodeBatchStatus.generating
            assert result["quantity"] == 3
            assert result["expected_item_count"] == 3
            assert result["generated_count"] == 0
            assert await db.scalar(select(func.count()).select_from(CodeItem)) == 0

    @pytest.mark.anyio
    async def test_create_batch_enforces_cumulative_max_codes(self):
        async with TestSession() as db:
            await _activate_current_quota_epoch(db)
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            tenant = await db.get(Tenant, tenant_id)
            assert tenant is not None
            tenant.quota = {"max_codes": 1, "max_codes_per_batch": 100}
            await db.flush()

            with pytest.raises(QuotaExceededError, match="max_codes"):
                await create_code_batch(
                    db,
                    tenant_id,
                    product_id,
                    sku_id,
                    production_batch_id,
                    quantity=2,
                    created_by=_uuid(),
                )

    @pytest.mark.anyio
    async def test_paired_codes_charge_each_generated_code_item(self):
        async with TestSession() as db:
            await _activate_current_quota_epoch(db)
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            tenant = await db.get(Tenant, tenant_id)
            assert tenant is not None
            tenant.quota = {"max_codes": 3, "max_codes_per_batch": 100}
            await db.flush()

            with pytest.raises(QuotaExceededError, match="max_codes"):
                await create_code_batch(
                    db,
                    tenant_id,
                    product_id,
                    sku_id,
                    production_batch_id,
                    quantity=2,
                    created_by=_uuid(),
                    code_type=CodeType.paired,
                )

    @pytest.mark.anyio
    async def test_create_batch_product_not_found(self):
        async with TestSession() as db:
            tenant_id, _, _, sku_id, _ = await _create_prerequisites(db)
            created_by = _uuid()
            fake_product_id = _uuid()

            with pytest.raises(ValueError, match="Product not found"):
                await create_code_batch(
                    db,
                    tenant_id,
                    fake_product_id,
                    sku_id,
                    _uuid(),
                    quantity=1,
                    created_by=created_by,
                )

    @pytest.mark.anyio
    async def test_create_batch_sku_wrong_product(self):
        async with TestSession() as db:
            tenant_id, _, product_id, _, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            # 创建属于另一个产品的 SKU
            other_product = Product(tenant_id=tenant_id, brand_id=_uuid(), name="其他产品")
            db.add(other_product)
            await db.flush()
            wrong_sku = SKU(tenant_id=tenant_id, product_id=other_product.id, code="SKU-WRONG", name="错误SKU")
            db.add(wrong_sku)
            await db.flush()

            with pytest.raises(ValueError, match="SKU does not belong"):
                await create_code_batch(
                    db,
                    tenant_id,
                    product_id,
                    wrong_sku.id,
                    production_batch_id,
                    quantity=1,
                    created_by=created_by,
                )

    @pytest.mark.anyio
    async def test_create_batch_production_batch_mismatch(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, _ = await _create_prerequisites(db)
            created_by = _uuid()

            # 生产批次属于另一个 SKU
            wrong_prod_batch = ProductionBatch(
                tenant_id=tenant_id,
                product_id=product_id,
                sku_id=_uuid(),  # 不同 SKU
                batch_code="PB-WRONG",
                production_date=date(2026, 1, 1),
                expiry_date=date(2027, 1, 1),
                status=BatchStatus.active,
            )
            db.add(wrong_prod_batch)
            await db.flush()

            with pytest.raises(ValueError, match="Production batch does not belong"):
                await create_code_batch(
                    db,
                    tenant_id,
                    product_id,
                    sku_id,
                    wrong_prod_batch.id,
                    quantity=1,
                    created_by=created_by,
                )

    @pytest.mark.anyio
    async def test_create_batch_tenant_isolation(self):
        """不同租户不能看到对方的产品"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            other_tenant_id = _uuid()
            created_by = _uuid()

            with pytest.raises(ValueError, match="Product not found"):
                await create_code_batch(
                    db,
                    other_tenant_id,
                    product_id,
                    sku_id,
                    production_batch_id,
                    quantity=1,
                    created_by=created_by,
                )

    @pytest.mark.anyio
    async def test_create_paired_batch(self):
        """配对码模式：每个数量生成 outer+inner 两个码项"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=3,
                created_by=created_by,
                code_type=CodeType.paired,
            )

            assert result["generated_count"] == 6  # 3 * 2
            batch_id = uuid.UUID(result["id"])

            items_result = await db.execute(select(CodeItem).where(CodeItem.code_batch_id == batch_id))
            items = list(items_result.scalars().all())
            assert len(items) == 6

            outer_items = [i for i in items if i.code_type == CodeType.outer]
            inner_items = [i for i in items if i.code_type == CodeType.inner]
            assert len(outer_items) == 3
            assert len(inner_items) == 3

            # 每对有相同的 pair_id
            pair_ids = set(i.pair_id for i in items)
            assert len(pair_ids) == 3

            artifact = await generate_code_csv(db, tenant_id, batch_id, created_by)
            assert artifact.row_count == 6

    @pytest.mark.anyio
    async def test_create_batch_level_mode(self):
        """批次级模式：数量强制为 1，码类型为 single"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=100,
                created_by=created_by,
                generation_mode=CodeGenerationMode.batch_level,
            )

            assert result["quantity"] == 1
            assert result["generated_count"] == 1
            assert result["code_type"] == CodeType.single
            assert result["generation_mode"] == CodeGenerationMode.batch_level


class TestActivateBatch:
    """activate_batch 测试"""

    def test_activation_lock_order_is_production_batch_then_code_batch_then_items(self):
        source = inspect.getsource(activate_batch)
        production_batch_lock = source.index("select(ProductionBatch)")
        code_batch_lock = source.index("select(CodeBatch)", production_batch_lock)
        code_item_update = source.index("sa_update(CodeItem)", code_batch_lock)

        assert production_batch_lock < code_batch_lock < code_item_update
        assert ".with_for_update()" in source[production_batch_lock:code_batch_lock]
        assert ".with_for_update()" in source[code_batch_lock:code_item_update]

    @pytest.mark.anyio
    async def test_activate_success(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=5,
                created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])

            await _prepare_delivered_batch(db, tenant_id, batch_id, created_by)
            activate_result = await activate_batch(db, tenant_id, batch_id, actor_id=str(created_by))
            assert activate_result.activated == 5

            # 验证所有码项状态已变为 activated
            items_result = await db.execute(select(CodeItem).where(CodeItem.code_batch_id == batch_id))
            items = list(items_result.scalars().all())
            for item in items:
                assert item.status == CodeItemStatus.activated
                assert item.activated_at is not None

            # 批次状态应为 activated
            batch = await db.get(CodeBatch, batch_id)
            assert batch is not None
            assert batch.status == CodeBatchStatus.activated

    @pytest.mark.anyio
    @pytest.mark.parametrize("status", [BatchStatus.recalled, BatchStatus.expired])
    async def test_non_active_production_batch_blocks_create(self, status):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            production_batch = await db.get(ProductionBatch, production_batch_id)
            production_batch.status = status
            if status == BatchStatus.recalled:
                production_batch.recall_reason = "safety recall"
                production_batch.recalled_at = datetime.now(UTC)
                production_batch.recalled_by = str(uuid.uuid4())
            await db.flush()

            with pytest.raises(ConflictError, match="not active"):
                await create_code_batch(
                    db,
                    tenant_id,
                    product_id,
                    sku_id,
                    production_batch_id,
                    quantity=1,
                    created_by=uuid.uuid4(),
                )

    @pytest.mark.anyio
    @pytest.mark.parametrize("status", [BatchStatus.recalled, BatchStatus.expired])
    async def test_non_active_production_batch_blocks_activation(self, status):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            actor_id = uuid.uuid4()
            created = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=1,
                created_by=actor_id,
            )
            await _prepare_delivered_batch(db, tenant_id, uuid.UUID(created["id"]), actor_id)
            production_batch = await db.get(ProductionBatch, production_batch_id)
            production_batch.status = status
            if status == BatchStatus.recalled:
                production_batch.recall_reason = "safety recall"
                production_batch.recalled_at = datetime.now(UTC)
                production_batch.recalled_by = str(actor_id)
            await db.flush()

            with pytest.raises(InvalidStateTransitionError, match="not active"):
                await activate_batch(db, tenant_id, uuid.UUID(created["id"]), actor_id=str(actor_id))

    @pytest.mark.anyio
    async def test_past_expiry_date_blocks_code_batch_creation(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            production_batch = await db.get(ProductionBatch, production_batch_id)
            production_batch.expiry_date = date.today() - timedelta(days=1)
            await db.flush()

            with pytest.raises(ConflictError, match="not active"):
                await create_code_batch(
                    db,
                    tenant_id,
                    product_id,
                    sku_id,
                    production_batch_id,
                    quantity=1,
                    created_by=uuid.uuid4(),
                )

    @pytest.mark.anyio
    async def test_past_expiry_date_blocks_code_batch_activation(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            actor_id = uuid.uuid4()
            created = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=1,
                created_by=actor_id,
            )
            await _prepare_delivered_batch(db, tenant_id, uuid.UUID(created["id"]), actor_id)
            production_batch = await db.get(ProductionBatch, production_batch_id)
            production_batch.expiry_date = date.today() - timedelta(days=1)
            await db.flush()

            with pytest.raises(InvalidStateTransitionError, match="not active"):
                await activate_batch(db, tenant_id, uuid.UUID(created["id"]), actor_id=str(actor_id))

    @pytest.mark.anyio
    async def test_activate_non_completed_batch_rejected(self):
        """只有 completed 状态的批次才能激活"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            # 手动创建一个 pending 状态的批次
            batch = CodeBatch(
                tenant_id=tenant_id,
                product_id=product_id,
                sku_id=sku_id,
                production_batch_id=production_batch_id,
                batch_code="PENDING-001",
                quantity=1,
                created_by=created_by,
                status=CodeBatchStatus.pending,
            )
            db.add(batch)
            await db.flush()

            with pytest.raises(InvalidStateTransitionError, match="Cannot activate"):
                await activate_batch(db, tenant_id, batch.id, actor_id=str(created_by))

    @pytest.mark.anyio
    async def test_activate_already_activated_rejected(self):
        """已激活的批次不能再激活"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=3,
                created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])

            await _prepare_delivered_batch(db, tenant_id, batch_id, created_by)
            await activate_batch(db, tenant_id, batch_id, actor_id=str(created_by))

            with pytest.raises(InvalidStateTransitionError, match="already activated"):
                await activate_batch(db, tenant_id, batch_id, actor_id=str(created_by))

    @pytest.mark.anyio
    async def test_activate_wrong_tenant(self):
        """不同租户无法激活"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=3,
                created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])

            other_tenant_id = _uuid()
            with pytest.raises(ValueError, match="not found"):
                await activate_batch(db, other_tenant_id, batch_id)


class TestOperationalBatchTransitions:
    @pytest.mark.anyio
    async def test_exported_batch_code_is_immutable(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            actor_id = _uuid()
            created = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=1,
                created_by=actor_id,
            )
            batch_id = uuid.UUID(created["id"])
            batch = await db.get(CodeBatch, batch_id)
            batch.status = CodeBatchStatus.exported
            await db.flush()

            with pytest.raises(ConflictError, match="immutable"):
                await update_batch(
                    db,
                    tenant_id,
                    batch_id,
                    actor_id=str(actor_id),
                    batch_code="CHANGED",
                )

    def test_transition_lock_order_is_production_batch_then_code_batch(self):
        source = inspect.getsource(code_service._lock_forward_operational_code_batch)
        locator = source.index("select(CodeBatch.production_batch_id)")
        production_batch_lock = source.index("select(ProductionBatch)", locator)
        code_batch_lock = source.index("select(CodeBatch)", production_batch_lock)

        assert locator < production_batch_lock < code_batch_lock
        assert ".with_for_update()" not in source[locator:production_batch_lock]
        assert ".with_for_update()" in source[production_batch_lock:code_batch_lock]
        assert ".with_for_update()" in source[code_batch_lock:]
        for transition in (mark_printing, mark_delivered):
            transition_source = inspect.getsource(transition)
            assert transition_source.index("_lock_forward_operational_code_batch") < transition_source.index(
                "batch.status ="
            )

    @pytest.mark.anyio
    async def test_changed_parent_chain_fails_closed_after_ordered_locks(self):
        tenant_id = uuid.uuid4()
        batch_id = uuid.uuid4()
        production_batch_id = uuid.uuid4()
        product_id = uuid.uuid4()
        sku_id = uuid.uuid4()
        production_batch = SimpleNamespace(
            id=production_batch_id,
            tenant_id=tenant_id,
            product_id=product_id,
            sku_id=sku_id,
            status=BatchStatus.active,
            expiry_date=date.today() + timedelta(days=1),
        )
        changed_batch = SimpleNamespace(
            id=batch_id,
            tenant_id=tenant_id,
            production_batch_id=uuid.uuid4(),
            product_id=product_id,
            sku_id=sku_id,
        )

        class RacingDB:
            def __init__(self):
                self.scalars = iter((production_batch_id, production_batch, changed_batch))
                self.scalar_calls = 0

            async def scalar(self, _statement):
                self.scalar_calls += 1
                return next(self.scalars)

        db = RacingDB()
        with pytest.raises(ConflictError, match="production chain changed") as exc_info:
            await code_service._lock_forward_operational_code_batch(db, tenant_id, batch_id)

        assert exc_info.value.error_code == "CODE_BATCH_PRODUCTION_CHAIN_CONFLICT"
        assert db.scalar_calls == 3

    @pytest.mark.anyio
    @pytest.mark.parametrize("transition", ["printing", "delivered"])
    @pytest.mark.parametrize("production_batch_state", ["recalled", "past_expiry"])
    async def test_non_active_production_batch_blocks_forward_transition(self, transition, production_batch_state):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            actor_id = _uuid()
            created = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=1,
                created_by=actor_id,
            )
            batch_id = uuid.UUID(created["id"])
            await generate_code_csv(db, tenant_id, batch_id, actor_id)
            if transition == "delivered":
                await mark_printing(db, tenant_id, batch_id, actor_id=str(actor_id))

            production_batch = await db.get(ProductionBatch, production_batch_id)
            if production_batch_state == "recalled":
                production_batch.status = BatchStatus.recalled
                production_batch.recall_reason = "safety recall"
                production_batch.recalled_at = datetime.now(UTC)
                production_batch.recalled_by = str(actor_id)
            else:
                production_batch.expiry_date = date.today() - timedelta(days=1)
            await db.flush()

            with pytest.raises(ConflictError) as exc_info:
                if transition == "printing":
                    await mark_printing(db, tenant_id, batch_id, actor_id=str(actor_id))
                else:
                    await mark_delivered(
                        db,
                        tenant_id,
                        batch_id,
                        actor_id=str(actor_id),
                        reason="test handoff",
                        recipient="test recipient",
                        confirm="deliver",
                    )

            assert exc_info.value.error_code == "PRODUCTION_BATCH_NOT_ACTIVE"
            batch = await db.get(CodeBatch, batch_id)
            assert batch.status == (CodeBatchStatus.exported if transition == "printing" else CodeBatchStatus.printing)


class TestBindCodeItem:
    def test_bind_lock_order_is_production_batch_then_code_batch_then_item(self):
        source = inspect.getsource(bind_code_item)
        locator = source.index("select(CodeItem.code_batch_id, CodeBatch.production_batch_id)")
        production_batch_lock = source.index("select(ProductionBatch)", locator)
        code_batch_lock = source.index("select(CodeBatch)", production_batch_lock)
        code_item_lock = source.index("select(CodeItem)", code_batch_lock)

        assert locator < production_batch_lock < code_batch_lock < code_item_lock
        assert ".with_for_update()" not in source[locator:production_batch_lock]
        assert ".with_for_update()" in source[production_batch_lock:code_batch_lock]
        assert ".with_for_update()" in source[code_batch_lock:code_item_lock]
        assert ".with_for_update()" in source[code_item_lock:]

    @pytest.mark.anyio
    @pytest.mark.parametrize("batch_state", ["recalled", "past_expiry"])
    async def test_non_active_production_batch_blocks_bind(self, batch_state):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            actor_id = uuid.uuid4()
            created = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=1,
                created_by=actor_id,
            )
            batch_id = uuid.UUID(created["id"])
            await _prepare_delivered_batch(db, tenant_id, batch_id, actor_id)
            await activate_batch(db, tenant_id, batch_id, actor_id=str(actor_id))
            item = await db.scalar(select(CodeItem).where(CodeItem.code_batch_id == batch_id))
            production_batch = await db.get(ProductionBatch, production_batch_id)
            if batch_state == "recalled":
                production_batch.status = BatchStatus.recalled
                production_batch.recall_reason = "safety recall"
                production_batch.recalled_at = datetime.now(UTC)
                production_batch.recalled_by = str(actor_id)
            else:
                production_batch.expiry_date = date.today() - timedelta(days=1)
            await db.flush()

            with pytest.raises(ConflictError, match="Production batch is not active") as exc_info:
                await bind_code_item(db, tenant_id, item.id, actor_id=str(actor_id))

            assert exc_info.value.error_code == "PRODUCTION_BATCH_NOT_ACTIVE"
            await db.refresh(item)
            assert item.status == CodeItemStatus.activated

    @pytest.mark.anyio
    async def test_changed_parent_chain_fails_closed_before_item_lock(self):
        tenant_id = uuid.uuid4()
        item_id = uuid.uuid4()
        code_batch_id = uuid.uuid4()
        located_production_batch_id = uuid.uuid4()
        product_id = uuid.uuid4()
        sku_id = uuid.uuid4()
        production_batch = SimpleNamespace(
            id=located_production_batch_id,
            tenant_id=tenant_id,
            product_id=product_id,
            sku_id=sku_id,
            status=BatchStatus.active,
            expiry_date=date.today() + timedelta(days=1),
        )
        changed_batch = SimpleNamespace(
            id=code_batch_id,
            tenant_id=tenant_id,
            production_batch_id=uuid.uuid4(),
            product_id=product_id,
            sku_id=sku_id,
            status=CodeBatchStatus.activated,
        )

        class LocatorResult:
            @staticmethod
            def one_or_none():
                return code_batch_id, located_production_batch_id

        class RacingDB:
            def __init__(self):
                self.scalars = iter((production_batch, changed_batch))
                self.scalar_calls = 0

            def get_bind(self):
                return engine.sync_engine

            async def execute(self, _statement):
                return LocatorResult()

            async def scalar(self, _statement):
                self.scalar_calls += 1
                return next(self.scalars)

        db = RacingDB()
        with pytest.raises(ConflictError, match="production chain changed") as exc_info:
            await bind_code_item(db, tenant_id, item_id, actor_id=str(uuid.uuid4()))

        assert exc_info.value.error_code == "CODE_BIND_CHAIN_CONFLICT"
        assert db.scalar_calls == 2


class TestFreezeBatch:
    """freeze_batch 测试"""

    @pytest.mark.anyio
    async def test_freeze_activated_batch(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=5,
                created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])
            await _prepare_delivered_batch(db, tenant_id, batch_id, created_by)
            await activate_batch(db, tenant_id, batch_id, actor_id=str(created_by))

            freeze_result = await freeze_batch(
                db,
                tenant_id,
                batch_id,
                actor_id=str(created_by),
                reason="batch investigation",
            )
            assert freeze_result.frozen == 5

            # 验证码项状态
            items_result = await db.execute(select(CodeItem).where(CodeItem.code_batch_id == batch_id))
            for item in items_result.scalars().all():
                assert item.status == CodeItemStatus.frozen
                assert item.frozen_from_status == CodeItemStatus.activated.value
                assert item.frozen_by == str(created_by)
                assert item.freeze_reason == "batch investigation"
                assert item.freeze_provenance_version == 1

    @pytest.mark.anyio
    async def test_freeze_empty_batch_returns_conflict(self):
        """没有可冻结码项时与 PostgreSQL 权威接口一致返回冲突。"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=3,
                created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])
            # 批次处于 completed 但码项都是 created 状态，不在 activated/bound

            with pytest.raises(ConflictError) as raised:
                await freeze_batch(
                    db,
                    tenant_id,
                    batch_id,
                    actor_id=str(created_by),
                    reason="batch investigation",
                )
            assert raised.value.error_code == "CODE_LIFECYCLE_CONFLICT"


class TestVoidBatch:
    """void_batch 测试"""

    @pytest.mark.anyio
    async def test_void_batch_success(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=5,
                created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])

            void_result = await void_batch(
                db,
                tenant_id,
                batch_id,
                actor_id=str(created_by),
                reason="permanent batch incident",
            )
            assert void_result.voided == 5

            # 验证码项状态
            items_result = await db.execute(select(CodeItem).where(CodeItem.code_batch_id == batch_id))
            for item in items_result.scalars().all():
                assert item.status == CodeItemStatus.revoked
                assert item.revoked_at is not None

    @pytest.mark.anyio
    async def test_void_already_revoked_items(self):
        """已 revoked 的码项不计入作废数量"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=3,
                created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])

            # 先作废一次
            first_void = await void_batch(
                db,
                tenant_id,
                batch_id,
                actor_id=str(created_by),
                reason="permanent batch incident",
            )
            assert first_void.voided == 3

            # 再作废一次（所有码已是 revoked）必须保持终态并返回稳定冲突。
            with pytest.raises(ConflictError) as raised:
                await void_batch(
                    db,
                    tenant_id,
                    batch_id,
                    actor_id=str(created_by),
                    reason="second attempt",
                )
            assert raised.value.error_code == "CODE_LIFECYCLE_CONFLICT"


class TestListCodeBatches:
    """list_code_batches 测试"""

    @pytest.mark.anyio
    async def test_list_returns_created_batches(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=5,
                created_by=created_by,
            )
            await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=10,
                created_by=created_by,
            )

            batches, total = await list_code_batches(db, tenant_id)
            assert total == 2
            assert len(batches) == 2

    @pytest.mark.anyio
    async def test_list_filter_by_status(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=5,
                created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])
            await _prepare_delivered_batch(db, tenant_id, batch_id, created_by)
            await activate_batch(db, tenant_id, batch_id, actor_id=str(created_by))

            # 新建一个不激活的
            await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=3,
                created_by=created_by,
            )

            activated_batches, total = await list_code_batches(db, tenant_id, status=CodeBatchStatus.activated)
            assert total == 1
            assert activated_batches[0].status == CodeBatchStatus.activated

    @pytest.mark.anyio
    async def test_list_tenant_isolation(self):
        """不同租户看不到对方的批次"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=5,
                created_by=created_by,
            )

            other_tenant_id = _uuid()
            batches, total = await list_code_batches(db, other_tenant_id)
            assert total == 0
            assert len(batches) == 0


class TestListCodeItems:
    """list_code_items 测试"""

    @pytest.mark.anyio
    async def test_list_items_by_batch(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=5,
                created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])

            items, total = await list_code_items(db, tenant_id, code_batch_id=batch_id)
            assert total == 5
            assert len(items) == 5

    @pytest.mark.anyio
    async def test_list_items_filter_by_status(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db,
                tenant_id,
                product_id,
                sku_id,
                production_batch_id,
                quantity=5,
                created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])
            await _prepare_delivered_batch(db, tenant_id, batch_id, created_by)
            await activate_batch(db, tenant_id, batch_id, actor_id=str(created_by))

            activated_items, total = await list_code_items(db, tenant_id, status=CodeItemStatus.activated)
            assert total == 5

            created_items, created_total = await list_code_items(db, tenant_id, status=CodeItemStatus.created)
            assert created_total == 0
