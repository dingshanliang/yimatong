"""外部成交与 GMV 归因模型"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class ExternalOrder(Base):
    __tablename__ = "external_orders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[float] = mapped_column(nullable=False)
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    product_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    order_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    matched: Mapped[bool] = mapped_column(default=False, nullable=False)

    __table_args__ = (Index("ix_ext_orders_tenant_ext_id", "tenant_id", "external_id"),)


class GmvAttribution(Base):
    __tablename__ = "gmv_attributions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    external_order_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    public_id: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    amount: Mapped[float] = mapped_column(nullable=False)
    match_type: Mapped[str] = mapped_column(String(30), nullable=False)

    __table_args__ = (Index("ix_gmv_attr_tenant_order", "tenant_id", "external_order_id"),)
