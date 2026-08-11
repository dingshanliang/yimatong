"""外部权益连接器模型"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class Connector(Base):
    __tablename__ = "connectors"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    secrets_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "NOT (config::jsonb ?| ARRAY['oa_appsecret','cert_private_key','api_v3_key',"
            "'api_key','api_secret','callback_secret','mch_key','secret'])",
            name="ck_connectors_config_has_no_plaintext_secrets",
        ).ddl_if(dialect="postgresql"),
        UniqueConstraint("tenant_id", "id", name="uq_connectors_tenant_id_id"),
        Index("ix_connectors_tenant_type", "tenant_id", "connector_type"),
        Index(
            "uq_connectors_active_wecom_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("connector_type = 'wecom_customer_contact' AND enabled IS TRUE"),
            sqlite_where=text("connector_type = 'wecom_customer_contact' AND enabled = 1"),
        ),
    )


class CouponPool(Base):
    __tablename__ = "coupon_pools"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    total_codes: Mapped[int] = mapped_column(nullable=False, default=0)
    remaining: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_coupon_pools_tenant"),
        CheckConstraint("total_codes >= 0", name="ck_coupon_pools_total_nonnegative"),
        CheckConstraint("remaining >= 0 AND remaining <= total_codes", name="ck_coupon_pools_remaining_range"),
        Index("uq_coupon_pools_tenant_id_id_idx", "tenant_id", "id", unique=True),
        Index("ix_coupon_pools_tenant", "tenant_id"),
    )


class CouponCode(Base):
    __tablename__ = "coupon_codes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    pool_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    claim_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    consumer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    distributed: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "pool_id"],
            ["coupon_pools.tenant_id", "coupon_pools.id"],
            name="fk_coupon_codes_tenant_pool",
        ),
        CheckConstraint(
            "claim_id IS NULL OR (distributed IS TRUE AND NULLIF(trim(consumer_id),'') IS NOT NULL)",
            name="ck_coupon_codes_claim_distribution",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "claim_id"],
            ["benefit_claims.tenant_id", "benefit_claims.id"],
            name="fk_coupon_codes_tenant_claim",
        ),
        Index("uq_coupon_codes_tenant_code_idx", "tenant_id", "code", unique=True),
        Index("ix_coupon_codes_pool_dist", "pool_id", "distributed"),
        Index("ix_coupon_codes_tenant_pool_dist_id", "tenant_id", "pool_id", "distributed", "id"),
        Index(
            "uq_coupon_codes_tenant_claim",
            "tenant_id",
            "claim_id",
            unique=True,
            postgresql_where=text("claim_id IS NOT NULL"),
            sqlite_where=text("claim_id IS NOT NULL"),
        ),
    )


class BenefitDelivery(Base):
    """外部权益发放记录，支持状态回传和失败重试"""

    __tablename__ = "benefit_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    connector_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    benefit_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    claim_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    campaign_outbox_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    consumer_id: Mapped[str] = mapped_column(String(100), nullable=False)
    benefit_type: Mapped[str] = mapped_column(String(50), nullable=False)
    benefit_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    external_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "connector_id"],
            ["connectors.tenant_id", "connectors.id"],
            name="fk_benefit_deliveries_tenant_connector",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "benefit_id"],
            ["benefits.tenant_id", "benefits.id"],
            name="fk_benefit_deliveries_tenant_benefit",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "claim_id"],
            ["benefit_claims.tenant_id", "benefit_claims.id"],
            name="fk_benefit_deliveries_tenant_claim",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_outbox_id"],
            ["campaign_claim_outbox.tenant_id", "campaign_claim_outbox.id"],
            name="fk_benefit_deliveries_tenant_outbox",
        ),
        UniqueConstraint(
            "tenant_id",
            "campaign_outbox_id",
            name="uq_benefit_deliveries_tenant_outbox",
        ),
        CheckConstraint(
            "campaign_outbox_id IS NULL OR (claim_id IS NOT NULL AND benefit_id IS NOT NULL "
            "AND NULLIF(trim(external_id),'') IS NOT NULL)",
            name="ck_benefit_deliveries_authority_identity",
        ),
        Index("ix_benefit_deliveries_tenant_status", "tenant_id", "status"),
        Index("ix_benefit_deliveries_retry", "status", "next_retry_at"),
        Index(
            "uq_benefit_deliveries_authority_claim",
            "tenant_id",
            "claim_id",
            unique=True,
            postgresql_where=text("campaign_outbox_id IS NOT NULL"),
            sqlite_where=text("campaign_outbox_id IS NOT NULL"),
        ),
    )
