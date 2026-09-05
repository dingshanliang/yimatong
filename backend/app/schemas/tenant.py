import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.utils.email import normalize_email
from app.utils.security import validate_password_strength


class TenantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="租户名称", examples=["示例食品公司"])
    slug: str | None = Field(
        None,
        min_length=1,
        max_length=50,
        pattern=r"^[a-z0-9-]+$",
        description="租户唯一标识（URL 友好）",
        examples=["example-food"],
    )
    plan: str = Field("free", description="订阅计划", examples=["free", "pro", "enterprise"])
    tenant_type: str = Field("brand", description="租户类型", examples=["brand", "agency", "regional_org", "platform"])
    admin_email: str = Field(..., max_length=255, description="管理员邮箱", examples=["admin@example.com"])
    admin_name: str = Field(..., max_length=100, description="管理员姓名", examples=["张三"])
    admin_password: str = Field(..., min_length=8, description="管理员密码", examples=["SecurePass123!"])
    industry: str | None = Field(None, max_length=50, description="行业类别")

    @field_validator("admin_password")
    @classmethod
    def _validate_admin_password(cls, v: str) -> str:
        try:
            validate_password_strength(v)
        except ValueError as e:
            raise ValueError(f"管理员密码不符合要求: {e}") from e
        return v

    notes: str | None = Field(None, max_length=1000, description="备注")
    template_id: int | None = Field(None, description="行业模板 ID，创建后自动应用")

    @field_validator("admin_email")
    @classmethod
    def _normalize_admin_email(cls, value: str) -> str:
        return normalize_email(value)


class TenantUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    tenant_type: str | None = Field(None, description="租户类型")
    industry: str | None = Field(None, max_length=50)
    notes: str | None = Field(None, max_length=1000)
    quota: dict | None = None
    compliance_settings: dict | None = None
    plan_expires_at: datetime | None = None
    onboarding_progress: dict | None = None
    enabled_features: dict | None = None
    categories: list[str] | None = None

    @field_validator("enabled_features")
    @classmethod
    def validate_enabled_features(cls, value: dict | None) -> dict | None:
        if value is None:
            return None
        from app.services.entitlement import validate_feature_flags

        return validate_feature_flags(value)

    @field_validator("quota")
    @classmethod
    def validate_quota(cls, value: dict | None) -> dict | None:
        if value is None:
            return None
        from app.services.quota import validate_quota_config

        return validate_quota_config(value)

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > 100:
            raise ValueError("品类数量不能超过 100 条")
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in v:
            s = item.strip()
            if not s:
                continue
            if len(s) > 20:
                raise ValueError(f"品类名称不能超过 20 个字符: {s}")
            key = s.lower()
            if key not in seen:
                seen.add(key)
                cleaned.append(s)
        return cleaned


class TenantUpdateSelf(BaseModel):
    """租户用户自助更新 — 仅允许非敏感字段。"""

    name: str | None = Field(None, min_length=1, max_length=100)
    industry: str | None = Field(None, max_length=50)
    notes: str | None = Field(None, max_length=1000)
    categories: list[str] | None = None
    onboarding_progress: dict | None = None
    contact_email: EmailStr | None = None
    brand_profile: dict | None = None

    @field_validator("brand_profile")
    @classmethod
    def validate_brand_profile_slots(cls, v: dict | None) -> dict | None:
        """H5 租户定制槽位白名单与主色安全校验（yimatong-z6i0.10）"""
        if v is None:
            return v
        from app.utils.brand_color import validate_brand_profile

        return validate_brand_profile(v)

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > 100:
            raise ValueError("品类数量不能超过 100 条")
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in v:
            s = item.strip()
            if not s:
                continue
            if len(s) > 20:
                raise ValueError(f"品类名称不能超过 20 个字符: {s}")
            key = s.lower()
            if key not in seen:
                seen.add(key)
                cleaned.append(s)
        return cleaned


