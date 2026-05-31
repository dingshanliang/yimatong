"""AI 生成记录模型"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class AiGeneration(Base):
    __tablename__ = "ai_generations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)

    # 生成类型：extract_text | extract_image | copywriting | page_copy | page_suggest | campaign
    type: Mapped[str] = mapped_column(String(50), nullable=False)

    # 多态外键 — 关联产品、页面模板、活动等实体
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    # 输入快照（调用参数，便于审计和 prompt 优化）
    input_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # AI 输出结果
    output_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 状态：draft → accepted | discarded
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")

    # 使用的模型版本（便于追踪效果）
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
