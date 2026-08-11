"""既有码接管项目及其不可变交付证据。"""

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
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


def _contains_only(column: str, allowed: str) -> str:
    """Build a PostgreSQL/SQLite-compatible exact character whitelist."""

    remainder = column
    for character in allowed:
        remainder = f"replace({remainder}, '{character}', '')"
    return f"{remainder} = ''"


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
    dead_letter = "dead_letter"


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
    rolling_back = "rolling_back"
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
    domain_verification_token: Mapped[str] = mapped_column(String(36), nullable=False, default=lambda: str(uuid7()))
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
        UniqueConstraint("tenant_id", "id", name="uq_takeover_projects_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "id", "active_route_version_id"],
            [
                "takeover_route_versions.tenant_id",
                "takeover_route_versions.project_id",
                "takeover_route_versions.id",
            ],
            name="fk_takeover_projects_tenant_active_route",
            use_alter=True,
        ),
        Index("ix_takeover_projects_tenant_status", "tenant_id", "status"),
    )


class TakeoverImportJob(Base):
    """导入任务：dry-run 与正式提交共享摘要，保证重试幂等。"""

    __tablename__ = "takeover_import_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[TakeoverImportStatus] = mapped_column(String(30), nullable=False)
    source_rows: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_job_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_takeover_import_jobs_tenant_id_id"),
        UniqueConstraint("tenant_id", "project_id", "id", name="uq_takeover_import_jobs_tenant_project_id"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["takeover_projects.tenant_id", "takeover_projects.id"],
            name="fk_takeover_import_jobs_tenant_project",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "parent_job_id"],
            [
                "takeover_import_jobs.tenant_id",
                "takeover_import_jobs.project_id",
                "takeover_import_jobs.id",
            ],
            name="fk_takeover_import_jobs_tenant_parent",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 20",
            name="ck_takeover_import_jobs_attempt_count",
        ),
        CheckConstraint(
            "((claim_token IS NULL AND claimed_at IS NULL) OR "
            "(claim_token IS NOT NULL AND claimed_at IS NOT NULL AND status = 'processing'))",
            name="ck_takeover_import_jobs_claim_state",
        ),
        UniqueConstraint("project_id", "file_sha256", name="uq_takeover_import_project_file"),
        Index("ix_takeover_import_jobs_tenant_status", "tenant_id", "status"),
        Index(
            "ix_takeover_import_jobs_pending_poll",
            "tenant_id",
            "next_attempt_at",
            "created_at",
            postgresql_where=text("status IN ('pending', 'failed')"),
        ),
        Index(
            "ix_takeover_import_jobs_stale_claim",
            "claimed_at",
            postgresql_where=text("status = 'processing'"),
        ),
    )


class TakeoverImportError(Base):
    __tablename__ = "takeover_import_errors"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    legacy_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    retryable: Mapped[bool] = mapped_column(nullable=False, default=True)
    raw_row: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["takeover_import_jobs.tenant_id", "takeover_import_jobs.id"],
            name="fk_takeover_import_errors_tenant_job",
        ),
    )


class TakeoverAlias(Base):
    """旧编号独立命名空间，不改变内部 CodeItem.public_id 规则。"""

    __tablename__ = "takeover_aliases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    source_job_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    legacy_code: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_code: Mapped[str] = mapped_column(String(255), nullable=False)
    alias_type: Mapped[TakeoverAliasType] = mapped_column(String(20), nullable=False)
    internal_code_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    production_batch_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    channel_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    status: Mapped[TakeoverAliasStatus] = mapped_column(String(20), nullable=False, default=TakeoverAliasStatus.staged)
    capabilities: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    disabled_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["takeover_projects.tenant_id", "takeover_projects.id"],
            name="fk_takeover_aliases_tenant_project",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "source_job_id"],
            [
                "takeover_import_jobs.tenant_id",
                "takeover_import_jobs.project_id",
                "takeover_import_jobs.id",
            ],
            name="fk_takeover_aliases_tenant_source_job",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "internal_code_id"],
            ["code_items.tenant_id", "code_items.id"],
            name="fk_takeover_aliases_tenant_code_item",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_takeover_aliases_tenant_product",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "production_batch_id"],
            ["production_batches.tenant_id", "production_batches.id"],
            name="fk_takeover_aliases_tenant_production_batch",
        ),
        UniqueConstraint("project_id", "normalized_code", name="uq_takeover_alias_project_code"),
        Index("ix_takeover_aliases_tenant_code", "tenant_id", "normalized_code"),
    )


class TakeoverDomainCheck(Base):
    __tablename__ = "takeover_domain_checks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    expected_cname: Mapped[str] = mapped_column(String(253), nullable=False)
    observed_cnames: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    observed_ips: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    observed_ownership_tokens: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    ownership_verified: Mapped[bool] = mapped_column(nullable=False, default=False)
    ttl: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tls_status: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    certificate_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending_external")
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_observation: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "id", name="uq_takeover_domain_checks_tenant_project_id"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["takeover_projects.tenant_id", "takeover_projects.id"],
            name="fk_takeover_domain_checks_tenant_project",
        ),
    )


class TakeoverRouteVersion(Base):
    """路由候选的不可变业务内容；状态变化用事件记录，不原地改写规则。"""

    __tablename__ = "takeover_route_versions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
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
    current_event_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "id", name="uq_takeover_routes_tenant_project_id"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["takeover_projects.tenant_id", "takeover_projects.id"],
            name="fk_takeover_routes_tenant_project",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "id", "current_event_id"],
            [
                "takeover_cutover_events.tenant_id",
                "takeover_cutover_events.project_id",
                "takeover_cutover_events.route_version_id",
                "takeover_cutover_events.id",
            ],
            name="fk_takeover_routes_tenant_current_event",
            use_alter=True,
        ),
        UniqueConstraint("project_id", "version", name="uq_takeover_route_project_version"),
        Index("ix_takeover_route_tenant_status", "tenant_id", "status"),
    )


