"""会员与积分模型"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
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
    phone_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    phone_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    phone_key_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lead_contact_suppressed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    lead_consent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    lead_scan_event_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    lead_scan_event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lead_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
        ForeignKeyConstraint(
            ["tenant_id", "lead_consent_id"],
            ["consent_records.tenant_id", "consent_records.id"],
            name="fk_consumer_profiles_tenant_lead_consent",
            use_alter=True,
        ),
        UniqueConstraint("tenant_id", "id", name="uq_consumer_profiles_tenant_id"),
        UniqueConstraint("tenant_id", "phone_hash", name="uq_consumer_tenant_phone"),
        CheckConstraint(
            "(phone_hash IS NULL AND phone_ciphertext IS NULL AND phone_nonce IS NULL AND phone_key_id IS NULL) OR "
            "(length(phone_hash)=64 AND phone_ciphertext IS NOT NULL AND length(phone_nonce)=12 "
            "AND NULLIF(trim(phone_key_id),'') IS NOT NULL)",
            name="ck_consumer_profiles_phone_envelope",
        ),
        CheckConstraint(
            "phone_hash IS NULL OR (phone_hash ~ '^[0-9a-f]{64}$' AND octet_length(phone_ciphertext)=27 "
            "AND phone_key_id ~ '^aes-master-v[1-9][0-9]*$')",
            name="ck_consumer_profiles_phone_envelope_format",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "lead_consent_id IS NULL OR (lead_scan_event_id IS NOT NULL AND lead_scan_event_time IS NOT NULL "
            "AND lead_captured_at IS NOT NULL)",
            name="ck_consumer_profiles_lead_provenance",
        ),
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
        Index("ix_consumer_profiles_tenant_lead_consent", "tenant_id", "lead_consent_id"),
    )


class BrandMembership(Base):
    """A consumer's explicit, tenant-owned membership relationship."""

    __tablename__ = "brand_memberships"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    membership_number: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    join_consent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_brand_memberships_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "join_consent_id"],
            ["consent_records.tenant_id", "consent_records.id"],
            name="fk_brand_memberships_join_consent",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "merged_into_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_brand_memberships_merged_into",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_brand_memberships_tenant_id"),
        UniqueConstraint("tenant_id", "membership_number", name="uq_brand_memberships_tenant_number"),
        CheckConstraint("status IN ('active','merged')", name="ck_brand_memberships_status"),
        CheckConstraint(
            "(status='active' AND merged_into_id IS NULL) OR (status='merged' AND merged_into_id IS NOT NULL)",
            name="ck_brand_memberships_merge_state",
        ),
        CheckConstraint("merged_into_id IS NULL OR merged_into_id<>id", name="ck_brand_memberships_not_self_merged"),
        Index("ix_brand_memberships_tenant_join_consent", "tenant_id", "join_consent_id"),
        Index(
            "ix_brand_memberships_tenant_merged_into",
            "tenant_id",
            "merged_into_id",
            postgresql_where=text("merged_into_id IS NOT NULL"),
            sqlite_where=text("merged_into_id IS NOT NULL"),
        ),
        Index("ix_brand_memberships_tenant_joined", "tenant_id", "joined_at"),
    )


class BrandMembershipProfileLink(Base):
    """Preserves every tenant profile that has been strongly linked to a member."""

    __tablename__ = "brand_membership_profile_links"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    consumer_profile_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    link_reason: Mapped[str] = mapped_column(String(30), nullable=False)
    verification_receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_membership_profile_links_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_membership_profile_links_membership",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "consumer_profile_id"],
            ["consumer_profiles.tenant_id", "consumer_profiles.id"],
            name="fk_membership_profile_links_consumer",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_membership_profile_links_tenant_id"),
        UniqueConstraint("tenant_id", "consumer_profile_id", name="uq_membership_profile_links_tenant_consumer"),
        CheckConstraint(
            "link_reason IN ('explicit_join','verified_recovery','verified_merge')",
            name="ck_membership_profile_links_reason",
        ),
        CheckConstraint(
            "length(verification_receipt_hash)=64",
            name="ck_membership_profile_links_receipt_hash",
        ),
        Index("ix_membership_profile_links_tenant_member", "tenant_id", "membership_id"),
        Index(
            "uq_membership_profile_links_primary",
            "tenant_id",
            "membership_id",
            unique=True,
            postgresql_where=text("is_primary IS TRUE"),
            sqlite_where=text("is_primary IS TRUE"),
        ),
    )


class MemberIdentityCredential(Base):
    """A verified, issuer-scoped credential that can restore one brand member."""

    __tablename__ = "member_identity_credentials"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    credential_type: Mapped[str] = mapped_column(String(30), nullable=False)
    issuer: Mapped[str] = mapped_column(String(160), nullable=False)
    subject_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    subject_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    subject_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_member_identity_credentials_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_member_identity_credentials_membership",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_member_identity_credentials_tenant_id"),
        CheckConstraint(
            "credential_type IN ('verified_phone','wechat_openid','wechat_unionid')",
            name="ck_member_identity_credentials_type",
        ),
        CheckConstraint("length(trim(issuer)) BETWEEN 1 AND 160", name="ck_member_identity_credentials_issuer"),
        CheckConstraint("length(subject_hash)=64", name="ck_member_identity_credentials_hash"),
        CheckConstraint(
            "length(verification_receipt_hash)=64",
            name="ck_member_identity_credentials_receipt_hash",
        ),
        CheckConstraint(
            "length(subject_nonce)=12 AND octet_length(subject_ciphertext)>=16 "
            "AND subject_key_id ~ '^aes-master-v[1-9][0-9]*$'",
            name="ck_member_identity_credentials_envelope",
        ).ddl_if(dialect="postgresql"),
        Index("ix_member_identity_credentials_tenant_member", "tenant_id", "membership_id"),
        Index(
            "uq_member_identity_credentials_active_subject",
            "tenant_id",
            "credential_type",
            "issuer",
            "subject_hash",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )


class BrandMembershipEvent(Base):
    """Append-only, PII-free receipt for membership authority changes."""

    __tablename__ = "brand_membership_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_brand_membership_events_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_brand_membership_events_membership",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_brand_membership_events_tenant_id"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_brand_membership_events_idempotency"),
        CheckConstraint(
            "event_type IN ('joined','identity_bound','recovered','merged_source','merged_target')",
            name="ck_brand_membership_events_type",
        ),
        CheckConstraint("length(payload_hash)=64", name="ck_brand_membership_events_payload_hash"),
        Index("ix_brand_membership_events_tenant_member", "tenant_id", "membership_id", "occurred_at"),
    )


class ConsumerPhoneEncryptionKey(Base):
    """Deployment-global non-secret key metadata for sanctioned phone envelopes."""

    __tablename__ = "consumer_phone_encryption_keys"

    key_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    algorithm: Mapped[str] = mapped_column(String(20), nullable=False, default="aes-256-gcm")
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("algorithm='aes-256-gcm'", name="ck_consumer_phone_keys_algorithm"),
        CheckConstraint("status IN ('active','retired')", name="ck_consumer_phone_keys_status"),
        CheckConstraint(
            "(status='active' AND retired_at IS NULL) OR (status='retired' AND retired_at IS NOT NULL)",
            name="ck_consumer_phone_keys_retirement",
        ),
        Index(
            "uq_consumer_phone_keys_single_active",
            "status",
            unique=True,
            postgresql_where=text("status='active'"),
            sqlite_where=text("status='active'"),
        ),
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
