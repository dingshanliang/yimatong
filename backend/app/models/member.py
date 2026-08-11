"""会员与积分模型"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
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
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    wechat_openid_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wechat_openid_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    wechat_openid_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    wechat_openid_key_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_consumer_profiles_tenant"),
        UniqueConstraint("tenant_id", "phone_hash", name="uq_consumer_tenant_phone"),
        CheckConstraint(
            "(wechat_openid_hash IS NULL AND wechat_openid_ciphertext IS NULL "
            "AND wechat_openid_nonce IS NULL AND wechat_openid_key_id IS NULL) OR "
            "(length(wechat_openid_hash)=64 AND wechat_openid_ciphertext IS NOT NULL "
            "AND length(wechat_openid_nonce)=12 AND NULLIF(trim(wechat_openid_key_id),'') IS NOT NULL)",
            name="ck_consumer_profiles_wechat_openid_envelope",
        ),
        CheckConstraint(
            "wechat_openid_hash IS NULL OR (wechat_openid_hash ~ '^[0-9a-f]{64}$' "
            "AND octet_length(wechat_openid_ciphertext)>=16 "
            "AND wechat_openid_key_id ~ '^aes-master-v[1-9][0-9]*$')",
            name="ck_consumer_profiles_wechat_openid_envelope_format",
        ).ddl_if(dialect="postgresql"),
        Index("ix_consumer_profiles_tenant_id", "tenant_id"),
        Index(
            "uq_consumer_profiles_tenant_wechat_openid_hash",
            "tenant_id",
            "wechat_openid_hash",
            unique=True,
            postgresql_where=text("wechat_openid_hash IS NOT NULL"),
            sqlite_where=text("wechat_openid_hash IS NOT NULL"),
        ),
        Index("ix_consumer_profiles_tenant_phone", "tenant_id", "phone_hash"),
    )


class PointTransactionType(StrEnum):
    earning = "earning"
    spending = "spending"
    expired = "expired"


class PointTransaction(Base):
    __tablename__ = "point_transactions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    consumer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("consumer_profiles.id"), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(nullable=False)
    balance_after: Mapped[int] = mapped_column(nullable=False)
    txn_type: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    reference_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_expired: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_point_transactions_consumer", "tenant_id", "consumer_id"),
        Index("ix_point_transactions_tenant_created", "tenant_id", "created_at"),
        Index("ix_point_txn_expires", "expires_at", postgresql_where=mapped_column("expires_at").is_not(None)),
    )


class PointRule(Base):
    """积分规则配置"""

    __tablename__ = "point_rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    rule_type: Mapped[str] = mapped_column(String(50), nullable=False)
    points: Mapped[int] = mapped_column(nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    daily_limit: Mapped[int] = mapped_column(default=0, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_point_rules_tenant_type", "tenant_id", "rule_type", unique=True),)


class PointProduct(Base):
    """积分商城商品"""

    __tablename__ = "point_products"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    points_cost: Mapped[int] = mapped_column(nullable=False)
    stock: Mapped[int] = mapped_column(nullable=False, default=0)
    total_claimed: Mapped[int] = mapped_column(nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    benefit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("benefits.id", ondelete="SET NULL"), nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    per_consumer_limit: Mapped[int] = mapped_column(nullable=False, default=1)
    sort_order: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_point_products_tenant", "tenant_id"),
        Index("ix_point_products_tenant_enabled_sort", "tenant_id", "enabled", "sort_order"),
        CheckConstraint("points_cost > 0", name="check_point_product_cost_positive"),
        CheckConstraint("stock >= 0", name="check_point_product_stock_nonneg"),
        CheckConstraint("per_consumer_limit >= 0", name="check_point_product_limit_nonneg"),
    )


class PointRedemptionStatus(StrEnum):
    success = "success"
    failed = "failed"


class PointRedemption(Base):
    """积分商品兑换记录"""

    __tablename__ = "point_redemptions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    consumer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("consumer_profiles.id"), nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("point_products.id"), nullable=False, index=True)
    points_cost: Mapped[int] = mapped_column(nullable=False)
    point_transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("point_transactions.id"), nullable=False, index=True
    )
    benefit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("benefits.id", ondelete="SET NULL"), nullable=True, index=True
    )
    benefit_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("benefit_claims.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=PointRedemptionStatus.success)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_point_redemptions_tenant_created", "tenant_id", "created_at"),
        Index("ix_point_redemptions_tenant_product", "tenant_id", "product_id"),
        Index("ix_point_redemptions_tenant_consumer", "tenant_id", "consumer_id"),
    )
