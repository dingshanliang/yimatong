# 代运营工作台 UX 改进 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复代运营工作台的 3 个 Critical 安全/功能问题、7 个 Moderate 体验问题，补齐行业/备注/负责人等业务字段，接入已有行业模板系统，将用户旅程评分从 3.2 提升到 4.0+。

**Architecture:** 分 5 个 Wave 推进。Wave 1 修复安全漏洞 + 数据模型扩展（数据库迁移），Wave 2 修复后端性能 N+1 查询，Wave 3 重构初始化客户向导（接入真实行业模板、汇总预览、密码复制），Wave 4 修复前端核心体验（下一步跳转、任务筛选、客户选择器），Wave 5 打磨细节（检查清单、统计卡片、到期提醒）。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic（后端）、Next.js 16 + Ant Design 6 + Zustand + SWR（前端）、pytest + httpx（后端测试）、Vitest + Testing Library（前端测试）

---

## File Structure

### 后端修改
| 文件 | 操作 | 职责 |
|------|------|------|
| `backend/alembic/versions/xxx_add_tenant_industry_notes_ops_assigned.py` | Create | 新增 industry, notes 到 tenants；assigned_to 到 ops_tasks |
| `backend/app/models/tenant.py` | Modify | Tenant 增加 industry, notes 字段；OpsTask 增加 assigned_to 字段 |
| `backend/app/core/dependencies.py` | Modify | 新增 get_ops_user 依赖（认证 + 角色检查） |
| `backend/app/api/v1/ops.py` | Modify | 所有端点添加 get_ops_user 依赖 |
| `backend/app/schemas/tenant.py` | Modify | TenantCreate/Read/Update 增加 industry, notes, template_id；OpsTaskCreate/Read 增加 assigned_to |
| `backend/app/services/tenant.py` | Modify | create_tenant 接收 industry/notes/template_id；不再从前端接收 slug |
| `backend/app/services/ops.py` | Modify | 批量查询替代 N+1；assigned_to 传递 |
| `backend/tests/test_api/test_ops_auth.py` | Create | ops 端点认证和权限测试 |
| `backend/tests/test_services/test_tenant_create.py` | Create | 租户创建含模板应用的测试 |

### 前端修改
| 文件 | 操作 | 职责 |
|------|------|------|
| `frontend/apps/admin/src/app/(dashboard)/agency/_components/types.ts` | Modify | 新增 industry, notes, assigned_to 类型 |
| `frontend/apps/admin/src/app/(dashboard)/agency/_components/InitClientModal.tsx` | Modify | 接入真实行业模板、添加汇总预览步骤、密码复制、备注字段、移除假模板 |
| `frontend/apps/admin/src/app/(dashboard)/agency/_components/ClientTable.tsx` | Modify | "下一步"改为 router.push 跳转 |
| `frontend/apps/admin/src/app/(dashboard)/agency/_components/TaskTable.tsx` | Modify | 添加筛选、描述展示、负责人列 |
| `frontend/apps/admin/src/app/(dashboard)/agency/_components/TaskModals.tsx` | Modify | 客户选择器改用独立 API；添加描述输入；检查清单改为图标 + 重试按钮 |
| `frontend/apps/admin/src/app/(dashboard)/agency/_components/StatsCards.tsx` | Modify | 卡片可点击跳转筛选 |
| `frontend/apps/admin/src/app/(dashboard)/agency/page.tsx` | Modify | 到期提醒 Alert、传递 assigned_to、客户端独立搜索 |

---

## Wave 1: Security & Data Foundation

### Task 1: 数据库迁移 — Tenant 增加 industry/notes，OpsTask 增加 assigned_to

**Files:**
- Create: `backend/alembic/versions/xxx_add_tenant_fields_and_ops_assigned.py`
- Modify: `backend/app/models/tenant.py:40-56` (Tenant class), `backend/app/models/tenant.py:125-147` (OpsTask class)

- [ ] **Step 1: 在 Tenant 模型中添加 industry 和 notes 字段**

在 `backend/app/models/tenant.py` 的 `Tenant` 类中，`plan_expires_at` 字段之后添加：

```python
# backend/app/models/tenant.py — Tenant class, after plan_expires_at field
industry: Mapped[str | None] = mapped_column(String(50), nullable=True)
notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
```

- [ ] **Step 2: 在 OpsTask 模型中添加 assigned_to 字段**

在 `backend/app/models/tenant.py` 的 `OpsTask` 类中，`tenant_id` 字段之后添加：

```python
# backend/app/models/tenant.py — OpsTask class, after tenant_id field
assigned_to: Mapped[uuid.UUID | None] = mapped_column(
    ForeignKey("accounts.id"), nullable=True, index=True
)
```

- [ ] **Step 3: 生成迁移文件**

```bash
cd backend && source .venv/bin/activate
alembic revision --autogenerate -m "add tenant industry notes and ops task assigned_to"
```

- [ ] **Step 4: 检查生成的迁移文件，确认包含三列**

打开生成的迁移文件，确认 `upgrade()` 包含：
- `op.add_column('tenants', sa.Column('industry', sa.String(50), nullable=True))`
- `op.add_column('tenants', sa.Column('notes', sa.Text(), nullable=True))`（Alembic 可能将 String(1000) 映射为 Text）
- `op.add_column('ops_tasks', sa.Column('assigned_to', sa.UUID(), nullable=True))`
- `op.create_foreign_key(...)` 连接到 accounts 表

如有遗漏，手动补充。

- [ ] **Step 5: 运行迁移**

```bash
alembic upgrade head
```

Expected: 无错误输出

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/tenant.py backend/alembic/versions/
git commit -m "feat(models): add industry, notes to Tenant; assigned_to to OpsTask"
```

---

### Task 2: 后端认证 — 新增 get_ops_user 依赖，保护 ops_router 所有端点

**Files:**
- Modify: `backend/app/core/dependencies.py`
- Modify: `backend/app/api/v1/ops.py`

- [ ] **Step 1: 写测试 — ops 端点无 token 返回 401**

创建 `backend/tests/test_api/test_ops_auth.py`：

```python
"""代运营工作台 API 认证测试"""
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.fixture
def transport():
    return ASGITransport(app=app)


@pytest.mark.asyncio
async def test_ops_workbench_requires_auth(transport):
    """无 token 访问 workbench 应返回 401"""
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/ops/workbench")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ops_create_task_requires_auth(transport):
    """无 token 创建任务应返回 401"""
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/ops/tasks", json={"tenant_id": "00000000-0000-0000-0000-000000000000", "title": "test"})
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ops_overview_requires_auth(transport):
    """无 token 访问 overview 应返回 401"""
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/ops/overview")
        assert resp.status_code == 401
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_api/test_ops_auth.py -v
```

Expected: 所有测试 FAIL（当前 ops 端点无认证依赖，返回 200 而非 401，或者因 DB 查询失败但不是 401）

- [ ] **Step 3: 在 dependencies.py 中新增 get_ops_user 依赖**

在 `backend/app/core/dependencies.py` 末尾添加：

```python
ALLOWED_OPS_ROLES = {"platform_admin", "operator"}


async def get_ops_user(
    account_id: uuid.UUID = Depends(get_current_account_id),
    role: str = Depends(get_current_role),
) -> tuple[uuid.UUID, str]:
    """验证当前用户已认证且具有代运营工作台访问权限。"""
    if role not in ALLOWED_OPS_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"Role '{role}' is not allowed to access operations workbench",
        )
    return account_id, role
