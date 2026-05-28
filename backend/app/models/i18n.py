"""多语言翻译模型"""

import uuid

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class Translation(Base):
    __tablename__ = "translations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    locale: Mapped[str] = mapped_column(String(10), nullable=False)
    value: Mapped[str] = mapped_column(String(1000), nullable=False)

    __table_args__ = (
        Index("ix_translations_tenant_key_locale", "tenant_id", "key", "locale", unique=True),
    )
