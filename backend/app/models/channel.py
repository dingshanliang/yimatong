"""渠道流向模型：经销商、区域、门店"""

import uuid

from sqlalchemy import ForeignKey, Index, String, Text
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

    __table_args__ = (Index("ix_distributors_tenant_code", "tenant_id", "code", unique=True),)


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    province: Mapped[str | None] = mapped_column(String(50), nullable=True)
    city: Mapped[str | None] = mapped_column(String(50), nullable=True)
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("distributors.id"),
        nullable=True,
        index=True,
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

    __table_args__ = (Index("ix_stores_tenant_code", "tenant_id", "code", unique=True),)


class DiversionClue(Base):
    """窜货线索"""

    __tablename__ = "diversion_clues"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    public_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    code_item_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    expected_region: Mapped[str | None] = mapped_column(String(200), nullable=True)
    detected_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved: Mapped[bool] = mapped_column(default=False, nullable=False)

    __table_args__ = (Index("ix_diversion_clues_tenant_resolved", "tenant_id", "resolved"),)