class TenantRead(BaseModel):
    id: uuid.UUID = Field(..., description="租户 ID")
    name: str = Field(..., description="租户名称")
    slug: str = Field(..., description="租户唯一标识")
    status: str = Field(..., description="租户状态")
    plan: str = Field(..., description="订阅计划")
    tenant_type: str = Field(..., description="租户类型")
    plan_expires_at: datetime | None = Field(None, description="计划过期时间")
    industry: str | None = Field(None, description="行业类别")
    notes: str | None = Field(None, description="备注")
    quota: dict | None = Field(None, description="配额配置")
    compliance_settings: dict | None = Field(None, description="合规设置")
    brand_profile: dict | None = Field(None, description="租户品牌定制槽位（H5 受控定制）")
    onboarding_progress: dict | None = Field(None, description=" onboarding 进度")
    enabled_features: dict | None = Field(None, description="已启用功能")
    categories: list[str] | None = Field(None, description="租户品类配置")
    created_at: datetime | None = Field(None, description="创建时间")

    model_config = {"from_attributes": True}


class CategoriesResponse(BaseModel):
    categories: list[str] = Field(default_factory=list, description="租户品类列表")


class TenantEntitlementRead(BaseModel):
    """当前有效租户的精确只读套餐状态。"""

    tenant_id: uuid.UUID
    plan: str
    plan_expires_at: datetime | None = None
    read_only: bool
    enabled_features: dict[str, bool] = Field(default_factory=dict)


class TenantListItem(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    plan: str
    tenant_type: str = "brand"
    plan_expires_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class OpsTaskCreate(BaseModel):
    tenant_id: uuid.UUID
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    priority: str = "medium"
    due_date: datetime | None = None
    assigned_to: uuid.UUID | None = Field(None, description="负责人 account ID")


class OpsTaskUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    status: str | None = None
    priority: str | None = None
    due_date: datetime | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is not None and v not in ("pending", "in_progress", "completed", "cancelled"):
            raise ValueError(f"Invalid status: {v}")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str | None) -> str | None:
        if v is not None and v not in ("low", "medium", "high"):
            raise ValueError(f"Invalid priority: {v}")
        return v


class OpsTaskRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    description: str | None = None
    status: str
    priority: str
    due_date: datetime | None = None
    assigned_to: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class OpsReadinessSummary(BaseModel):
    ready: bool
    passed_count: int
    total_count: int
    percent: int
    missing_keys: list[str] = Field(default_factory=list)
    missing_labels: list[str] = Field(default_factory=list)


class OpsTaskSummary(BaseModel):
    pending: int = 0
    in_progress: int = 0
    overdue: int = 0
    high_priority: int = 0


class OpsNextAction(BaseModel):
    type: str
    label: str
    href: str
    task_title: str


class OpsWorkbenchSummary(BaseModel):
    total_clients: int = 0
    active_clients: int = 0
    ready_clients: int = 0
    blocked_clients: int = 0
    pending_tasks: int = 0
    in_progress_tasks: int = 0
    overdue_tasks: int = 0


class OpsWorkbenchClient(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    plan: str
    plan_expires_at: datetime | None = None
    created_at: datetime | None = None
    readiness: OpsReadinessSummary
    task_summary: OpsTaskSummary
    next_action: OpsNextAction
    agency_scope: list[str] = Field(default_factory=list)
    full_workbench_access: bool = False


class OpsWorkbenchTask(OpsTaskRead):
    tenant_name: str | None = None
    overdue: bool = False


class OpsWorkbenchResponse(BaseModel):
    summary: OpsWorkbenchSummary
    clients: list[OpsWorkbenchClient]
    tasks: list[OpsWorkbenchTask]
    total: int
    page: int
    page_size: int


# ── 试点聚合（beads: yimatong-bgag.10，PRD §4.5 ops 跨租户聚合板）─────


class PilotMilestoneSummary(BaseModel):
    """单客户的里程碑达成概要。"""

    achieved_count: int
    total: int


class PendingRetrospectiveSummary(BaseModel):
    """单客户的待办复盘概要。"""

    retro_id: uuid.UUID
    period_day: int
    next_review_date: datetime | None = None
    derived_status: str  # pending/completed/overdue/overdue_completed


class PilotClientSummary(BaseModel):
    """聚合板中单客户的试点概要。"""

    client_id: uuid.UUID
    client_name: str | None = None
    client_slug: str | None = None
    milestone_summary: PilotMilestoneSummary
    pending_retrospectives: list[PendingRetrospectiveSummary]
    full_pilot_access: bool = False


class PilotAggregateSummary(BaseModel):
    """聚合板汇总。"""

    total_clients: int
    clients_with_pending_retros: int


class PilotAggregateResponse(BaseModel):
    """代运营/平台的试点跨租户聚合响应。"""

    summary: PilotAggregateSummary
    clients: list[PilotClientSummary]