```

注意：文件顶部已有的 `Depends` import 需确认存在，如果只有 `HTTPException` 和 `Request`，需添加：

```python
from fastapi import Depends, HTTPException, Request
```

- [ ] **Step 4: 为 ops_router 所有端点添加 get_ops_user 依赖**

修改 `backend/app/api/v1/ops.py`，将所有端点的参数列表中添加依赖：

```python
# 文件顶部 import 新增
from app.core.dependencies import get_ops_user

# get_ops_overview — 添加依赖
@ops_router.get("/overview", summary="代运营工作台概览")
async def get_ops_overview(
    db: AsyncSession = Depends(get_db),
    _ops_user: tuple = Depends(get_ops_user),
):

# get_ops_workbench_endpoint — 添加依赖
@ops_router.get("/workbench", response_model=OpsWorkbenchResponse, summary="代运营工作台聚合")
async def get_ops_workbench_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, description="搜索客户名称"),
    readiness: str = Query("all", pattern="^(all|ready|blocked)$"),
    task_status: str = Query("all", pattern="^(all|pending|in_progress|overdue)$"),
    db: AsyncSession = Depends(get_db),
    _ops_user: tuple = Depends(get_ops_user),
):

# get_client_status — 将 get_current_tenant 替换为 get_ops_user
@ops_router.get("/clients/{tenant_id}/status")
async def get_client_status(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _ops_user: tuple = Depends(get_ops_user),
):

# get_launch_checklist_endpoint — 将 get_current_tenant 替换为 get_ops_user
@ops_router.get("/clients/{tenant_id}/launch-checklist", summary="获取 launch checklist")
async def get_launch_checklist_endpoint(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _ops_user: tuple = Depends(get_ops_user),
):

# create_task_endpoint — 添加依赖
@ops_router.post("/tasks", response_model=OpsTaskRead, status_code=201, summary="创建任务")
async def create_task_endpoint(
    body: OpsTaskCreate,
    db: AsyncSession = Depends(get_db),
    _ops_user: tuple = Depends(get_ops_user),
):

