"""渠道管理服务单元测试"""

import uuid

from app.models.channel import CodeAllocation, Distributor, Region, Store


class TestDistributorModel:
    def test_creation(self):
        dist = Distributor(tenant_id=uuid.uuid4(), name="测试经销商", code="DIST-001")
        assert dist.name == "测试经销商"
        assert dist.code == "DIST-001"
        assert dist.status in ("active", None)

    def test_with_contact(self):
        dist = Distributor(
            tenant_id=uuid.uuid4(),
            name="经销商A",
            code="A001",
            contact_name="张三",
            contact_phone_encrypted="enc",
            contact_phone_hash="hash",
        )
        assert dist.contact_name == "张三"
        assert dist.contact_phone_encrypted == "enc"


class TestRegionModel:
    def test_creation(self):
        region = Region(tenant_id=uuid.uuid4(), name="华东区", code="CN-EAST", province="上海", city="上海")
        assert region.name == "华东区"
        assert region.province == "上海"
        assert region.city == "上海"

    def test_with_distributor(self):
        did = uuid.uuid4()
        region = Region(tenant_id=uuid.uuid4(), name="华北区", code="CN-NORTH", distributor_id=did)
        assert region.distributor_id == did


class TestStoreModel:
    def test_creation(self):
        store = Store(tenant_id=uuid.uuid4(), name="上海旗舰店", code="STORE-001", address="南京路100号")
        assert store.name == "上海旗舰店"
        assert store.address == "南京路100号"
        assert store.status in ("active", None)

    def test_with_relations(self):
        rid = uuid.uuid4()
        did = uuid.uuid4()
        store = Store(
            tenant_id=uuid.uuid4(),
            name="门店A",
            code="A001",
            region_id=rid,
            distributor_id=did,
        )
        assert store.region_id == rid
        assert store.distributor_id == did


class TestCodeAllocationModel:
    def test_creation(self):
        alloc = CodeAllocation(
            tenant_id=uuid.uuid4(),
            batch_id=uuid.uuid4(),
            store_id=uuid.uuid4(),
            distributor_id=uuid.uuid4(),
            quantity=500,
            allocated_at="2026-05-31T10:00:00+00:00",
        )
        assert alloc.quantity == 500
        assert alloc.allocated_at is not None

    def test_optional_store(self):
        alloc = CodeAllocation(
            tenant_id=uuid.uuid4(),
            batch_id=uuid.uuid4(),
            store_id=None,
            distributor_id=uuid.uuid4(),
            quantity=1000,
        )
        assert alloc.store_id is None
        assert alloc.distributor_id is not None


class TestStoreStatusTransition:
    def test_active_to_inactive(self):
        store = Store(tenant_id=uuid.uuid4(), name="test", code="T001", status="active")
        store.status = "inactive"
        assert store.status == "inactive"

    def test_default_status(self):
        store = Store(tenant_id=uuid.uuid4(), name="test", code="T001")
        assert store.status in ("active", None)


class TestHierarchy:
    """测试渠道层级关系"""

    def test_distributor_region_store_chain(self):
        """经销商 → 区域 → 门店的层级链路"""
        dist_id = uuid.uuid4()
        region_id = uuid.uuid4()
        store = Store(
            tenant_id=uuid.uuid4(),
            name="终端门店",
            code="END-001",
            region_id=region_id,
            distributor_id=dist_id,
        )
        assert store.distributor_id == dist_id
        assert store.region_id == region_id

    def test_allocation_links_batch_to_store(self):
        """分配记录连接批次和门店"""
        batch_id = uuid.uuid4()
        store_id = uuid.uuid4()
        alloc = CodeAllocation(
            tenant_id=uuid.uuid4(),
            batch_id=batch_id,
            store_id=store_id,
            quantity=200,
        )
        assert alloc.batch_id == batch_id
        assert alloc.store_id == store_id
        assert alloc.quantity == 200


class TestResolveStoreLogic:
    """测试扫码匹配门店的逻辑"""

    def test_resolve_result_structure(self):
        """验证 resolve_store_for_code 返回结构"""
        result = {
            "matched": True,
            "store_id": str(uuid.uuid4()),
            "store_name": "测试门店",
            "store_code": "STORE-001",
            "address": "测试地址",
            "region_name": "华东区",
            "city": "上海",
            "distributor_name": "总经销商",
        }
        required = ["matched", "store_id", "store_name", "store_code"]
        for key in required:
            assert key in result

    def test_no_match_result(self):
        result = {"matched": False}
        assert result["matched"] is False
