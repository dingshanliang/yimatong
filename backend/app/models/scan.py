"""扫码事件模型"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid7


class ScanEvent(Base):
    __tablename__ = "scan_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    public_id: Mapped[str] = mapped_column(String(20), nullable=False)
    scan_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_first_scan: Mapped[bool] = mapped_column(default=False, nullable=False)
    environment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # yimatong-zgb1.10 Decision 20：有效访问标记。
    # 有效访问 = H5 成功加载 + 活动可用的 active 码 + 非异常权益暂停 + 非 robot/internal test。
    # raw scan（解析请求）保持为诊断指标，不替代有效访问作分母（Decision 21）。
    is_valid_visit: Mapped[bool] = mapped_column(default=False, nullable=False)
    # yimatong-zgb1.10 Decision 22：关联匿名访客（first-party 稳定 ID）。
    # 跨会话稳定，清缓存即换新（保守，不做弱信号合并）。
    visitor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # yimatong-zgb1.15 Decision 54：扫码位置观察事实
    location_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    location_accuracy: Mapped[str | None] = mapped_column(String(20), nullable=True)
    location_authorized: Mapped[bool | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "public_id"],
            ["code_items.tenant_id", "code_items.public_id"],
            name="fk_scan_events_tenant_public_id",
        ),
        Index("ix_scan_events_ip", "ip_hash"),
        Index("ix_scan_events_environment", "environment"),
        Index("ix_scan_events_public_id", "public_id"),
        Index("ix_scan_events_tenant_time", "tenant_id", "scan_time"),
        Index("ix_scan_events_valid_visit", "tenant_id", "is_valid_visit"),
    )
