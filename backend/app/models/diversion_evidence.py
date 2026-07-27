"""防窜调查证据模型（yimatong-zgb1.17）。

AC2：证据保留来源、上传人、时间和与扫码观察的关联。
每条证据关联一个 DiversionClue，记录证据类型（transfer/order/logistics/explanation）、
文件 URL、上传人、上传时间。
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class DiversionEvidence(Base):
    __tablename__ = "diversion_evidence"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    clue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("diversion_clues.id"), nullable=False, index=True
    )
    # 证据类型：transfer（调货单）/ order（订单）/ logistics（物流）/ explanation（说明）/ other
    evidence_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # 证据来源：distributor（经销商）/ brand_ops（品牌运营）/ system（系统）/ other
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="distributor")
    # 文件 URL 或文本内容
    file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 上传人 + 时间（AC2）
    uploaded_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
