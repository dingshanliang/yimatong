"""导出审计日志模型"""

import uuid

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class ExportLog(Base):
    __tablename__ = "export_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    export_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    row_count: Mapped[int] = mapped_column(default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
