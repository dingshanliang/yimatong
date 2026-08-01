"""既有码接管项目及其不可变交付证据。"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class TakeoverMode(StrEnum):
    legacy_redirect = "legacy_redirect"
    cname = "cname"


class TakeoverProjectStatus(StrEnum):
    draft = "draft"
    needs_fix = "needs_fix"
    ready_for_confirmation = "ready_for_confirmation"
    pending_external = "pending_external"
    cutover_ready = "cutover_ready"
    observing = "observing"
    completed = "completed"
    rolling_back = "rolling_back"
    rolled_back = "rolled_back"
    failed = "failed"
    cancelled = "cancelled"


class TakeoverImportStatus(StrEnum):
    dry_run = "dry_run"
    pending = "pending"
    processing = "processing"
    completed = "completed"
    partial_failed = "partial_failed"
    failed = "failed"


class TakeoverAliasType(StrEnum):
    unique = "unique"
    shared = "shared"


class TakeoverAliasStatus(StrEnum):
    staged = "staged"
    active = "active"
    disabled = "disabled"


class TakeoverRouteStatus(StrEnum):
    candidate = "candidate"
    confirmed = "confirmed"
    active = "active"
    paused = "paused"
    rolled_back = "rolled_back"


class TakeoverProject(Base):
    """一个租户、来源、入口和码范围组成的接管控制面。"""

    __tablename__ = "takeover_projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[TakeoverMode] = mapped_column(String(30), nullable=False)
    source_domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    consumer_domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    expected_cname: Mapped[str] = mapped_column(String(253), nullable=False, default="cname.yimatong.cn")
    sample_url: Mapped[str] = mapped_column(Text, nullable=False)
    url_rule: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    code_scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    control_facts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    responsible_person: Mapped[str] = mapped_column(String(100), nullable=False)
    technical_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rollback_contact: Mapped[str] = mapped_column(String(100), nullable=False)
    fallback_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TakeoverProjectStatus] = mapped_column(
        String(30), nullable=False, default=TakeoverProjectStatus.draft
    )
    assessment: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    readiness_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    readiness_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    configuration_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    brand_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    brand_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    brand_confirmation_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active_route_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_takeover_projects_tenant_status", "tenant_id", "status"),
        Index(
            "uq_takeover_project_domain_owner",
            func.coalesce(consumer_domain, source_domain),
            unique=True,
            postgresql_where=func.coalesce(consumer_domain, source_domain).is_not(None),
        ),
    )


class TakeoverImportJob(Base):
    """导入任务：dry-run 与正式提交共享摘要，保证重试幂等。"""

    __tablename__ = "takeover_import_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("takeover_projects.id"), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[TakeoverImportStatus] = mapped_column(String(30), nullable=False)
    source_rows: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("takeover_import_jobs.id"), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "file_sha256", name="uq_takeover_import_project_file"),
        Index("ix_takeover_import_jobs_tenant_status", "tenant_id", "status"),
    )


class TakeoverImportError(Base):
    __tablename__ = "takeover_import_errors"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("takeover_import_jobs.id"), nullable=False, index=True)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    legacy_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    retryable: Mapped[bool] = mapped_column(nullable=False, default=True)
    raw_row: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TakeoverAlias(Base):
    """旧编号独立命名空间，不改变内部 CodeItem.public_id 规则。"""

    __tablename__ = "takeover_aliases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("takeover_projects.id"), nullable=False, index=True)
    source_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("takeover_import_jobs.id"), nullable=True)
    legacy_code: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_code: Mapped[str] = mapped_column(String(255), nullable=False)
    alias_type: Mapped[TakeoverAliasType] = mapped_column(String(20), nullable=False)
    internal_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("code_items.id"), nullable=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    production_batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("production_batches.id"), nullable=True)
    channel_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    status: Mapped[TakeoverAliasStatus] = mapped_column(String(20), nullable=False, default=TakeoverAliasStatus.staged)
    capabilities: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    disabled_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("project_id", "normalized_code", name="uq_takeover_alias_project_code"),
        Index("ix_takeover_aliases_tenant_code", "tenant_id", "normalized_code"),
    )


class TakeoverDomainCheck(Base):
    __tablename__ = "takeover_domain_checks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("takeover_projects.id"), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    expected_cname: Mapped[str] = mapped_column(String(253), nullable=False)
    observed_cnames: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    observed_ips: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    ttl: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tls_status: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    certificate_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending_external")
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_observation: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TakeoverRouteVersion(Base):
    """路由候选的不可变业务内容；状态变化用事件记录，不原地改写规则。"""

    __tablename__ = "takeover_route_versions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("takeover_projects.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[TakeoverMode] = mapped_column(String(30), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_rule: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    sample_codes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    code_prefix: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[TakeoverRouteStatus] = mapped_column(
        String(20), nullable=False, default=TakeoverRouteStatus.candidate
    )
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    readiness_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    brand_confirmation_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    executed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rollback_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_takeover_route_project_version"),
        Index("ix_takeover_route_tenant_status", "tenant_id", "status"),
    )


class TakeoverCutoverEvent(Base):
    __tablename__ = "takeover_cutover_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("takeover_projects.id"), nullable=False, index=True)
    route_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("takeover_route_versions.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_takeover_event_idempotency"),
        Index("ix_takeover_events_project_created", "project_id", "created_at"),
    )


class TakeoverObservation(Base):
    __tablename__ = "takeover_observations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("takeover_projects.id"), nullable=False, index=True)
    route_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("takeover_route_versions.id"), nullable=True)
    checked_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    success_rate: Mapped[float] = mapped_column(nullable=False, default=0)
    error_rate: Mapped[float] = mapped_column(nullable=False, default=0)
    latency_ms: Mapped[float | None] = mapped_column(nullable=True)
    h5_reach_rate: Mapped[float] = mapped_column(nullable=False, default=0)
    target_match: Mapped[bool] = mapped_column(nullable=False, default=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    recommendation: Mapped[str] = mapped_column(String(30), nullable=False, default="pause")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
