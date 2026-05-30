"""会员与积分模型"""

import uuid
from enum import StrEnum

from sqlalchemy import JSON, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class MemberLevel(StrEnum):
    normal = "normal"
    silver = "silver"
    gold = "gold"
    platinum = "platinum"


class ConsumerProfile(Base):
    __tablename__ = "consumer_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    member_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=MemberLevel.normal,
    )
    tags: Mapped[str | None] = mapped_column(String(500), nullable=True)
    total_points: Mapped[int] = mapped_column(nullable=False, default=0)
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)

    __table_args__ = (
        UniqueConstraint("phone_hash"),
        Index("ix_consumer_profiles_tenant", "tenant_id"),
    )


class PointTransactionType(StrEnum):
    earning = "earning"
    spending = "spending"


class PointTransaction(Base):
    __tablename__ = "point_transactions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    consumer_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    amount: Mapped[int] = mapped_column(nullable=False)
    balance_after: Mapped[int] = mapped_column(nullable=False)
    txn_type: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    reference_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (Index("ix_point_transactions_consumer", "tenant_id", "consumer_id"),)


class PointRule(Base):
    """积分规则配置"""

    __tablename__ = "point_rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    rule_type: Mapped[str] = mapped_column(String(50), nullable=False)
    points: Mapped[int] = mapped_column(nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)

    __table_args__ = (Index("ix_point_rules_tenant_type", "tenant_id", "rule_type", unique=True),)
