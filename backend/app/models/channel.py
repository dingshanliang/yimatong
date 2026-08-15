"""渠道流向模型：经销商、区域、门店"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class Distributor(Base):
    __tablename__ = "distributors"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_phone_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_phone_recovery_state: Mapped[str] = mapped_column(String(20), nullable=False, default="legacy_unknown")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_distributors_tenant"),
        UniqueConstraint("tenant_id", "id", name="uq_distributors_tenant_id_id_u7a"),
        CheckConstraint(
            "contact_phone_recovery_state IN ('absent','encrypted','recovered','legacy_unknown')",
            name="ck_distributors_phone_recovery_state",
        ),
        CheckConstraint("status IN ('active','inactive')", name="ck_distributors_status"),
        CheckConstraint("version > 0", name="ck_distributors_version_positive"),
        Index("ix_distributors_tenant_code", "tenant_id", "code", unique=True),
    )


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    province: Mapped[str | None] = mapped_column(String(50), nullable=True)
    city: Mapped[str | None] = mapped_column(String(50), nullable=True)
    coverage_type: Mapped[str] = mapped_column(String(30), nullable=False, default="city")
    coverage_areas: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_regions_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "distributor_id"],
            ["distributors.tenant_id", "distributors.id"],
            name="fk_regions_tenant_distributor",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_regions_tenant_id_id_u7a"),
        CheckConstraint("status IN ('active','inactive')", name="ck_regions_status"),
        CheckConstraint("version > 0", name="ck_regions_version_positive"),
        Index("ix_regions_tenant_code", "tenant_id", "code", unique=True),
    )


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    region_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_stores_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "region_id"],
            ["regions.tenant_id", "regions.id"],
            name="fk_stores_tenant_region",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "distributor_id"],
            ["distributors.tenant_id", "distributors.id"],
            name="fk_stores_tenant_distributor",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_stores_tenant_id_id_u7a"),
        CheckConstraint("status IN ('active','inactive')", name="ck_stores_status"),
        CheckConstraint("version > 0", name="ck_stores_version_positive"),
        Index("ix_stores_tenant_code", "tenant_id", "code", unique=True),
    )


class CodeAllocation(Base):
    """渠道流向登记：记录已赋码货品流向经销商/区域/门店。

    yimatong-zgb1.15 Decision 53：版本化流向——每次分配/变更生成可追溯版本，
    不覆盖历史事实。effective_from/effective_to 表达有效期，version 递增。
    """

    __tablename__ = "code_allocations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    store_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    region_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    allocation_root_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False, default="allocate")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    allocated_at: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # yimatong-zgb1.15 Decision 53：版本化字段（有效期 + 版本号）
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    actor_tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    audit_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # 变更原因（审计）
    change_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_code_allocations_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            ["code_batches.tenant_id", "code_batches.id"],
            name="fk_code_allocations_tenant_batch",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_code_allocations_tenant_store",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "region_id"],
            ["regions.tenant_id", "regions.id"],
            name="fk_code_allocations_tenant_region",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "distributor_id"],
            ["distributors.tenant_id", "distributors.id"],
            name="fk_code_allocations_tenant_distributor",
        ),
        ForeignKeyConstraint(
            ["actor_tenant_id", "actor_id"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_code_allocations_actor",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_code_alloc_tenant_id_u7a"),
        UniqueConstraint("tenant_id", "allocation_root_id", "version", name="uq_code_alloc_root_version_u7a"),
        CheckConstraint("version > 0", name="ck_code_allocations_version_positive"),
        CheckConstraint("quantity >= 0", name="ck_code_allocations_quantity_nonnegative"),
        CheckConstraint("action IN ('allocate','reassign','archive')", name="ck_code_allocations_action"),
        CheckConstraint("status IN ('active','archived')", name="ck_code_allocations_status"),
        CheckConstraint(
            "(action = 'archive' AND status = 'archived' AND target_type IS NULL AND target_id IS NULL "
            "AND quantity = 0) OR (action <> 'archive' AND status = 'active' "
            "AND target_type IN ('distributor','region','store') AND target_id IS NOT NULL AND quantity > 0)",
            name="ck_code_allocations_target_shape",
        ),
        Index("ix_code_alloc_batch_store", "batch_id", "store_id"),
        Index("ix_code_alloc_tenant_batch", "tenant_id", "batch_id"),
        Index(
            "uq_code_alloc_current_root_u7a",
            "tenant_id",
            "allocation_root_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
            sqlite_where=text("effective_to IS NULL"),
        ),
        # yimatong-zgb1.15：按有效期查询当前版本
        Index("ix_code_alloc_batch_effective", "batch_id", "effective_from", "effective_to"),
    )


class DiversionClue(Base):
    """窜货线索"""

    __tablename__ = "diversion_clues"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    public_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    code_item_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    expected_region: Mapped[str | None] = mapped_column(String(200), nullable=True)
    detected_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    region_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # yimatong-zgb1.15 Decision 54：位置观察事实（来源/精度/授权状态）
    # location_source：ip_inference / browser_geolocation / manual
    # location_accuracy：high（浏览器 GPS）/ medium（IP 推断）/ low / unknown
    # location_authorized：消费者是否授权位置（浏览器定位场景）
    location_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    location_accuracy: Mapped[str | None] = mapped_column(String(20), nullable=True)
    location_authorized: Mapped[bool | None] = mapped_column(nullable=True)
    # yimatong-zgb1.16：线索可解释性字段（AC3 + AC4）
    # rule_name：命中的规则名（如 cross_region_ip / cross_region_browser）
    # confidence：置信度（high/medium/low）— 低置信度标记 pending_review
    # pending_review：位置不足/低置信度时标记待核实（AC4 不自动确认窜货）
    # observation_count：聚合的观察次数（AC2 同码/同批聚合）
    rule_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pending_review: Mapped[bool] = mapped_column(default=False, nullable=False)
    observation_count: Mapped[int] = mapped_column(default=1, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    # yimatong-zgb1.17 AC1：调查协作字段
    # investigation_status：open / confirmed_diversion / false_positive /
    #   normal_transfer / pending_evidence
    # assigned_to：负责人 account_id
    investigation_status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    resolved: Mapped[bool] = mapped_column(default=False, nullable=False)
    resolution_action: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_diversion_clues_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "code_item_id"],
            ["code_items.tenant_id", "code_items.id"],
            name="fk_diversion_clues_tenant_code_item",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "distributor_id"],
            ["distributors.tenant_id", "distributors.id"],
            name="fk_diversion_clues_tenant_distributor",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "region_id"],
            ["regions.tenant_id", "regions.id"],
            name="fk_diversion_clues_tenant_region",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "assigned_to"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_diversion_clues_tenant_assignee",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "resolved_by_account_id"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_diversion_clues_tenant_resolver",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_diversion_clues_tenant_id_id"),
        CheckConstraint(
            "investigation_status IN "
            "('open','pending_evidence','confirmed_diversion','false_positive','normal_transfer')",
            name="ck_diversion_clues_investigation_status",
        ),
        CheckConstraint("observation_count > 0", name="ck_diversion_clues_observation_count"),
        CheckConstraint("version > 0", name="ck_diversion_clues_version_positive"),
        CheckConstraint(
            "(resolved = false AND investigation_status IN ('open','pending_evidence') "
            "AND resolved_at IS NULL AND resolved_by_account_id IS NULL) OR "
            "(resolved = true AND investigation_status IN "
            "('confirmed_diversion','false_positive','normal_transfer') "
            "AND resolved_at IS NOT NULL AND resolved_by_account_id IS NOT NULL)",
            name="ck_diversion_clues_resolution_state",
        ),
        Index("ix_diversion_clues_tenant_resolved", "tenant_id", "resolved"),
        Index(
            "uq_diversion_clues_open_subject_rule",
            "tenant_id",
            "public_id",
            "rule_name",
            unique=True,
            postgresql_where=text("resolved = false"),
            sqlite_where=text("resolved = false"),
        ),
    )


class AccountChannelScope(Base):
    """账号可见渠道范围：经销商或门店轻量入口使用"""

    __tablename__ = "account_channel_scopes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    region_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
        index=True,
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_account_channel_scopes_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_account_channel_scopes_tenant_account",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "distributor_id"],
            ["distributors.tenant_id", "distributors.id"],
            name="fk_account_channel_scopes_tenant_distributor",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "region_id"],
            ["regions.tenant_id", "regions.id"],
            name="fk_account_channel_scopes_tenant_region",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_account_channel_scopes_tenant_store",
        ),
        CheckConstraint("scope_type IN ('distributor','region','store')", name="ck_account_channel_scopes_type"),
        CheckConstraint(
            "(scope_type='distributor' AND target_id=distributor_id AND distributor_id IS NOT NULL "
            "AND region_id IS NULL AND store_id IS NULL) OR "
            "(scope_type='region' AND target_id=region_id AND distributor_id IS NULL "
            "AND region_id IS NOT NULL AND store_id IS NULL) OR "
            "(scope_type='store' AND target_id=store_id AND distributor_id IS NULL "
            "AND region_id IS NULL AND store_id IS NOT NULL)",
            name="ck_account_channel_scopes_exact_target",
        ),
        CheckConstraint("version > 0", name="ck_account_channel_scopes_version_positive"),
        Index("ix_account_channel_scope_unique", "tenant_id", "account_id", "scope_type", unique=True),
    )


class ChannelActionReceipt(Base):
    """Append-only idempotency and actor receipt for channel mutations."""

    __tablename__ = "channel_action_receipts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    resource_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    actor_tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    audit_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_channel_action_receipts_tenant"),
        ForeignKeyConstraint(
            ["actor_tenant_id", "actor_id"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_channel_action_receipts_actor",
        ),
        UniqueConstraint("tenant_id", "action", "idempotency_key", name="uq_channel_receipts_idem_u7a"),
        UniqueConstraint("tenant_id", "audit_id", name="uq_channel_receipts_audit_u7a"),
        CheckConstraint("resource_version > 0", name="ck_channel_action_receipts_version_positive"),
        Index("ix_channel_action_receipts_resource", "tenant_id", "resource_type", "resource_id"),
    )
