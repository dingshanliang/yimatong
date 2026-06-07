"""码管理服务层测试

使用 SQLite 内存数据库 + AsyncSession 测试 app/services/code.py 中的核心函数：
- create_code_batch: 正常创建、产品不存在、SKU 不匹配
- activate_batch: 正常激活、状态校验
- freeze_batch / void_batch: 正常操作
- 状态转换校验（非法转换抛 InvalidStateTransitionError）
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.code import (
    CodeBatch,
    CodeBatchStatus,
    CodeGenerationMode,
    CodeItem,
    CodeItemStatus,
    CodeType,
)
from app.models.product import Brand, BatchStatus, Product, SKU, ProductionBatch
from app.services.code import (
    activate_batch,
    create_code_batch,
    freeze_batch,
    list_code_batches,
    list_code_items,
    void_batch,
)
from app.services.code_state import InvalidStateTransitionError

engine = create_async_engine("sqlite+aiosqlite://")
TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


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


async def _create_prerequisites(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """创建租户、品牌、产品、SKU、生产批次并返回所有 ID"""
    tenant_id = _uuid()
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


class TestCreateCodeBatch:
    """create_code_batch 测试"""

    @pytest.mark.anyio
    async def test_create_batch_success(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=10, created_by=created_by,
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
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=5, created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])

            # 验证 CodeItem 数量
            items_result = await db.execute(
                select(CodeItem).where(CodeItem.code_batch_id == batch_id)
            )
            items = list(items_result.scalars().all())
            assert len(items) == 5

            # 每个 CodeItem 应有 public_id 和正确的初始状态
            for item in items:
                assert item.public_id is not None
                assert len(item.public_id) > 0
                assert item.status == CodeItemStatus.created
                assert item.tenant_id == tenant_id

    @pytest.mark.anyio
    async def test_create_batch_product_not_found(self):
        async with TestSession() as db:
            tenant_id, _, _, sku_id, _ = await _create_prerequisites(db)
            created_by = _uuid()
            fake_product_id = _uuid()

            with pytest.raises(ValueError, match="Product not found"):
                await create_code_batch(
                    db, tenant_id, fake_product_id, sku_id, _uuid(),
                    quantity=1, created_by=created_by,
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
                    db, tenant_id, product_id, wrong_sku.id, production_batch_id,
                    quantity=1, created_by=created_by,
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
                    db, tenant_id, product_id, sku_id, wrong_prod_batch.id,
                    quantity=1, created_by=created_by,
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
                    db, other_tenant_id, product_id, sku_id, production_batch_id,
                    quantity=1, created_by=created_by,
                )

    @pytest.mark.anyio
    async def test_create_paired_batch(self):
        """配对码模式：每个数量生成 outer+inner 两个码项"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=3, created_by=created_by,
                code_type=CodeType.paired,
            )

            assert result["generated_count"] == 6  # 3 * 2
            batch_id = uuid.UUID(result["id"])

            items_result = await db.execute(
                select(CodeItem).where(CodeItem.code_batch_id == batch_id)
            )
            items = list(items_result.scalars().all())
            assert len(items) == 6

            outer_items = [i for i in items if i.code_type == CodeType.outer]
            inner_items = [i for i in items if i.code_type == CodeType.inner]
            assert len(outer_items) == 3
            assert len(inner_items) == 3

            # 每对有相同的 pair_id
            pair_ids = set(i.pair_id for i in items)
            assert len(pair_ids) == 3

    @pytest.mark.anyio
    async def test_create_batch_level_mode(self):
        """批次级模式：数量强制为 1，码类型为 single"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=100, created_by=created_by,
                generation_mode=CodeGenerationMode.batch_level,
            )

            assert result["quantity"] == 1
            assert result["generated_count"] == 1
            assert result["code_type"] == CodeType.single
            assert result["generation_mode"] == CodeGenerationMode.batch_level


class TestActivateBatch:
    """activate_batch 测试"""

    @pytest.mark.anyio
    async def test_activate_success(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=5, created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])

            activate_result = await activate_batch(db, tenant_id, batch_id)
            assert activate_result.activated == 5

            # 验证所有码项状态已变为 activated
            items_result = await db.execute(
                select(CodeItem).where(CodeItem.code_batch_id == batch_id)
            )
            items = list(items_result.scalars().all())
            for item in items:
                assert item.status == CodeItemStatus.activated
                assert item.activated_at is not None

            # 批次状态应为 activated
            batch = await db.get(CodeBatch, batch_id)
            assert batch is not None
            assert batch.status == CodeBatchStatus.activated

    @pytest.mark.anyio
    async def test_activate_non_completed_batch_rejected(self):
        """只有 completed 状态的批次才能激活"""
        async with TestSession() as db:
            tenant_id = _uuid()
            created_by = _uuid()

            # 手动创建一个 pending 状态的批次
            batch = CodeBatch(
                tenant_id=tenant_id,
                product_id=_uuid(),
                sku_id=_uuid(),
                batch_code="PENDING-001",
                quantity=1,
                created_by=created_by,
                status=CodeBatchStatus.pending,
            )
            db.add(batch)
            await db.flush()

            with pytest.raises(InvalidStateTransitionError, match="Cannot activate"):
                await activate_batch(db, tenant_id, batch.id)

    @pytest.mark.anyio
    async def test_activate_already_activated_rejected(self):
        """已激活的批次不能再激活"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=3, created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])

            await activate_batch(db, tenant_id, batch_id)

            with pytest.raises(InvalidStateTransitionError, match="already activated"):
                await activate_batch(db, tenant_id, batch_id)

    @pytest.mark.anyio
    async def test_activate_wrong_tenant(self):
        """不同租户无法激活"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=3, created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])

            other_tenant_id = _uuid()
            with pytest.raises(ValueError, match="not found"):
                await activate_batch(db, other_tenant_id, batch_id)


class TestFreezeBatch:
    """freeze_batch 测试"""

    @pytest.mark.anyio
    async def test_freeze_activated_batch(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=5, created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])
            await activate_batch(db, tenant_id, batch_id)

            freeze_result = await freeze_batch(db, tenant_id, batch_id)
            assert freeze_result.frozen == 5

            # 验证码项状态
            items_result = await db.execute(
                select(CodeItem).where(CodeItem.code_batch_id == batch_id)
            )
            for item in items_result.scalars().all():
                assert item.status == CodeItemStatus.frozen

    @pytest.mark.anyio
    async def test_freeze_empty_batch_returns_zero(self):
        """没有可冻结码项时返回 0"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=3, created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])
            # 批次处于 completed 但码项都是 created 状态，不在 activated/bound

            freeze_result = await freeze_batch(db, tenant_id, batch_id)
            assert freeze_result.frozen == 0


