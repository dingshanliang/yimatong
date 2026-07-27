"""外部成交与 GMV 归因模型"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String, UniqueConstraint, func
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
    channel: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    source_system: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # yimatong-zgb1.13：订单状态 + 退款金额 + 币种（Decision 30 净 GMV）
    # status：paid / refunded / partially_refunded / cancelled
    # refund_amount：累计退款金额（含部分退款）；net_amount = amount - refund_amount
    # currency：币种（ISO 4217，默认 CNY）
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="paid")
    refund_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_ext_orders_tenant_ext_id", "tenant_id", "external_id"),
        # yimatong-zgb1.13：去重 unique constraint（同 source + external_id 只导入一次）
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "external_id",
            name="uq_ext_orders_source_external",
        ),
    )


class GmvAttribution(Base):
    __tablename__ = "gmv_attributions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    external_order_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    public_id: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    code_item_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    consumer_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    amount: Mapped[float] = mapped_column(nullable=False)
    match_type: Mapped[str] = mapped_column(String(30), nullable=False)
    scan_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attribution_window_hours: Mapped[int] = mapped_column(default=168, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    # yimatong-zgb1.14 Decision 33：归因快照（不可漂移）。
    # 这些字段在归因写入时快照，后续配置变更不改写历史。
    product_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code_batch_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    channel_snapshot: Mapped[str | None] = mapped_column(String(50), nullable=True)
    page_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    original_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_gmv_attr_tenant_order", "tenant_id", "external_order_id"),
        Index("ix_gmv_attr_tenant_campaign", "tenant_id", "campaign_id"),
    )


class GmvDailyStats(Base):
    __tablename__ = "gmv_daily_stats"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    stat_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    channel: Mapped[str | None] = mapped_column(String(50), nullable=True)
    attributed_gmv: Mapped[float] = mapped_column(nullable=False, default=0)
    attributed_orders: Mapped[int] = mapped_column(nullable=False, default=0)
    scan_count: Mapped[int] = mapped_column(nullable=False, default=0)
    scan_uv: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_gmv_daily_tenant_date", "tenant_id", "stat_date"),
        Index("uq_gmv_daily_stats", "tenant_id", "stat_date", "campaign_id", "channel", unique=True),
    )