# list_tasks_endpoint — 添加依赖
@ops_router.get("/tasks", response_model=PaginatedResponse, summary="任务列表")
async def list_tasks_endpoint(
    tenant_id: uuid.UUID | None = Query(None, description="按客户筛选"),
    status: str | None = Query(None, description="按状态筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _ops_user: tuple = Depends(get_ops_user),
):

# get_task_endpoint — 添加依赖
@ops_router.get("/tasks/{task_id}", response_model=OpsTaskRead, summary="获取任务")
async def get_task_endpoint(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _ops_user: tuple = Depends(get_ops_user),
):

# update_task_endpoint — 添加依赖
@ops_router.patch("/tasks/{task_id}", response_model=OpsTaskRead, summary="更新任务")
async def update_task_endpoint(
    task_id: uuid.UUID,
    body: OpsTaskUpdate,
    db: AsyncSession = Depends(get_db),
    _ops_user: tuple = Depends(get_ops_user),
):

# delete_task_endpoint — 添加依赖
@ops_router.delete("/tasks/{task_id}", status_code=204, summary="删除任务")
async def delete_task_endpoint(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _ops_user: tuple = Depends(get_ops_user),
):
```

- [ ] **Step 5: 运行测试确认通过**

```bash
python -m pytest tests/test_api/test_ops_auth.py -v
```

Expected: 所有测试 PASS（返回 401）

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/dependencies.py backend/app/api/v1/ops.py backend/tests/test_api/test_ops_auth.py
git commit -m "fix(ops): add authentication and role check to all ops endpoints"
```

---

### Task 3: 后端 Schema & Service 更新 — industry/notes/template_id、slug 修复、assigned_to

**Files:**
- Modify: `backend/app/schemas/tenant.py`
- Modify: `backend/app/services/tenant.py`
- Modify: `backend/app/services/ops.py`

- [ ] **Step 1: 更新 TenantCreate schema — 添加 industry, notes, template_id，slug 改为可选且不 required**

修改 `backend/app/schemas/tenant.py` 的 `TenantCreate`：

```python
class TenantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="租户名称", examples=["示例食品公司"])
    slug: str | None = Field(
        None,
        min_length=1,
        max_length=50,
        pattern=r"^[a-z0-9-]+$",
        description="租户唯一标识（URL 友好），为空时自动从名称生成",
    )
    plan: str = Field("free", description="订阅计划")
    admin_email: str = Field(..., max_length=255, description="管理员邮箱")
    admin_name: str = Field(..., max_length=100, description="管理员姓名")
    admin_password: str = Field(..., min_length=8, description="管理员密码")
    industry: str | None = Field(None, max_length=50, description="行业类别")
    notes: str | None = Field(None, max_length=1000, description="备注")
    template_id: int | None = Field(None, description="行业模板 ID，创建后自动应用")
```

- [ ] **Step 2: 更新 TenantRead/TenantUpdate schema**

修改 `TenantRead`：

```python
class TenantRead(BaseModel):
    id: uuid.UUID = Field(..., description="租户 ID")
    name: str = Field(..., description="租户名称")
    slug: str = Field(..., description="租户唯一标识")
    status: str = Field(..., description="租户状态")
    plan: str = Field(..., description="订阅计划")
    plan_expires_at: datetime | None = Field(None, description="计划过期时间")
    industry: str | None = Field(None, description="行业类别")
    notes: str | None = Field(None, description="备注")
    quota: dict | None = Field(None, description="配额配置")
    compliance_settings: dict | None = Field(None, description="合规设置")
    onboarding_progress: dict | None = Field(None, description="onboarding 进度")
    enabled_features: dict | None = Field(None, description="已启用功能")
    created_at: datetime | None = Field(None, description="创建时间")

    model_config = {"from_attributes": True}
```

修改 `TenantUpdate`：

```python
class TenantUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    industry: str | None = Field(None, max_length=50)
    notes: str | None = Field(None, max_length=1000)
    quota: dict | None = None
    compliance_settings: dict | None = None
    plan_expires_at: datetime | None = None
    onboarding_progress: dict | None = None
    enabled_features: dict | None = None
```

- [ ] **Step 3: 更新 OpsTaskCreate/OpsTaskRead — 添加 assigned_to**

修改 `OpsTaskCreate`：

```python
class OpsTaskCreate(BaseModel):
    tenant_id: uuid.UUID
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    priority: str = "medium"
    due_date: datetime | None = None
    assigned_to: uuid.UUID | None = Field(None, description="负责人 account ID")
```

修改 `OpsTaskRead`：

```python
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
```

- [ ] **Step 4: 更新 create_tenant service — 接收 industry/notes，应用模板**

修改 `backend/app/services/tenant.py`：

```python
async def create_tenant(
    db: AsyncSession,
    name: str,
    slug: str | None,
    plan: str,
    admin_email: str,
    admin_name: str,
    admin_password: str,
    industry: str | None = None,
    notes: str | None = None,
    template_id: int | None = None,
) -> Tenant:
    if not slug:
        slug = _generate_slug(name)

    tenant = Tenant(
        name=name,
        slug=slug,
        status=TenantStatus.active,
        plan=TenantPlan(plan),
        industry=industry,
        notes=notes,
        quota={"max_codes": 10000, "max_campaigns": 50, "max_accounts": 10},
    )
    db.add(tenant)
    await db.flush()

    org = Organization(tenant_id=tenant.id, name=f"{name} 默认组织")
    db.add(org)
    await db.flush()

    hashed = hash_password(admin_password)
    account = Account(
        tenant_id=tenant.id,
        organization_id=org.id,
        email=admin_email,
        hashed_password=hashed,
        name=admin_name,
    )
    db.add(account)
    await db.flush()

    # 应用行业模板（如果指定）
    if template_id is not None:
        from app.services.industry_templates import ALL_TEMPLATES
        from app.models.page import PageTemplate, PageVersion, PageVersionStatus

        if 0 <= template_id < len(ALL_TEMPLATES):
            template_def = ALL_TEMPLATES[template_id]
            tmpl = PageTemplate(
                tenant_id=tenant.id,
                name=template_def["name"],
                template_type=template_def["template_type"],
                status="draft",
            )
            db.add(tmpl)
            await db.flush()

            version = PageVersion(
                tenant_id=tenant.id,
                page_template_id=tmpl.id,
                version_number=1,
                config_json=template_def["config_json"],
                status=PageVersionStatus.draft,
            )
            db.add(version)
            await db.flush()

    await db.flush()
    await db.refresh(tenant)
    return tenant
```

- [ ] **Step 5: 更新 tenants API — create_tenant_endpoint 传递新字段**

修改 `backend/app/api/v1/tenants.py` 的 `create_tenant_endpoint`：

```python
@router.post(
    "",
    response_model=TenantRead,
    status_code=201,
    summary="创建租户",
    response_description="租户创建成功",
)
async def create_tenant_endpoint(body: TenantCreate, db: AsyncSession = Depends(get_db)):
    tenant = await create_tenant(
        db=db,
        name=body.name,
        slug=body.slug,
        plan=body.plan,
        admin_email=body.admin_email,
        admin_name=body.admin_name,
        admin_password=body.admin_password,
        industry=body.industry,
        notes=body.notes,
        template_id=body.template_id,
    )
    return tenant
```

- [ ] **Step 6: 更新 update_tenant service — 支持 industry/notes**

修改 `backend/app/services/tenant.py` 的 `update_tenant` 函数签名和逻辑：

```python
async def update_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str | None = None,
    industry: str | None = None,
    notes: str | None = None,
    quota: dict | None = None,
    compliance_settings: dict | None = None,
    plan_expires_at: datetime | None = None,
    onboarding_progress: dict | None = None,
    enabled_features: dict | None = None,
) -> Tenant | None:
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        return None
    if name:
        tenant.name = name
    if industry is not None:
        tenant.industry = industry
    if notes is not None:
        tenant.notes = notes
    if quota is not None:
        tenant.quota = quota
    if compliance_settings is not None:
        existing = tenant.compliance_settings or {}
        existing.update(compliance_settings)
        tenant.compliance_settings = existing
    if plan_expires_at is not None:
        tenant.plan_expires_at = plan_expires_at
    if onboarding_progress is not None:
        tenant.onboarding_progress = onboarding_progress
    if enabled_features is not None:
        tenant.enabled_features = enabled_features
    await db.flush()
    await db.refresh(tenant)
    return tenant
```

更新 `tenants.py` 中两个调用 `update_tenant` 的端点，传递新参数：

```python
# update_current_tenant_endpoint
tenant = await update_tenant(
    db, tenant_id,
    name=body.name, industry=body.industry, notes=body.notes,
    quota=body.quota, compliance_settings=body.compliance_settings,
    plan_expires_at=body.plan_expires_at,
    onboarding_progress=body.onboarding_progress,
    enabled_features=body.enabled_features,
)

# update_tenant_endpoint (同上)
```

- [ ] **Step 7: 更新 create_task_endpoint — 传递 assigned_to**

修改 `backend/app/api/v1/ops.py` 的 `create_task_endpoint`：

```python
@ops_router.post("/tasks", response_model=OpsTaskRead, status_code=201, summary="创建任务")
async def create_task_endpoint(
    body: OpsTaskCreate,
    db: AsyncSession = Depends(get_db),
    _ops_user: tuple = Depends(get_ops_user),
):
    task = OpsTask(
        tenant_id=body.tenant_id,
        title=body.title,
        description=body.description,
        priority=OpsTaskPriority(body.priority) if body.priority else OpsTaskPriority.medium,
        due_date=body.due_date,
        assigned_to=body.assigned_to,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task
```

- [ ] **Step 8: 运行现有测试确保无回归**

```bash
python -m pytest tests/ -v --timeout=30
```

Expected: 所有现有测试通过

- [ ] **Step 9: Commit**

```bash
git add backend/app/schemas/tenant.py backend/app/services/tenant.py backend/app/api/v1/tenants.py backend/app/api/v1/ops.py
git commit -m "feat(tenant,ops): add industry/notes/template_id to tenant creation, assigned_to to ops tasks"
```

---

## Wave 2: Backend Performance

### Task 4: 批量查询替代 N+1 — get_ops_workbench 性能优化

**Files:**
- Modify: `backend/app/services/ops.py:204-340`

- [ ] **Step 1: 写测试 — 验证 workbench 返回正确的 readiness 数据**

创建 `backend/tests/test_services/test_ops_workbench.py`：

```python
"""代运营工作台服务层测试"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.ops import build_readiness_summary, build_next_action


def test_build_readiness_summary_all_passed():
    status = {"brands": 2, "products": 1, "published_pages": 1, "activated_batches": 1}
    result = build_readiness_summary(status)
    assert result["ready"] is True
    assert result["passed_count"] == 4
    assert result["total_count"] == 4
    assert result["percent"] == 100
    assert result["missing_keys"] == []
    assert result["missing_labels"] == []


def test_build_readiness_summary_partial():
    status = {"brands": 1, "products": 0, "published_pages": 1, "activated_batches": 0}
    result = build_readiness_summary(status)
    assert result["ready"] is False
    assert result["passed_count"] == 2
    assert result["total_count"] == 4
    assert result["percent"] == 50
    assert "product_created" in result["missing_keys"]
    assert "code_batch_activated" in result["missing_keys"]


def test_build_next_action_overdue_first():
    readiness = {"ready": True, "missing_keys": []}
    task_summary = {"overdue": 2, "pending": 5, "in_progress": 1, "high_priority": 0}
    result = build_next_action("测试客户", readiness, task_summary)
    assert result["type"] == "overdue_task"
    assert "逾期" in result["label"]


def test_build_next_action_missing_config():
    readiness = {"ready": False, "missing_keys": ["brand_configured"]}
    task_summary = {"overdue": 0, "pending": 0, "in_progress": 0, "high_priority": 0}
    result = build_next_action("测试客户", readiness, task_summary)
    assert result["type"] == "brand_configured"
    assert result["href"] == "/brands"


def test_build_next_action_all_good():
    readiness = {"ready": True, "missing_keys": []}
    task_summary = {"overdue": 0, "pending": 0, "in_progress": 0, "high_priority": 0}
    result = build_next_action("测试客户", readiness, task_summary)
    assert result["type"] == "checklist"
```

- [ ] **Step 2: 运行测试确认通过**

```bash
python -m pytest tests/test_services/test_ops_workbench.py -v
```

Expected: PASS（纯函数测试，不依赖 DB）

- [ ] **Step 3: 重构 get_ops_workbench — 批量查询**

在 `backend/app/services/ops.py` 中替换 `get_ops_workbench` 函数，将 N+1 查询改为批量聚合：

```python
async def get_ops_workbench(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    readiness: str = "all",
    task_status: str = "all",
) -> dict:
    from sqlalchemy import case, literal_column

    # 1. 查询租户（带搜索和分页）
    tenant_query = select(Tenant).where(Tenant.status != TenantStatus.terminated)
    count_query = select(func.count()).select_from(Tenant).where(Tenant.status != TenantStatus.terminated)

    if q:
        escaped = escape_like_pattern(q)
        tenant_query = tenant_query.where(Tenant.name.ilike(f"%{escaped}%", escape="\\"))
        count_query = count_query.where(Tenant.name.ilike(f"%{escaped}%", escape="\\"))

    total = (await db.execute(count_query)).scalar() or 0
    tenant_query = tenant_query.order_by(Tenant.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    tenants = list((await db.execute(tenant_query)).scalars().all())
    tenant_ids = [tenant.id for tenant in tenants]

    if not tenant_ids:
        return {
            "summary": {"total_clients": total, "active_clients": 0, "ready_clients": 0,
                        "blocked_clients": 0, "pending_tasks": 0, "in_progress_tasks": 0, "overdue_tasks": 0},
            "clients": [], "tasks": [], "total": total, "page": page, "page_size": page_size,
        }

    # 2. 批量查询统计数据（替代 N+1）
    brand_counts = dict(
        (await db.execute(
            select(Brand.tenant_id, func.count()).group_by(Brand.tenant_id).where(Brand.tenant_id.in_(tenant_ids))
        )).all()
    )
    product_counts = dict(
        (await db.execute(
            select(Product.tenant_id, func.count()).group_by(Product.tenant_id).where(Product.tenant_id.in_(tenant_ids))
        )).all()
    )
    published_page_counts = dict(
        (await db.execute(
            select(PageVersion.tenant_id, func.count()).group_by(PageVersion.tenant_id)
            .where(PageVersion.tenant_id.in_(tenant_ids), PageVersion.status == PageVersionStatus.published)
        )).all()
    )

    from app.models.code import CodeBatch, CodeBatchStatus
    activated_batch_counts = dict(
        (await db.execute(
            select(CodeBatch.tenant_id, func.count()).group_by(CodeBatch.tenant_id)
            .where(CodeBatch.tenant_id.in_(tenant_ids), CodeBatch.status == CodeBatchStatus.completed)
        )).all()
    )

    active_campaign_counts = dict(
        (await db.execute(
            select(Campaign.tenant_id, func.count()).group_by(Campaign.tenant_id)
            .where(Campaign.tenant_id.in_(tenant_ids), Campaign.status == CampaignStatus.ACTIVE)
        )).all()
    )

    # 3. 批量查询任务
    task_rows: list[OpsTask] = []
    if tenant_ids:
        task_result = await db.execute(
            select(OpsTask).where(OpsTask.tenant_id.in_(tenant_ids)).order_by(OpsTask.created_at.desc())
        )
        task_rows = list(task_result.scalars().all())

    tasks_by_tenant: dict[uuid.UUID, list[OpsTask]] = {tid: [] for tid in tenant_ids}
    for task in task_rows:
        tasks_by_tenant.setdefault(task.tenant_id, []).append(task)

    now = datetime.now(UTC)
    clients = []
    ready_clients = 0
    blocked_clients = 0
    pending_tasks = 0
    in_progress_tasks = 0
    overdue_tasks = 0

    for tenant in tenants:
        status = {
            "tenant_id": str(tenant.id),
            "brands": brand_counts.get(tenant.id, 0),
            "products": product_counts.get(tenant.id, 0),
            "published_pages": published_page_counts.get(tenant.id, 0),
            "activated_batches": activated_batch_counts.get(tenant.id, 0),
            "active_campaigns": active_campaign_counts.get(tenant.id, 0),
            "onboarding_progress": tenant.onboarding_progress,
        }
        readiness_summary = build_readiness_summary(status)
        tenant_tasks = tasks_by_tenant.get(tenant.id, [])
        task_summary = {"pending": 0, "in_progress": 0, "overdue": 0, "high_priority": 0}

        for task in tenant_tasks:
            is_open = task.status in (OpsTaskStatus.pending, OpsTaskStatus.in_progress)
            if task.status == OpsTaskStatus.pending:
                task_summary["pending"] += 1
                pending_tasks += 1
            if task.status == OpsTaskStatus.in_progress:
                task_summary["in_progress"] += 1
                in_progress_tasks += 1
            if _is_task_overdue(task, now):
                task_summary["overdue"] += 1
                overdue_tasks += 1
            if task.priority == OpsTaskPriority.high and is_open:
                task_summary["high_priority"] += 1

        if readiness_summary["ready"]:
            ready_clients += 1
        else:
            blocked_clients += 1

        if readiness == "ready" and not readiness_summary["ready"]:
            continue
        if readiness == "blocked" and readiness_summary["ready"]:
            continue
        if task_status != "all" and task_summary.get(task_status, 0) == 0:
            continue

        clients.append({
            "id": tenant.id,
            "name": tenant.name,
            "status": tenant.status.value if hasattr(tenant.status, "value") else str(tenant.status),
            "plan": tenant.plan.value if hasattr(tenant.plan, "value") else str(tenant.plan),
            "plan_expires_at": tenant.plan_expires_at,
            "industry": tenant.industry,
            "created_at": tenant.created_at,
            "readiness": readiness_summary,
            "task_summary": task_summary,
            "next_action": build_next_action(tenant.name, readiness_summary, task_summary),
        })

    # 4. 构建可操作任务列表
    actionable_tasks = []
    for task in task_rows:
        is_open = task.status in (OpsTaskStatus.pending, OpsTaskStatus.in_progress)
        if not is_open:
            continue
        tenant = next((t for t in tenants if t.id == task.tenant_id), None)
        actionable_tasks.append({
            "id": task.id,
            "tenant_id": task.tenant_id,
            "tenant_name": tenant.name if tenant else None,
            "title": task.title,
            "description": task.description,
            "status": task.status.value if hasattr(task.status, "value") else str(task.status),
            "priority": task.priority.value if hasattr(task.priority, "value") else str(task.priority),
            "due_date": task.due_date,
            "assigned_to": task.assigned_to,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "overdue": _is_task_overdue(task, now),
        })

    actionable_tasks.sort(
        key=lambda item: (
            not item["overdue"],
            item["priority"] != OpsTaskPriority.high.value,
            _as_aware_utc(item["due_date"]) if item["due_date"] else datetime.max.replace(tzinfo=UTC),
        )
    )

    return {
        "summary": {
            "total_clients": total,
            "active_clients": sum(1 for t in tenants if t.status == TenantStatus.active),
            "ready_clients": ready_clients,
            "blocked_clients": blocked_clients,
            "pending_tasks": pending_tasks,
            "in_progress_tasks": in_progress_tasks,
            "overdue_tasks": overdue_tasks,
        },
        "clients": clients,
        "tasks": actionable_tasks,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
```

- [ ] **Step 4: 运行全部测试确认无回归**

```bash
python -m pytest tests/ -v --timeout=30
```

Expected: 全部通过

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ops.py backend/tests/test_services/test_ops_workbench.py
git commit -m "perf(ops): replace N+1 queries with batch aggregation in workbench"
```

---

## Wave 3: Frontend — 初始化客户向导重构

### Task 5: 前端 types.ts 更新 — 新增字段类型

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/types.ts`

- [ ] **Step 1: 更新类型定义**

在 `types.ts` 中修改以下接口：

```typescript
// Client 接口新增 industry
export interface Client {
  id: string;
  name: string;
  status: string;
  plan: string;
  industry?: string | null;
  plan_expires_at: string | null;
  created_at: string;
}

// Task 接口新增 assigned_to
export interface Task {
  id: string;
  tenant_id: string;
  tenant_name?: string;
  title: string;
  status: string;
  priority: string;
  due_date: string | null;
  assigned_to?: string | null;
}

// WorkbenchTask 新增 assigned_to（继承自 Task，已覆盖）
// 无需额外修改，因为 Task 已包含 assigned_to

// WorkbenchSummary 保持不变

// 新增：行业模板类型
export interface IndustryTemplate {
  id: number;
  name: string;
  template_type: string;
  description: string;
}

// OpsWorkbenchClient 已在 AgencyClientRow 中定义
// 无需额外修改
```

- [ ] **Step 2: Commit**

```bash
git add frontend/apps/admin/src/app/(dashboard)/agency/_components/types.ts
git commit -m "feat(agency): add industry, assigned_to, template types"
```

---

### Task 6: InitClientModal 重构 — 真实模板、汇总预览、密码复制、备注、slug 修复

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/InitClientModal.tsx`

- [ ] **Step 1: 重写 InitClientModal 完整实现**

替换 `InitClientModal.tsx` 全部内容：

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, App, Button, Descriptions, Form, Input, Modal, Select, Space, Steps, Typography } from "antd";
import { CopyOutlined } from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";
import type { IndustryTemplate } from "./types";

interface InitClientModalProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

const INDUSTRY_OPTIONS = [
  { value: "food", label: "食品" },
  { value: "agriculture", label: "农产品" },
  { value: "beverage", label: "饮料" },
  { value: "daily", label: "日用品" },
  { value: "other", label: "其他" },
];

const PLAN_OPTIONS = [
  { value: "free", label: "免费版" },
  { value: "starter", label: "入门版" },
  { value: "pro", label: "专业版" },
  { value: "enterprise", label: "企业版" },
];

function getStepFields(step: number): string[] {
  switch (step) {
    case 0: return ["client_name", "contact_name", "contact_phone"];
    case 1: return ["brand_name"];
    case 2: return [];
    default: return [];
  }
}

export function InitClientModal({ open, onClose, onSuccess }: InitClientModalProps) {
  const { message, modal } = App.useApp();
  const [form] = Form.useForm();
  const [currentStep, setCurrentStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const [credentials, setCredentials] = useState<{ email: string; password: string } | null>(null);
  const [templates, setTemplates] = useState<IndustryTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [passwordCopied, setPasswordCopied] = useState(false);

  // 加载行业模板列表
  useEffect(() => {
    if (!open) return;
    setTemplatesLoading(true);
    api.get("/industry-templates")
      .then(({ data }) => setTemplates(Array.isArray(data) ? data : []))
      .catch(() => setTemplates([]))
      .finally(() => setTemplatesLoading(false));
  }, [open]);

  const generatePassword = () => `Ymt-${Math.random().toString(36).slice(2, 8)}${Date.now().toString().slice(-6)}`;

  const handleNext = async () => {
    if (currentStep < 3) {
      try {
        const fields = getStepFields(currentStep);
        if (fields.length > 0) await form.validateFields(fields);
        setCurrentStep(currentStep + 1);
      } catch { /* validation */ }
    }
  };

  const handlePrev = () => { if (currentStep > 0) setCurrentStep(currentStep - 1); };

  const handleCopyPassword = useCallback(async () => {
    if (!credentials) return;
    try {
      await navigator.clipboard.writeText(credentials.password);
      setPasswordCopied(true);
      message.success("密码已复制到剪贴板");
    } catch {
      message.error("复制失败，请手动选中复制");
    }
  }, [credentials, message]);

  const handleFinish = async () => {
    setSaving(true);
    try {
      const values = form.getFieldsValue(true);
      const initialPassword = generatePassword();
      const { data } = await api.post("/tenants", {
        name: values.client_name,
        // 不传 slug，让后端从 name 自动生成
        plan: values.plan || "free",
        admin_email: values.contact_email || `${values.client_name?.replace(/\s+/g, "").toLowerCase()}@example.com`,
        admin_name: values.contact_name || "管理员",
        admin_password: initialPassword,
        industry: values.industry,
        notes: values.notes,
        template_id: values.template_id ?? null,
      });
      setCredentials({
        email: data?.admin_email || values.contact_email || `${values.client_name?.replace(/\s+/g, "").toLowerCase()}@example.com`,
        password: data?.initial_password || initialPassword,
      });
      message.success("客户初始化成功");
      onSuccess();
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, "初始化失败"));
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    if (credentials && !passwordCopied) {
      // 密码未复制，弹确认框
      modal.confirm({
        title: "确认关闭？",
        content: "管理员密码尚未复制，关闭后将无法再次查看。请确认已妥善保存密码。",
        okText: "确认关闭",
        okButtonProps: { danger: true },
        onOk: () => resetAndClose(),
      });
      return;
    }
    resetAndClose();
  };

  const resetAndClose = () => {
    setCurrentStep(0);
    setCredentials(null);
    setPasswordCopied(false);
    form.resetFields();
    onClose();
  };

  const values = form.getFieldsValue(true);

  const stepContent = [
    // Step 1: 基础信息
    <div key="step1">
      <Form form={form} layout="vertical">
        <Form.Item name="client_name" label="客户名称" rules={[{ required: true, message: "请输入客户名称" }]}>
          <Input placeholder="客户公司名称" />
        </Form.Item>
        <Form.Item name="contact_name" label="联系人" rules={[{ required: true, message: "请输入联系人" }]}>
          <Input placeholder="联系人姓名" />
        </Form.Item>
        <Form.Item name="contact_phone" label="联系电话" rules={[{ required: true, message: "请输入联系电话" }]}>
          <Input placeholder="联系电话" />
        </Form.Item>
        <Form.Item name="contact_email" label="联系邮箱">
          <Input placeholder="联系邮箱" />
        </Form.Item>
        <Form.Item name="industry" label="所属行业">
          <Select placeholder="选择行业" options={INDUSTRY_OPTIONS} />
        </Form.Item>
        <Form.Item name="plan" label="套餐">
          <Select placeholder="选择套餐" options={PLAN_OPTIONS} />
        </Form.Item>
      </Form>
    </div>,

    // Step 2: 产品与模板配置
    <div key="step2">
      <Form form={form} layout="vertical">
        <Form.Item name="brand_name" label="品牌名称" rules={[{ required: true, message: "请输入品牌名称" }]}>
          <Input placeholder="主品牌名称" />
        </Form.Item>
        <Form.Item name="template_id" label="扫码页模板">
          <Select
            placeholder="选择行业模板（可选）"
            loading={templatesLoading}
            allowClear
            options={templates.map((t) => ({ value: t.id, label: `${t.name} — ${t.description}` }))}
          />
        </Form.Item>
      </Form>
    </div>,

    // Step 3: 备注
    <div key="step3">
      <Form form={form} layout="vertical">
        <Form.Item name="notes" label="备注">
          <Input.TextArea rows={4} placeholder="记录客户特殊需求、上线时间要求等" />
        </Form.Item>
      </Form>
    </div>,

    // Step 4: 确认提交 + 凭证展示
    <div key="step4" className="py-4">
      {credentials ? (
        <div>
          <Alert
            className="mb-4"
            data-testid="agency-init-credentials"
            type="success"
            showIcon
            message="客户管理员账号已生成"
            description={
              <Space direction="vertical" size={4}>
                <span>登录邮箱：{credentials.email}</span>
                <span>
                  临时密码：{credentials.password}
                  <Button size="small" type="link" icon={<CopyOutlined />} onClick={handleCopyPassword}>
                    {passwordCopied ? "已复制" : "复制密码"}
                  </Button>
                </span>
              </Space>
            }
          />
          <Typography.Text type="secondary">请将登录信息发送给客户，客户首次登录后建议修改密码。</Typography.Text>
        </div>
      ) : (
        <Descriptions column={1} size="small" bordered title="配置确认">
          <Descriptions.Item label="客户名称">{values.client_name || "—"}</Descriptions.Item>
          <Descriptions.Item label="联系人">{values.contact_name || "—"}</Descriptions.Item>
          <Descriptions.Item label="联系电话">{values.contact_phone || "—"}</Descriptions.Item>
          <Descriptions.Item label="联系邮箱">{values.contact_email || "—"}</Descriptions.Item>
          <Descriptions.Item label="行业">{INDUSTRY_OPTIONS.find((o) => o.value === values.industry)?.label || "未选择"}</Descriptions.Item>
          <Descriptions.Item label="套餐">{PLAN_OPTIONS.find((o) => o.value === values.plan)?.label || "免费版"}</Descriptions.Item>
          <Descriptions.Item label="品牌名称">{values.brand_name || "—"}</Descriptions.Item>
          <Descriptions.Item label="扫码页模板">
            {values.template_id != null ? templates.find((t) => t.id === values.template_id)?.name || "未选择" : "未选择"}
          </Descriptions.Item>
          {values.notes && <Descriptions.Item label="备注">{values.notes}</Descriptions.Item>}
        </Descriptions>
      )}
    </div>,
  ];

  return (
    <Modal
      title="初始化客户配置"
      open={open}
      onCancel={handleCancel}
      width={640}
      footer={
        currentStep < 3
          ? [
              <Button key="cancel" onClick={handleCancel}>取消</Button>,
              currentStep > 0 && <Button key="prev" onClick={handlePrev}>上一步</Button>,
              <Button key="next" type="primary" onClick={handleNext}>下一步</Button>,
            ]
          : credentials
            ? [<Button key="close" type="primary" onClick={handleCancel}>完成</Button>]
            : [
                <Button key="prev" onClick={handlePrev}>上一步</Button>,
                <Button key="finish" type="primary" onClick={handleFinish} loading={saving}>完成初始化</Button>,
              ]
      }
    >
      <Steps
        current={currentStep}
        items={[{ title: "基础信息" }, { title: "产品配置" }, { title: "备注" }, { title: "确认" }]}
        className="mb-6"
        size="small"
      />
      {stepContent[currentStep]}
    </Modal>
  );
}
```

- [ ] **Step 2: 验证构建无错误**

```bash
cd frontend && pnpm build:admin
```

Expected: 构建成功，无 TypeScript 错误

- [ ] **Step 3: Commit**

```bash
git add frontend/apps/admin/src/app/(dashboard)/agency/_components/InitClientModal.tsx
git commit -m "feat(agency): overhaul InitClientModal with real templates, summary preview, copy password, slug fix"
```

---

## Wave 4: Frontend Core UX

### Task 7: 客户"下一步"改为页面跳转

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/ClientTable.tsx`

- [ ] **Step 1: 添加 useRouter import 并修改"下一步"按钮行为**

在 `ClientTable.tsx` 中：

文件顶部添加 import：
```typescript
import { useRouter } from "next/navigation";
```

在 `ClientTable` 函数内部添加：
```typescript
const router = useRouter();
```

修改 columns 的 `actions` 列（约第 94-101 行），将"下一步"按钮从 `onCreateTask` 改为 `router.push`：

```typescript
{ title: "下一步", key: "actions", render: (_: unknown, record) => (
  <Space size="small">
    <Button
      size="small"
      type="primary"
      onClick={() => router.push(record.next_action.href)}
    >
      {record.next_action.label}
    </Button>
    <Button size="small" onClick={() => onOpenChecklist(record.id, record.name)}>上线检查</Button>
    <Button size="small" type="link" onClick={() => onCreateTask(record.id, record.next_action.task_title)}>
      创建任务
    </Button>
  </Space>
)},
```

- [ ] **Step 2: 移除不再需要的 props？**

`onCreateTask` 仍然被保留（作为"创建任务"链接），所以 props 接口不变。但 `onCreateTask` 现在是一个次要操作而非主要操作。

- [ ] **Step 3: 验证构建**

```bash
cd frontend && pnpm build:admin
```

- [ ] **Step 4: Commit**

```bash
git add frontend/apps/admin/src/app/(dashboard)/agency/_components/ClientTable.tsx
git commit -m "fix(agency): client next-action button navigates to module instead of opening task modal"
```

---

### Task 8: 任务表格增强 — 筛选、描述、负责人

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/TaskTable.tsx`
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/TaskModals.tsx`

- [ ] **Step 1: TaskTable 添加筛选栏和描述列**

替换 `TaskTable.tsx` 全部内容：

```tsx
"use client";

import { useState } from "react";
import { Button, Card, Input, Select, Space, Table, Tag, Tooltip } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { AgencyClientRow, WorkbenchTask } from "./types";
import { PRIORITY_MAP, TASK_STATUS_MAP } from "./types";

interface TaskTableProps {
  tasks: WorkbenchTask[];
  clients: AgencyClientRow[];
  onUpdateStatus: (taskId: string, newStatus: string) => void;
  onDelete: (taskId: string, taskTitle: string) => void;
}

export function TaskTable({ tasks, clients, onUpdateStatus, onDelete }: TaskTableProps) {
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [priorityFilter, setPriorityFilter] = useState<string>("all");
  const [searchText, setSearchText] = useState("");

  const filteredTasks = tasks.filter((task) => {
    if (statusFilter !== "all" && task.status !== statusFilter) return false;
    if (priorityFilter !== "all" && task.priority !== priorityFilter) return false;
    if (searchText && !task.title.toLowerCase().includes(searchText.toLowerCase())) return false;
    return true;
  });

  const sortedTasks = [...filteredTasks].sort((a, b) => {
    if (a.overdue !== b.overdue) return a.overdue ? -1 : 1;
    if (a.priority !== b.priority) return a.priority === "high" ? -1 : 1;
    return (a.due_date || "").localeCompare(b.due_date || "");
  });

  const columns: ColumnsType<WorkbenchTask> = [
    {
      title: "任务",
      dataIndex: "title",
      key: "title",
      render: (title: string, record) => (
        <div>
          <div>{title}</div>
          {record.description && (
            <div className="mt-1 text-xs text-gray-400 line-clamp-1">{record.description}</div>
          )}
        </div>
      ),
    },
    {
      title: "关联客户",
      dataIndex: "tenant_id",
      key: "tenant_id",
      render: (v: string, record) => {
        const client = clients.find((c) => c.id === v);
        return record.tenant_name || client?.name || v?.slice(0, 8) + "...";
      },
    },
    {
      title: "优先级",
      dataIndex: "priority",
      key: "priority",
      width: 80,
      render: (p: string) => {
        const info = PRIORITY_MAP[p] || { label: p, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 90,
      render: (s: string) => {
        const info = TASK_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "截止日",
      dataIndex: "due_date",
      key: "due_date",
      width: 120,
      render: (v: string | null, record) => {
        if (!v) return "—";
        const date = v.split("T")[0];
        return record.overdue ? <Tag color="red">{date}（逾期）</Tag> : date;
      },
    },
    {
      title: "操作",
      key: "actions",
      width: 140,
      render: (_: unknown, record) => {
        if (record.status === "completed" || record.status === "cancelled") {
          return <Button size="small" type="link" danger onClick={() => onDelete(record.id, record.title)}>删除</Button>;
        }
        return (
          <Space size="small">
            {record.status === "pending" && (
              <Button size="small" type="link" onClick={() => onUpdateStatus(record.id, "in_progress")}>开始</Button>
            )}
            {record.status === "in_progress" && (
              <Button size="small" type="link" onClick={() => onUpdateStatus(record.id, "completed")}>完成</Button>
            )}
            <Button size="small" type="link" onClick={() => onUpdateStatus(record.id, "cancelled")}>取消</Button>
          </Space>
        );
      },
    },
  ];

  return (
    <Card title="任务列表" size="small">
      <div className="mb-4">
        <Space wrap>
          <Input.Search
            placeholder="搜索任务"
            allowClear
            style={{ width: 200 }}
            onSearch={(v) => setSearchText(v)}
            onChange={(e) => { if (!e.target.value) setSearchText(""); }}
          />
          <Select
            value={statusFilter}
            style={{ width: 120 }}
            options={[
              { value: "all", label: "全部状态" },
              { value: "pending", label: "待处理" },
              { value: "in_progress", label: "进行中" },
            ]}
            onChange={setStatusFilter}
          />
          <Select
            value={priorityFilter}
            style={{ width: 120 }}
            options={[
              { value: "all", label: "全部优先级" },
              { value: "high", label: "高" },
              { value: "medium", label: "中" },
              { value: "low", label: "低" },
            ]}
            onChange={setPriorityFilter}
          />
        </Space>
      </div>
      <Table columns={columns} dataSource={sortedTasks} rowKey="id" pagination={false} size="small" />
    </Card>
  );
}
```

- [ ] **Step 2: CreateTaskModal — 客户选择器改用独立 API + 添加描述输入**

修改 `TaskModals.tsx` 中的 `CreateTaskModal` 组件，添加描述字段并改用独立 API 获取客户列表：

在 `CreateTaskModal` 中，添加 `useEffect` 在弹窗打开时从 `/tenants` 获取完整客户列表：

```tsx
// 在 CreateTaskModal 组件内部，替换原有逻辑
const [allClients, setAllClients] = useState<Client[]>([]);
const [clientsLoading, setClientsLoading] = useState(false);

useEffect(() => {
  if (!open) return;
  setClientsLoading(true);
  api.get("/tenants", { params: { page: 1, page_size: 100 } })
    .then(({ data }) => {
      const items = data?.items ?? data ?? [];
      setAllClients(Array.isArray(items) ? items : []);
    })
    .catch(() => setAllClients([]))
    .finally(() => setClientsLoading(false));
}, [open]);
```

修改 `props` 接口——移除 `clients` prop（改用内部获取），但保留以兼容渐进迁移。实际上保留 `clients` 作为 fallback：

```tsx
const clientOptions = allClients.length > 0
  ? allClients.map((c) => ({ value: c.id, label: c.name }))
  : clients.map((c) => ({ value: c.id, label: c.name }));
```

在表单中添加描述字段（在 `tenant_id` 之后）：

```tsx
<Form.Item name="description" label="描述">
  <Input.TextArea rows={2} placeholder="任务描述（可选）" />
</Form.Item>
```

更新 `handleCreate` 提交时包含 description：

```tsx
await api.post("/ops/tasks", {
  tenant_id: values.tenant_id,
  title: values.title,
  description: values.description || null,
  priority: values.priority || "medium",
  ...(values.due_date ? { due_date: values.due_date.toISOString() } : {}),
});
```

- [ ] **Step 3: ChecklistModal — 替换 Checkbox 为图标 + 添加重试按钮**

修改 `TaskModals.tsx` 中的 `ChecklistModal`：

```tsx
import { CheckCircleFilled, CloseCircleFilled, ReloadOutlined } from "@ant-design/icons";

export function ChecklistModal({ open, clientName, onClose, data, loading, onRetry }: ChecklistModalProps) {
  return (
    <Modal title={`上线检查清单 — ${clientName}`} open={open} onCancel={onClose} footer={null} width={600}>
      {loading ? (
        <div className="py-8 text-center text-gray-400">加载中...</div>
      ) : data ? (
        <>
          <div className="mb-4">
            <Progress
              percent={data.total_count ? Math.round((data.passed_count / data.total_count) * 100) : 0}
              status={data.ready ? "success" : "active"}
            />
            <div className="mt-1 text-sm text-gray-400">{data.passed_count} / {data.total_count} 项通过</div>
          </div>
          <List
            dataSource={data.checks}
            renderItem={(item) => (
              <List.Item>
                <div className="flex items-center gap-2 w-full">
                  {item.passed ? (
                    <CheckCircleFilled className="text-green-500 text-lg" />
                  ) : (
                    <CloseCircleFilled className="text-red-400 text-lg" />
                  )}
                  <span>{item.name}</span>
                  <span className="ml-2 text-sm text-gray-400">{item.detail}</span>
                </div>
              </List.Item>
            )}
          />
        </>
      ) : (
        <div className="py-8 text-center">
          <div className="mb-4 text-gray-400">无法加载检查清单</div>
          <Button icon={<ReloadOutlined />} onClick={onRetry}>重试</Button>
        </div>
      )}
    </Modal>
  );
}
```

更新 `ChecklistModalProps` 接口：

```typescript
interface ChecklistModalProps {
  open: boolean;
  clientName: string;
  onClose: () => void;
  data: ChecklistResult | null;
  loading: boolean;
  onRetry: () => void;
}
```

- [ ] **Step 4: 更新 page.tsx — 传递 onRetry 给 ChecklistModal**

在 `page.tsx` 中修改 `ChecklistModal` 的使用方式，添加 `onRetry` prop：

```tsx
const handleRetryChecklist = () => {
  if (checklistModalOpen) {
    setChecklistLoading(true);
    // 重新获取当前客户端 ID（保存在 ref 或 state 中）
    // 需要新增一个 state 来保存当前检查的 clientId
  }
};
```

具体实现：在 `page.tsx` 中新增 `checklistClientId` state：

```typescript
const [checklistClientId, setChecklistClientId] = useState("");
```

修改 `handleOpenChecklist`：

```typescript
const handleOpenChecklist = async (clientId: string, clientName: string) => {
  setChecklistLoading(true);
  setChecklistClientName(clientName);
  setChecklistClientId(clientId);
  setChecklistModalOpen(true);
  try {
    const { data } = await api.get(`/ops/clients/${clientId}/launch-checklist`);
    setChecklistData(data);
  } catch {
    setChecklistData(null);
  } finally {
    setChecklistLoading(false);
  }
};
```

添加 `handleRetryChecklist`：

```typescript
const handleRetryChecklist = async () => {
  if (!checklistClientId) return;
  setChecklistLoading(true);
  setChecklistData(null);
  try {
    const { data } = await api.get(`/ops/clients/${checklistClientId}/launch-checklist`);
    setChecklistData(data);
  } catch {
    setChecklistData(null);
  } finally {
    setChecklistLoading(false);
  }
};
```

更新 ChecklistModal 调用：

```tsx
<ChecklistModal
  open={checklistModalOpen}
  clientName={checklistClientName}
  onClose={() => { setChecklistModalOpen(false); setChecklistData(null); setChecklistClientId(""); }}
  data={checklistData}
  loading={checklistLoading}
  onRetry={handleRetryChecklist}
/>
```

- [ ] **Step 5: 验证构建**

```bash
cd frontend && pnpm build:admin
```

- [ ] **Step 6: Commit**

```bash
git add frontend/apps/admin/src/app/(dashboard)/agency/
git commit -m "feat(agency): task filters, description, checklist retry, client search from tenants API"
```

---

## Wave 5: Frontend Polish

### Task 9: 统计卡片交互 + 到期提醒

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/StatsCards.tsx`
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/page.tsx`

- [ ] **Step 1: StatsCards 添加点击交互**

替换 `StatsCards.tsx`：

```tsx
"use client";

import { Card, Col, Row, Statistic } from "antd";
import {
  CheckCircleOutlined,
  CheckSquareOutlined,
  ExclamationCircleOutlined,
  TeamOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import type { WorkbenchSummary } from "./types";

interface StatsCardsProps {
  summary: WorkbenchSummary;
  onCardClick: (filterType: "overdue" | "blocked" | "ready" | "pending") => void;
}

export function StatsCards({ summary, onCardClick }: StatsCardsProps) {
  return (
    <Row gutter={[16, 16]} className="mb-6">
      <Col xs={24} sm={12} xl={5}>
        <Card className="cursor-default">
          <Statistic title="客户总数" value={summary.total_clients} prefix={<TeamOutlined />} />
        </Card>
      </Col>
      <Col xs={24} sm={12} xl={5}>
        <Card
          className="cursor-pointer transition-shadow hover:shadow-md"
          onClick={() => onCardClick("ready")}
        >
          <Statistic title="已具备上线条件" value={summary.ready_clients} prefix={<CheckCircleOutlined />} styles={{ content: { color: "#52c41a" } }} />
        </Card>
      </Col>
      <Col xs={24} sm={12} xl={5}>
        <Card
          className="cursor-pointer transition-shadow hover:shadow-md"
          onClick={() => onCardClick("blocked")}
        >
          <Statistic title="需补齐配置" value={summary.blocked_clients} prefix={<ToolOutlined />} styles={{ content: { color: "#faad14" } }} />
        </Card>
      </Col>
      <Col xs={24} sm={12} xl={4}>
        <Card
          className="cursor-pointer transition-shadow hover:shadow-md"
          onClick={() => onCardClick("overdue")}
        >
          <Statistic title="逾期任务" value={summary.overdue_tasks} prefix={<ExclamationCircleOutlined />} styles={{ content: { color: "#ff4d4f" } }} />
        </Card>
      </Col>
      <Col xs={24} sm={12} xl={5}>
        <Card
          className="cursor-pointer transition-shadow hover:shadow-md"
          onClick={() => onCardClick("pending")}
        >
          <Statistic title="待办任务" value={summary.pending_tasks} prefix={<CheckSquareOutlined />} styles={{ content: { color: "#1890ff" } }} />
        </Card>
      </Col>
    </Row>
  );
}
```

- [ ] **Step 2: page.tsx — 添加卡片点击处理和到期提醒 Alert**

在 `page.tsx` 中：

更新 `StatsCards` 调用，传入 `onCardClick`：

```tsx
<StatsCards summary={overview} onCardClick={handleStatsCardClick} />
```

添加 `handleStatsCardClick` 函数：

```typescript
const handleStatsCardClick = (filterType: "overdue" | "blocked" | "ready" | "pending") => {
  switch (filterType) {
    case "ready":
      handleFilterChange({ ...workbenchFilter, readiness: "ready" });
      break;
    case "blocked":
      handleFilterChange({ ...workbenchFilter, readiness: "blocked" });
      break;
    case "overdue":
      handleFilterChange({ ...workbenchFilter, task_status: "overdue" });
      break;
    case "pending":
      handleFilterChange({ ...workbenchFilter, task_status: "pending" });
      break;
  }
};
```

在 `StatsCards` 下方、`workbenchError` Alert 之前，添加到期提醒：

```tsx
{/* 即将到期客户提醒 */}
{clients.some((c) => {
  if (!c.plan_expires_at) return false;
  const daysLeft = Math.ceil((new Date(c.plan_expires_at).getTime() - Date.now()) / (1000 * 60 * 60 * 24));
  return daysLeft >= 0 && daysLeft < 30;
}) && (
  <Alert
    className="mb-4"
    type="warning"
    showIcon
    message="有客户套餐即将到期"
    description={
      <span>
        以下客户套餐将在 30 天内到期：
        {clients
          .filter((c) => {
            if (!c.plan_expires_at) return false;
            const daysLeft = Math.ceil((new Date(c.plan_expires_at).getTime() - Date.now()) / (1000 * 60 * 60 * 24));
            return daysLeft >= 0 && daysLeft < 30;
          })
          .map((c) => ` ${c.name}`)
          .join("、")}
      </span>
    }
  />
)}
```

- [ ] **Step 3: 验证构建**

```bash
cd frontend && pnpm build:admin
```

- [ ] **Step 4: Commit**

```bash
git add frontend/apps/admin/src/app/(dashboard)/agency/
git commit -m "feat(agency): stats card click-to-filter and expiring plan alert banner"
```

---

## 自查清单

### 1. Spec 覆盖检查

| 审查发现 | 对应 Task |
|----------|-----------|
| Critical #1: 初始化向导无汇总预览、密码不可复制 | Task 6 |
| Critical #2: slug 生成被前端绕过 | Task 6（前端不再传 slug） |
| Critical #3: ops API 无权限控制 | Task 2 |
| Moderate #1: N+1 查询性能问题 | Task 4 |
| Moderate #2: 创建任务客户列表来源受限 | Task 8 |
| Moderate #3: 任务列表缺筛选 | Task 8 |
| Moderate #4: 任务操作重新加载整个 workbench | 保留现状（优化更新复杂度较高，可在后续迭代中使用 SWR mutate 精细刷新） |
| Moderate #5: 密码不可复制 | Task 6 |
| Moderate #6: "下一步"应跳转而非创建任务 | Task 7 |
| Moderate #7: 检查清单无重试 | Task 8 |
| Suggestion #1: 统计卡片交互 | Task 9 |
| Suggestion #2: 前端排序冗余 | Task 8 保留（后端已排序，前端排序作为本地筛选后的再排序仍有价值） |
| Suggestion #3: 到期提醒 | Task 9 |
| Suggestion #4: 行业/备注字段 | Task 1 + Task 3 + Task 6 |
| Suggestion #5: 任务描述 | Task 8 |
| Suggestion #6: 时间筛选 | 未覆盖（优先级较低，可在后续迭代添加） |
| Suggestion #7: assigned_to | Task 1 + Task 3 |
| Suggestion #8: Checkbox 改图标 | Task 8 |
| 模板系统集成 | Task 3 + Task 6 |

### 2. Placeholder 扫描

无 TBD、TODO、"implement later"、"add validation" 等占位符。

### 3. 类型一致性

- `IndustryTemplate` 在 `types.ts` 中定义，在 `InitClientModal.tsx` 中导入使用 — 一致
- `Client.industry`、`Task.assigned_to`、`OpsTaskRead.assigned_to` — 前后端字段名一致
- `get_ops_user` 返回 `tuple[uuid.UUID, str]`，所有端点使用 `_ops_user: tuple` — 一致
- `template_id` 在 `TenantCreate` schema 和前端 POST body 中均为 `int | None` — 一致