class TestVoidBatch:
    """void_batch 测试"""

    @pytest.mark.anyio
    async def test_void_batch_success(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            result = await create_code_batch(
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=5, created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])

            void_result = await void_batch(db, tenant_id, batch_id)
            assert void_result.voided == 5

            # 验证码项状态
            items_result = await db.execute(
                select(CodeItem).where(CodeItem.code_batch_id == batch_id)
            )
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
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=3, created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])

            # 先作废一次
            first_void = await void_batch(db, tenant_id, batch_id)
            assert first_void.voided == 3

            # 再作废一次（所有码已是 revoked）
            second_void = await void_batch(db, tenant_id, batch_id)
            assert second_void.voided == 0


class TestListCodeBatches:
    """list_code_batches 测试"""

    @pytest.mark.anyio
    async def test_list_returns_created_batches(self):
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            await create_code_batch(
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=5, created_by=created_by,
            )
            await create_code_batch(
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=10, created_by=created_by,
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
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=5, created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])
            await activate_batch(db, tenant_id, batch_id)

            # 新建一个不激活的
            await create_code_batch(
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=3, created_by=created_by,
            )

            activated_batches, total = await list_code_batches(
                db, tenant_id, status=CodeBatchStatus.activated
            )
            assert total == 1
            assert activated_batches[0].status == CodeBatchStatus.activated

    @pytest.mark.anyio
    async def test_list_tenant_isolation(self):
        """不同租户看不到对方的批次"""
        async with TestSession() as db:
            tenant_id, _, product_id, sku_id, production_batch_id = await _create_prerequisites(db)
            created_by = _uuid()

            await create_code_batch(
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=5, created_by=created_by,
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
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=5, created_by=created_by,
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
                db, tenant_id, product_id, sku_id, production_batch_id,
                quantity=5, created_by=created_by,
            )
            batch_id = uuid.UUID(result["id"])
            await activate_batch(db, tenant_id, batch_id)

            activated_items, total = await list_code_items(
                db, tenant_id, status=CodeItemStatus.activated
            )
            assert total == 5

            created_items, created_total = await list_code_items(
                db, tenant_id, status=CodeItemStatus.created
            )
            assert created_total == 0
