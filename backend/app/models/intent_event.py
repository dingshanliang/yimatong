"""意图事件模型（yimatong-zgb1.10 增长转化线主干）。

Decision 19：意图事件 = 有效页面访问、权益入口点击、留资入口点击、企微入口点击、商城跳转。
与确认结果（领取/留资/订单）分开记录。

本表记录 H5 侧的意图事件（page_view / benefit_click / lead_click / wecom_click / mall_redirect）。
幂等依据：(tenant_id, visitor_id, event_type, public_id, client_event_id)。
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class IntentEvent(Base):
    __tablename__ = "intent_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    # 事件类型（page_view / benefit_click / lead_click / wecom_click / mall_redirect）
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    public_id: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # 关联匿名访客（first-party 稳定 ID）
    visitor_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 幂等键（客户端生成，防重复上报；同一 visitor + event_type + client_event_id 只记一次）
    client_event_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 来源页面版本（便于漏斗分析）
    page_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 发生时间（客户端上报的时间）
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # 接收时间（服务端记录的时间）
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_intent_events_tenant_type", "tenant_id", "event_type"),
        # 幂等：同一 visitor + event_type + client_event_id 唯一
        Index(
            "ix_intent_events_idempotent",
            "tenant_id",
            "visitor_id",
            "event_type",
            "client_event_id",
        ),
    )
