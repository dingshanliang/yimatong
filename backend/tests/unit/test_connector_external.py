"""外部权益连接器 L2-L4 单元测试

测试范围:
- 外部权益发放记录模型 (BenefitDelivery)
- 同步库存 (sync_stock)
- 发放状态回传 (deliver_benefit + callback)
- 失败重试 (retry_delivery)
- 熔断器集成
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.connector import Connector, CouponPool
from app.services.circuit_breaker import CircuitBreaker
from app.services.external_benefit import (
    DeliveryStatus,
    ExternalBenefitError,
    deliver_benefit,
    get_pending_retries,
    retry_delivery,
    sync_stock,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tenant_id():
    return uuid7()


@pytest.fixture
def connector(tenant_id):
    return Connector(
        id=uuid7(),
        tenant_id=tenant_id,
        name="测试优惠券连接器",
        connector_type="coupon",
        config={"api_url": "https://ext.example.com/api", "api_key": "sk-test"},
        enabled=True,
    )


@pytest.fixture
def redpacket_connector(tenant_id):
    return Connector(
        id=uuid7(),
        tenant_id=tenant_id,
        name="测试红包连接器",
        connector_type="redpacket",
        config={"api_url": "https://ext.example.com/redpacket", "api_key": "sk-test"},
        enabled=True,
    )


@pytest.fixture
def points_connector(tenant_id):
    return Connector(
        id=uuid7(),
        tenant_id=tenant_id,
        name="测试积分连接器",
        connector_type="points",
        config={"api_url": "https://ext.example.com/points", "api_key": "sk-test"},
        enabled=True,
    )


@pytest.fixture
def mock_db():
    db = AsyncMock()
    # flush and refresh are awaited in the service
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    # db.add is synchronous in SQLAlchemy, override the AsyncMock
    db.add = MagicMock()
    return db


@pytest.fixture
def circuit_breaker():
    return CircuitBreaker(threshold=3, reset_timeout=10)


def uuid7():
    from uuid6 import uuid7
    return uuid7()


# ---------------------------------------------------------------------------
# 1. 同步库存 sync_stock
# ---------------------------------------------------------------------------

class TestSyncStock:
    """L2: 从外部系统同步库存"""

    @pytest.mark.asyncio
    async def test_sync_coupon_stock(self, mock_db, connector, tenant_id):
        """同步优惠券库存应更新 Connector 的 config.stock"""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = connector
        # 第二次 execute 返回更新后的 connector
        mock_db.execute.return_value = mock_result
        mock_db.refresh.return_value = connector

        # 模拟外部 HTTP 返回库存
        with patch("app.services.external_benefit._fetch_external_stock", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = {"stock": 500}

            result = await sync_stock(mock_db, tenant_id, connector.id)

        assert result["stock"] == 500
        assert result["connector_id"] == str(connector.id)
        mock_fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_stock_connector_not_found(self, mock_db, tenant_id):
        """连接器不存在应抛出异常"""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ExternalBenefitError, match="not found"):
            await sync_stock(mock_db, tenant_id, uuid7())

    @pytest.mark.asyncio
    async def test_sync_stock_connector_disabled(self, mock_db, connector, tenant_id):
        """禁用的连接器不应同步"""
        connector.enabled = False
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = connector
        mock_db.execute.return_value = mock_result

        with pytest.raises(ExternalBenefitError, match="disabled"):
            await sync_stock(mock_db, tenant_id, connector.id)

    @pytest.mark.asyncio
    async def test_sync_stock_external_failure_with_circuit_breaker(
        self, mock_db, connector, tenant_id, circuit_breaker
    ):
        """外部系统失败应记录失败并触发熔断"""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = connector
        mock_db.execute.return_value = mock_result

        with patch("app.services.external_benefit._fetch_external_stock", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = Exception("Connection timeout")

            with patch("app.services.external_benefit._get_circuit_breaker", return_value=circuit_breaker):
                with pytest.raises(ExternalBenefitError, match="stock sync failed"):
                    await sync_stock(mock_db, tenant_id, connector.id)

        assert circuit_breaker._state.failure_count == 1
        assert circuit_breaker.is_available()  # 还没到阈值

    @pytest.mark.asyncio
    async def test_sync_redpacket_stock(self, mock_db, redpacket_connector, tenant_id):
        """红包类型连接器也应支持库存同步"""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = redpacket_connector
        mock_db.execute.return_value = mock_result

        with patch("app.services.external_benefit._fetch_external_stock", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = {"stock": 1000, "budget": 50000}

            result = await sync_stock(mock_db, tenant_id, redpacket_connector.id)

        assert result["stock"] == 1000
        assert result["budget"] == 50000

    @pytest.mark.asyncio
    async def test_sync_points_stock(self, mock_db, points_connector, tenant_id):
        """积分类型连接器应支持积分池同步"""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = points_connector
        mock_db.execute.return_value = mock_result

        with patch("app.services.external_benefit._fetch_external_stock", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = {"stock": 99999, "points_pool": "default"}

            result = await sync_stock(mock_db, tenant_id, points_connector.id)

        assert result["stock"] == 99999


# ---------------------------------------------------------------------------
# 2. 发放状态回传 deliver_benefit
# ---------------------------------------------------------------------------

class TestDeliverBenefit:
    """L3: 向外部系统发放权益并回传状态"""

    @pytest.mark.asyncio
    async def test_deliver_coupon_success(self, mock_db, connector, tenant_id):
        """成功发放优惠券"""
        consumer_id = "consumer-001"

        with patch("app.services.external_benefit._call_external_deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.return_value = {"status": "success", "code": "COUPON-12345"}

            result = await deliver_benefit(
                mock_db, tenant_id, connector, consumer_id,
                benefit_type="coupon",
                benefit_config={"pool_id": "pool-001"},
            )

        assert result["status"] == DeliveryStatus.SUCCESS
        assert result["external_code"] == "COUPON-12345"
        mock_db.add.assert_called_once()
        mock_db.flush.assert_awaited()

    @pytest.mark.asyncio
    async def test_deliver_redpacket_success(self, mock_db, redpacket_connector, tenant_id):
        """成功发放红包"""
        consumer_id = "consumer-002"

        with patch("app.services.external_benefit._call_external_deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.return_value = {"status": "success", "amount": 100}

            result = await deliver_benefit(
                mock_db, tenant_id, redpacket_connector, consumer_id,
                benefit_type="redpacket",
                benefit_config={"rule_id": "rule-001"},
            )

        assert result["status"] == DeliveryStatus.SUCCESS
        assert result["external_data"]["amount"] == 100

    @pytest.mark.asyncio
    async def test_deliver_points_success(self, mock_db, points_connector, tenant_id):
        """成功发放积分"""
        consumer_id = "consumer-003"

        with patch("app.services.external_benefit._call_external_deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.return_value = {"status": "success", "points": 50}

            result = await deliver_benefit(
                mock_db, tenant_id, points_connector, consumer_id,
                benefit_type="points",
                benefit_config={"points": 50},
            )

        assert result["status"] == DeliveryStatus.SUCCESS
        assert result["external_data"]["points"] == 50

    @pytest.mark.asyncio
    async def test_deliver_external_failure_records_retry(self, mock_db, connector, tenant_id):
        """外部系统失败应记录为 pending 状态，等待重试"""
        consumer_id = "consumer-004"

        with patch("app.services.external_benefit._call_external_deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.side_effect = Exception("External API error")

            with patch("app.services.external_benefit._get_circuit_breaker", return_value=CircuitBreaker(threshold=3)):
                result = await deliver_benefit(
                    mock_db, tenant_id, connector, consumer_id,
                    benefit_type="coupon",
                    benefit_config={"pool_id": "pool-001"},
                )

        assert result["status"] == DeliveryStatus.PENDING
        assert result["retry_count"] == 0
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_deliver_circuit_breaker_open(self, mock_db, connector, tenant_id):
        """熔断器开启时应直接标记为 pending，不调用外部"""
        cb = CircuitBreaker(threshold=1, reset_timeout=60)
        cb.record_failure()  # 触发熔断

        consumer_id = "consumer-005"

        with patch("app.services.external_benefit._get_circuit_breaker", return_value=cb):
            with patch("app.services.external_benefit._call_external_deliver", new_callable=AsyncMock) as mock_deliver:
                result = await deliver_benefit(
                    mock_db, tenant_id, connector, consumer_id,
                    benefit_type="coupon",
                    benefit_config={},
                )

        # 熔断器打开，不调用外部API
        mock_deliver.assert_not_awaited()
        assert result["status"] == DeliveryStatus.PENDING


# ---------------------------------------------------------------------------
# 3. 失败重试 retry_delivery
# ---------------------------------------------------------------------------

class TestRetryDelivery:
    """L4: 失败重试机制"""

    @pytest.mark.asyncio
    async def test_retry_success(self, mock_db, tenant_id):
        """重试成功应更新状态为 success"""
        delivery_id = uuid7()

        # mock delivery record
        mock_delivery = MagicMock()
        mock_delivery.id = delivery_id
        mock_delivery.tenant_id = tenant_id
        mock_delivery.status = DeliveryStatus.PENDING
        mock_delivery.retry_count = 1
        mock_delivery.connector_id = uuid7()
        mock_delivery.consumer_id = "consumer-001"
        mock_delivery.benefit_type = "coupon"
        mock_delivery.benefit_config = {"pool_id": "p1"}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_delivery
        mock_db.execute.return_value = mock_result

        with patch("app.services.external_benefit._call_external_deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.return_value = {"status": "success", "code": "COUPON-RETRY"}

            with patch("app.services.external_benefit._get_circuit_breaker", return_value=CircuitBreaker()):
                result = await retry_delivery(mock_db, delivery_id, tenant_id)

        assert result["status"] == DeliveryStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_retry_still_fails_increments_counter(self, mock_db, tenant_id):
        """重试仍然失败应增加 retry_count"""
        delivery_id = uuid7()

        mock_delivery = MagicMock()
        mock_delivery.id = delivery_id
        mock_delivery.tenant_id = tenant_id
        mock_delivery.status = DeliveryStatus.PENDING
        mock_delivery.retry_count = 2
        mock_delivery.max_retries = 5
        mock_delivery.connector_id = uuid7()
        mock_delivery.consumer_id = "consumer-001"
        mock_delivery.benefit_type = "coupon"
        mock_delivery.benefit_config = {}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_delivery
        mock_db.execute.return_value = mock_result

        with patch("app.services.external_benefit._call_external_deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.side_effect = Exception("Still failing")

            with patch("app.services.external_benefit._get_circuit_breaker", return_value=CircuitBreaker()):
                result = await retry_delivery(mock_db, delivery_id, tenant_id)

        assert result["status"] == DeliveryStatus.PENDING
        assert mock_delivery.retry_count == 3

    @pytest.mark.asyncio
    async def test_retry_max_exhausted_marks_failed(self, mock_db, tenant_id):
        """重试次数用尽应标记为 failed"""
        delivery_id = uuid7()

        mock_delivery = MagicMock()
        mock_delivery.id = delivery_id
        mock_delivery.tenant_id = tenant_id
        mock_delivery.status = DeliveryStatus.PENDING
        mock_delivery.retry_count = 4
        mock_delivery.max_retries = 5
        mock_delivery.connector_id = uuid7()
        mock_delivery.consumer_id = "consumer-001"
        mock_delivery.benefit_type = "coupon"
        mock_delivery.benefit_config = {}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_delivery
        mock_db.execute.return_value = mock_result

        with patch("app.services.external_benefit._call_external_deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.side_effect = Exception("Final failure")

            with patch("app.services.external_benefit._get_circuit_breaker", return_value=CircuitBreaker()):
                result = await retry_delivery(mock_db, delivery_id, tenant_id)

        assert result["status"] == DeliveryStatus.FAILED

    @pytest.mark.asyncio
    async def test_retry_already_success_is_noop(self, mock_db, tenant_id):
        """已成功的记录不应重试"""
        delivery_id = uuid7()

        mock_delivery = MagicMock()
        mock_delivery.id = delivery_id
        mock_delivery.tenant_id = tenant_id
        mock_delivery.status = DeliveryStatus.SUCCESS
        mock_delivery.retry_count = 0

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_delivery
        mock_db.execute.return_value = mock_result

        result = await retry_delivery(mock_db, delivery_id, tenant_id)

        assert result["status"] == DeliveryStatus.SUCCESS
        mock_db.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retry_not_found(self, mock_db, tenant_id):
        """delivery 记录不存在应抛异常"""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ExternalBenefitError, match="not found"):
            await retry_delivery(mock_db, uuid7(), tenant_id)


# ---------------------------------------------------------------------------
# 4. 获取待重试记录 get_pending_retries
# ---------------------------------------------------------------------------

class TestGetPendingRetries:
    """获取待重试的发放记录"""

    @pytest.mark.asyncio
    async def test_get_pending_deliveries(self, mock_db, tenant_id):
        """应返回所有 pending 且未超过最大重试次数的记录"""
        mock_result = MagicMock()
        mock_delivery = MagicMock()
        mock_delivery.status = DeliveryStatus.PENDING
        mock_delivery.retry_count = 2
        mock_result.scalars.return_value.all.return_value = [mock_delivery]
        mock_db.execute.return_value = mock_result

        result = await get_pending_retries(mock_db, tenant_id)

        assert len(result) == 1
        assert result[0].status == DeliveryStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_pending_empty(self, mock_db, tenant_id):
        """没有待重试记录应返回空列表"""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        result = await get_pending_retries(mock_db, tenant_id)

        assert len(result) == 0


# ---------------------------------------------------------------------------
# 5. BenefitDelivery model
# ---------------------------------------------------------------------------

class TestBenefitDeliveryModel:
    """BenefitDelivery ORM 模型字段验证"""

    def test_model_fields(self):
        """验证模型包含所有必要字段"""
        from app.models.connector import BenefitDelivery

        assert hasattr(BenefitDelivery, "id")
        assert hasattr(BenefitDelivery, "tenant_id")
        assert hasattr(BenefitDelivery, "connector_id")
        assert hasattr(BenefitDelivery, "consumer_id")
        assert hasattr(BenefitDelivery, "benefit_type")
        assert hasattr(BenefitDelivery, "benefit_config")
        assert hasattr(BenefitDelivery, "status")
        assert hasattr(BenefitDelivery, "retry_count")
        assert hasattr(BenefitDelivery, "max_retries")
        assert hasattr(BenefitDelivery, "external_data")
        assert hasattr(BenefitDelivery, "next_retry_at")
        assert hasattr(BenefitDelivery, "created_at")
        assert hasattr(BenefitDelivery, "updated_at")

    def test_delivery_status_values(self):
        """验证状态枚举值"""
        assert DeliveryStatus.PENDING == "pending"
        assert DeliveryStatus.SUCCESS == "success"
        assert DeliveryStatus.FAILED == "failed"


# ---------------------------------------------------------------------------
# 6. 回调 callback
# ---------------------------------------------------------------------------

class TestDeliveryCallback:
    """外部系统发放结果回调"""

    @pytest.mark.asyncio
    async def test_callback_updates_status(self, mock_db, tenant_id):
        """回调应更新发放状态"""
        delivery_id = uuid7()

        mock_delivery = MagicMock()
        mock_delivery.id = delivery_id
        mock_delivery.tenant_id = tenant_id
        mock_delivery.status = DeliveryStatus.PENDING

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_delivery
        mock_db.execute.return_value = mock_result

        from app.services.external_benefit import delivery_callback

        result = await delivery_callback(
            mock_db, tenant_id, delivery_id,
            status="success",
            external_data={"code": "COUPON-CB-001"},
        )

        assert result["status"] == DeliveryStatus.SUCCESS
        assert mock_delivery.status == DeliveryStatus.SUCCESS
        assert mock_delivery.external_data == {"code": "COUPON-CB-001"}

    @pytest.mark.asyncio
    async def test_callback_not_found(self, mock_db, tenant_id):
        """回调不存在的记录应报错"""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        from app.services.external_benefit import delivery_callback

        with pytest.raises(ExternalBenefitError, match="not found"):
            await delivery_callback(
                mock_db, tenant_id, uuid7(),
                status="success",
                external_data={},
            )
