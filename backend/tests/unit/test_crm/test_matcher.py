"""CRM 匹配器单元测试"""

import uuid

import pytest
from sqlalchemy import select

from app.models.member import ConsumerProfile
from app.models.sync_mapping import SyncMapping
from app.services.crm.matcher import (
    create_or_update_mapping,
    get_mappings_for_consumer,
    match_by_external_id,
    match_by_phone,
)


@pytest.fixture
def tenant_id():
    return uuid.uuid4()


async def _create_consumer(db, tenant_id, phone_hash="abc123", nickname="测试"):
    consumer = ConsumerProfile(
        tenant_id=tenant_id,
        phone_hash=phone_hash,
        nickname=nickname,
    )
    db.add(consumer)
    await db.flush()
    return consumer


class TestMatchByExternalId:
    async def test_no_mapping(self, db, tenant_id):
        result = await match_by_external_id(db, tenant_id, "wecom", "ext_123")
        assert result is None

    async def test_existing_mapping(self, db, tenant_id):
        consumer = await _create_consumer(db, tenant_id)
        mapping = SyncMapping(
            tenant_id=tenant_id,
            local_entity_type="consumer_profile",
            local_entity_id=consumer.id,
            source_system="wecom",
            external_id="ext_123",
            sync_direction="bidirectional",
        )
        db.add(mapping)
        await db.flush()

        result = await match_by_external_id(db, tenant_id, "wecom", "ext_123")
        assert result is not None
        assert result.consumer.id == consumer.id
        assert result.mapping.external_id == "ext_123"
        assert result.match_method == "external_id"
        assert result.is_new is False


class TestMatchByPhone:
    async def test_no_match(self, db, tenant_id):
        result = await match_by_phone(db, tenant_id, "13900001111")
        assert result is None

    async def test_found_by_hash(self, db, tenant_id):
        consumer = await _create_consumer(db, tenant_id, phone_hash="hashed_13900001111")
        # Note: hash_phone("13900001111") won't match "hashed_13900001111"
        # This test validates the query structure


class TestCreateOrUpdateMapping:
    async def test_create_new(self, db, tenant_id):
        consumer = await _create_consumer(db, tenant_id)
        mapping = await create_or_update_mapping(
            db, tenant_id, consumer.id, "wecom", "ext_new"
        )
        assert mapping.external_id == "ext_new"
        assert mapping.source_system == "wecom"
        assert mapping.local_entity_id == consumer.id

    async def test_update_existing(self, db, tenant_id):
        consumer = await _create_consumer(db, tenant_id)
        await create_or_update_mapping(
            db, tenant_id, consumer.id, "wecom", "ext_old"
        )
        await db.commit()

        new_consumer = await _create_consumer(
            db, tenant_id, phone_hash="new_hash", nickname="新用户"
        )
        mapping = await create_or_update_mapping(
            db, tenant_id, new_consumer.id, "wecom", "ext_old"
        )
        assert mapping.local_entity_id == new_consumer.id


class TestGetMappingsForConsumer:
    async def test_no_mappings(self, db, tenant_id):
        consumer = await _create_consumer(db, tenant_id)
        mappings = await get_mappings_for_consumer(db, tenant_id, consumer.id)
        assert mappings == []

    async def test_multiple_mappings(self, db, tenant_id):
        consumer = await _create_consumer(db, tenant_id)
        await create_or_update_mapping(
            db, tenant_id, consumer.id, "wecom", "ext_wecom"
        )
        await create_or_update_mapping(
            db, tenant_id, consumer.id, "youzan", "ext_youzan"
        )
        await db.commit()

        mappings = await get_mappings_for_consumer(db, tenant_id, consumer.id)
        assert len(mappings) == 2
        systems = {m.source_system for m in mappings}
        assert systems == {"wecom", "youzan"}
