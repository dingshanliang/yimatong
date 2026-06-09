"""渠道流向模型：经销商、区域、门店"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String, Text, func
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
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (Index("ix_distributors_tenant_code", "tenant_id", "code", unique=True),)


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
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("distributors.id"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (Index("ix_regions_tenant_code", "tenant_id", "code", unique=True),)


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    region_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("regions.id"),
        nullable=True,
        index=True,
    )
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("distributors.id"),
        nullable=True,
        index=True,
    )
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (Index("ix_stores_tenant_code", "tenant_id", "code", unique=True),)


class CodeAllocation(Base):
    """渠道流向登记：记录已赋码货品流向经销商/区域/门店"""

    __tablename__ = "code_allocations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("code_batches.id"),
        nullable=False,
        index=True,
    )
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stores.id"),
        nullable=True,
        index=True,
    )
    region_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("regions.id"),
        nullable=True,
        index=True,
    )
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("distributors.id"),
        nullable=True,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    allocated_at: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_code_alloc_batch_store", "batch_id", "store_id"),
        Index("ix_code_alloc_tenant_batch", "tenant_id", "batch_id"),
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
    resolved: Mapped[bool] = mapped_column(default=False, nullable=False)
    resolution_action: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_diversion_clues_tenant_resolved", "tenant_id", "resolved"),)


class AccountChannelScope(Base):
    """账号可见渠道范围：经销商或门店轻量入口使用"""

    __tablename__ = "account_channel_scopes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("distributors.id"),
        nullable=True,
        index=True,
    )
    region_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("regions.id"),
        nullable=True,
        index=True,
    )
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stores.id"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (Index("ix_account_channel_scope_unique", "tenant_id", "account_id", "scope_type", unique=True),)
