"""匿名访客模型（yimatong-zgb1.10 增长转化线主干）。

Decision 22：匿名访客接收 first-party 匿名访客标识。身份可通过权威消费者、留资、
企业微信或可信外部关系升级。IP、设备指纹或位置单独不能合并消费者，匿名跨设备访问保持独立。

设计：
- visitor_id 是 first-party 稳定 UUID，服务端签发，H5 localStorage 持有。
- 同一 visitor 可关联多个 scan_events（一次访问可能扫多个码）。
- 不存 PII（无手机号/openid），只存聚合分析用信号（首次访问时间、环境）。
- consumer_id 可选：当 visitor 完成留资/绑定后，关联到 ConsumerProfile（身份升级）。
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class AnonymousVisitor(Base):
    __tablename__ = "anonymous_visitors"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    # first-party 稳定访客标识（H5 localStorage 持有，跨会话稳定，清缓存即换新）
    visitor_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # 可选：身份升级后关联到 ConsumerProfile（Decision 22 身份升级路径）
    consumer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("consumer_profiles.id"), nullable=True
    )
    # 首次访问环境（wechat/alipay/browser，用于诊断，非身份合并依据）
    first_environment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    first_ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_visitors_tenant_consumer", "tenant_id", "consumer_id"),
    )
