import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


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
    notes: str | None = Field(None, max_length=1000, description="备注")
    template_id: int | None = Field(None, description="行业模板 ID，创建后自动应用")


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
    enabled_features: dict | None = None

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
    onboarding_progress: dict | None = Field(None, description=" onboarding 进度")
    enabled_features: dict | None = Field(None, description="已启用功能")
    categories: list[str] | None = Field(None, description="租户品类配置")
    created_at: datetime | None = Field(None, description="创建时间")

    model_config = {"from_attributes": True}


class CategoriesResponse(BaseModel):
    categories: list[str] = Field(default_factory=list, description="租户品类列表")


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