class TakeoverCutoverEvent(Base):
    __tablename__ = "takeover_cutover_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    route_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "route_version_id",
            "id",
            name="uq_takeover_events_tenant_project_route_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["takeover_projects.tenant_id", "takeover_projects.id"],
            name="fk_takeover_events_tenant_project",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "route_version_id"],
            [
                "takeover_route_versions.tenant_id",
                "takeover_route_versions.project_id",
                "takeover_route_versions.id",
            ],
            name="fk_takeover_events_tenant_route",
        ),
        UniqueConstraint("project_id", "idempotency_key", name="uq_takeover_event_idempotency"),
        Index("ix_takeover_events_project_created", "project_id", "created_at"),
    )


class TakeoverObservation(Base):
    __tablename__ = "takeover_observations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    route_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    transition_event_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    checked_url: Mapped[str] = mapped_column(Text, nullable=False)
    observed_target_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_source: Mapped[str] = mapped_column(String(30), nullable=False, default="legacy_client")
    evidence_purpose: Mapped[str] = mapped_column(String(30), nullable=False, default="legacy_client")
    evidence_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    success_rate: Mapped[float] = mapped_column(nullable=False, default=0)
    error_rate: Mapped[float] = mapped_column(nullable=False, default=0)
    latency_ms: Mapped[float | None] = mapped_column(nullable=True)
    h5_reach_rate: Mapped[float] = mapped_column(nullable=False, default=0)
    target_match: Mapped[bool] = mapped_column(nullable=False, default=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    recommendation: Mapped[str] = mapped_column(String(30), nullable=False, default="pause")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["takeover_projects.tenant_id", "takeover_projects.id"],
            name="fk_takeover_observations_tenant_project",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "route_version_id"],
            [
                "takeover_route_versions.tenant_id",
                "takeover_route_versions.project_id",
                "takeover_route_versions.id",
            ],
            name="fk_takeover_observations_tenant_route",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "route_version_id", "transition_event_id"],
            [
                "takeover_cutover_events.tenant_id",
                "takeover_cutover_events.project_id",
                "takeover_cutover_events.route_version_id",
                "takeover_cutover_events.id",
            ],
            name="fk_takeover_observations_tenant_event",
        ),
        CheckConstraint(
            "evidence_source IN ('legacy_client', 'server_probe')",
            name="ck_takeover_observations_evidence_source",
        ),
        CheckConstraint(
            "evidence_purpose IN ('legacy_client', 'pre_cutover', 'cutover', 'rollback')",
            name="ck_takeover_observations_evidence_purpose",
        ),
        CheckConstraint(
            "(evidence_source = 'legacy_client' AND evidence_purpose = 'legacy_client') OR "
            "(transition_event_id IS NOT NULL AND observed_target_url IS NOT NULL "
            "AND evidence_purpose IN ('pre_cutover', 'cutover', 'rollback') "
            "AND length(evidence_digest) = 64 AND evidence_digest = lower(evidence_digest) AND "
            + _contains_only("evidence_digest", "0123456789abcdef")
            + ")",
            name="ck_takeover_observations_server_probe",
        ),
    )


class TakeoverDomainClaim(Base):
    """Global public-routing authority, accessible to runtime only through reviewed functions."""

    __tablename__ = "takeover_domain_claims"

    domain_key: Mapped[str] = mapped_column(String(253), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    route_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    verification_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    domain_check_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    verification_event_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    current_event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", name="uq_takeover_domain_claims_tenant_project"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["takeover_projects.tenant_id", "takeover_projects.id"],
            name="fk_takeover_domain_claims_tenant_project",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "route_version_id"],
            [
                "takeover_route_versions.tenant_id",
                "takeover_route_versions.project_id",
                "takeover_route_versions.id",
            ],
            name="fk_takeover_domain_claims_tenant_route",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "domain_check_id"],
            [
                "takeover_domain_checks.tenant_id",
                "takeover_domain_checks.project_id",
                "takeover_domain_checks.id",
            ],
            name="fk_takeover_domain_claims_tenant_check",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "route_version_id", "verification_event_id"],
            [
                "takeover_cutover_events.tenant_id",
                "takeover_cutover_events.project_id",
                "takeover_cutover_events.route_version_id",
                "takeover_cutover_events.id",
            ],
            name="fk_takeover_domain_claims_tenant_verification_event",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "route_version_id", "current_event_id"],
            [
                "takeover_cutover_events.tenant_id",
                "takeover_cutover_events.project_id",
                "takeover_cutover_events.route_version_id",
                "takeover_cutover_events.id",
            ],
            name="fk_takeover_domain_claims_tenant_event",
        ),
        CheckConstraint(
            "domain_key = lower(rtrim(trim(domain_key), '.')) AND domain_key <> ''",
            name="ck_takeover_domain_claims_domain_key",
        ),
        CheckConstraint(
            "state IN ('active', 'completed', 'rolling_back', 'rolled_back')",
            name="ck_takeover_domain_claims_state",
        ),
        CheckConstraint(
            "(verification_kind = 'cname_dns_tls' AND domain_check_id IS NOT NULL "
            "AND verification_event_id IS NULL) OR "
            "(verification_kind = 'legacy_server_redirect' AND domain_check_id IS NULL "
            "AND verification_event_id IS NOT NULL)",
            name="ck_takeover_domain_claims_verification",
        ),
        {"comment": "Global public-routing authority; deliberately no RLS and no direct runtime table privileges"},
    )
