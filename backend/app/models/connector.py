"""外部权益连接器模型"""

import uuid

from sqlalchemy import JSON, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class Connector(Base):
    __tablename__ = "connectors"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)

    __table_args__ = (
        Index("ix_connectors_tenant_type", "tenant_id", "connector_type"),
    )


class CouponPool(Base):
    __tablename__ = "coupon_pools"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    total_codes: Mapped[int] = mapped_column(nullable=False, default=0)
    remaining: Mapped[int] = mapped_column(nullable=False, default=0)

    __table_args__ = (
        Index("ix_coupon_pools_tenant", "tenant_id"),
    )


class CouponCode(Base):
    __tablename__ = "coupon_codes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    pool_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    consumer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    distributed: Mapped[bool] = mapped_column(default=False, nullable=False)

    __table_args__ = (
        Index("ix_coupon_codes_pool_dist", "pool_id", "distributed"),
    )
