# Agency Workbench Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `/agency` from a passive client/task list into a complete operator workbench that shows client launch readiness, explains what blocks go-live, and gives the operator a clear next action for each client.

**Architecture:** Keep the existing `/tenants`, `/ops/tasks`, and `/ops/clients/{tenant_id}/launch-checklist` APIs intact. Add one backend aggregation endpoint for the workbench so the frontend can load summary cards, client queue rows, readiness progress, task counters, and recommended next actions in one request. Frontend components remain route-local under `frontend/apps/admin/src/app/(dashboard)/agency/_components`.

**Tech Stack:** FastAPI, SQLAlchemy async sessions, Pydantic schemas, pytest/httpx, Next.js 16, React 19, Ant Design, Vitest, Testing Library.

---

## Current State

- Backend already exposes:
  - `GET /api/v1/ops/overview`
  - `GET /api/v1/ops/tasks`
  - `GET /api/v1/ops/clients/{tenant_id}/launch-checklist`
  - `GET /api/v1/tenants`
- Frontend `/agency` currently renders:
  - `StatsCards`
  - `ClientTable`
  - `TaskTable`
  - `InitClientModal`
  - `CreateTaskModal`
  - `ChecklistModal`
- Main UX gap:
  - Operators see counts and rows, but not which customer needs attention first.
  - Client status is too coarse for operator decisions.
  - `检查清单` does not expose go-live readiness until after clicking.
  - Empty and failed states do not explain what happened.

## Target User Flow

1. Operator opens `/agency`.
2. The workbench loads a prioritized queue.
3. Top cards show total clients, ready clients, blocked clients, overdue tasks, and pending tasks.
4. Client rows show launch readiness as `passed_count / total_count`, missing items, and task counters.
5. Each client row has one primary next action:
   - `配置品牌`
   - `创建产品`
   - `发布扫码页`
   - `激活码批次`
   - `查看上线检查`
   - `处理任务`
6. Operator can open the checklist modal, create a task prefilled for the selected client, start/complete tasks, and see the queue refresh.
7. Empty state clearly distinguishes no clients, no matching search results, and API load failure.

## API Contract

Add `GET /api/v1/ops/workbench`.

Query parameters:

```text
page: int = 1
page_size: int = 20
q: string | null
readiness: "all" | "ready" | "blocked" = "all"
task_status: "all" | "pending" | "in_progress" | "overdue" = "all"
```

Response shape:

```json
{
  "summary": {
    "total_clients": 12,
    "active_clients": 11,
    "ready_clients": 7,
    "blocked_clients": 4,
    "pending_tasks": 3,
    "in_progress_tasks": 1,
    "overdue_tasks": 1
  },
  "clients": [
    {
      "id": "tenant-id",
      "name": "演示租户",
      "status": "active",
      "plan": "pro",
      "plan_expires_at": "2026-12-31T00:00:00Z",
      "created_at": "2026-05-31T00:00:00Z",
      "readiness": {
        "ready": false,
        "passed_count": 2,
        "total_count": 4,
        "percent": 50,
        "missing_keys": ["page_published", "code_batch_activated"],
        "missing_labels": ["发布扫码页", "激活码批次"]
      },
      "task_summary": {
        "pending": 1,
        "in_progress": 0,
        "overdue": 0,
        "high_priority": 1
      },
      "next_action": {
        "type": "publish_page",
        "label": "发布扫码页",
        "href": "/pages",
        "task_title": "为演示租户发布扫码页"
      }
    }
  ],
  "tasks": [
    {
      "id": "task-id",
      "tenant_id": "tenant-id",
      "tenant_name": "演示租户",
      "title": "配置品牌信息",
      "description": "补齐品牌名称、Logo 和简介",
      "status": "pending",
      "priority": "high",
      "due_date": "2026-06-03T00:00:00Z",
      "created_at": "2026-06-01T00:00:00Z",
      "updated_at": "2026-06-01T00:00:00Z",
      "overdue": false
    }
  ],
  "total": 12,
  "page": 1,
  "page_size": 20
}
```

Readiness key mapping:

```python
READINESS_STEPS = [
    ("brand_configured", "配置品牌", "/brands"),
    ("product_created", "创建产品", "/products"),
    ("page_published", "发布扫码页", "/pages"),
    ("code_batch_activated", "激活码批次", "/codes"),
]
```

Recommended action rule:

1. If there are overdue tasks, `next_action` is `处理逾期任务`.
2. Otherwise use the first missing readiness step in `READINESS_STEPS`.
3. If all readiness steps passed and pending tasks exist, `next_action` is `处理任务`.
4. If all readiness steps passed and no pending tasks exist, `next_action` is `查看上线检查`.

---

## Files

Backend:

- Modify: `backend/app/schemas/tenant.py`
  - Add Pydantic schemas for workbench summary, client rows, readiness, task summary, and next action.
- Modify: `backend/app/services/ops.py`
  - Add reusable readiness normalization and workbench aggregation functions.
- Modify: `backend/app/api/v1/ops.py`
  - Add `GET /workbench`.
- Modify: `backend/tests/test_api/test_operator_workbench.py`
  - Add API tests for summary, readiness, next action, filtering, and overdue tasks.

Frontend:

- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/types.ts`
  - Add `AgencyWorkbenchResponse`, `AgencyClientRow`, `ReadinessSummary`, `TaskSummary`, `NextAction`.
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/page.tsx`
  - Load `/ops/workbench`, keep existing fetchers for mutations and fallback.
  - Add selected-client task creation support.
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/StatsCards.tsx`
  - Replace coarse cards with operator-action cards.
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/ClientTable.tsx`
  - Render readiness progress, missing labels, task counters, next action, and clearer empty states.
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/TaskTable.tsx`
  - Sort and visually mark overdue/high-priority work.
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/TaskModals.tsx`
  - Allow prefilled tenant/title when creating tasks from a client row.
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/__tests__/page.test.tsx`
  - Add component tests for the new queue, actions, fallback, and empty states.

Browser verification:

- Use the in-app browser at `http://localhost:3000/agency`.

---

## Acceptance Criteria

- The workbench has a single backend aggregation endpoint: `GET /api/v1/ops/workbench`.
- The endpoint returns summary cards, client readiness, task summary, next action, prioritized clients, and recent actionable tasks.
- Client readiness is derived from actual products, brands, published pages, and activated code batches.
- Overdue tasks are computed from `due_date < now` and unfinished statuses.
- Frontend shows readiness directly in the client queue without requiring a modal click.
- `检查清单` copy is replaced with `上线检查`.
- A row-level next action is visible and action-oriented.
- Creating a task from a client row preselects that client and suggests a task title.
- Task status changes refresh the workbench summary and affected rows.
- Search and filters use backend workbench query parameters.
- Empty states distinguish:
  - no clients yet,
  - no matching search/filter results,
  - backend load failure.
- Existing `/ops/tasks`, `/tenants`, and launch checklist behavior stays compatible.

---

## Task 1: Backend Schemas

**Files:**
- Modify: `backend/app/schemas/tenant.py`
- Test: `backend/tests/test_api/test_operator_workbench.py`

- [ ] **Step 1: Write the failing API schema coverage test**

Add this test to `TestOpsTasks` or a new `TestOpsWorkbench` class in `backend/tests/test_api/test_operator_workbench.py`:

```python
@pytest.mark.anyio
async def test_ops_workbench_returns_client_queue_shape(
    self,
    platform_admin_client: AsyncClient,
    sample_tenants,
):
    resp = await platform_admin_client.get("/api/v1/ops/workbench")

    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "clients" in data
    assert "tasks" in data
    assert "total" in data
    assert data["clients"]

    row = data["clients"][0]
    assert {"id", "name", "readiness", "task_summary", "next_action"} <= set(row)
    assert {"ready", "passed_count", "total_count", "percent", "missing_labels"} <= set(row["readiness"])
    assert {"pending", "in_progress", "overdue", "high_priority"} <= set(row["task_summary"])
    assert {"type", "label", "href", "task_title"} <= set(row["next_action"])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
uv run pytest tests/test_api/test_operator_workbench.py::TestOpsWorkbench::test_ops_workbench_returns_client_queue_shape -q
```

Expected: FAIL with `404 Not Found` for `/api/v1/ops/workbench`.

- [ ] **Step 3: Add Pydantic schemas**

Append these models after `OpsTaskRead` in `backend/app/schemas/tenant.py`:

```python
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
```

- [ ] **Step 4: Run import check**

Run:

```bash
cd backend
uv run python -c "from app.schemas.tenant import OpsWorkbenchResponse; print(OpsWorkbenchResponse.__name__)"
```

Expected: prints `OpsWorkbenchResponse`.

---

## Task 2: Backend Workbench Aggregation

**Files:**
- Modify: `backend/app/services/ops.py`
- Modify: `backend/app/api/v1/ops.py`
- Test: `backend/tests/test_api/test_operator_workbench.py`

- [ ] **Step 1: Write failing tests for readiness and next actions**

Add these tests to `TestOpsWorkbench`:

```python
@pytest.mark.anyio
async def test_ops_workbench_prioritizes_missing_readiness_action(
    self,
    platform_admin_client: AsyncClient,
    sample_tenants,
):
    tenant_id = sample_tenants[0]["id"]

    resp = await platform_admin_client.get("/api/v1/ops/workbench")

    assert resp.status_code == 200
    row = next(client for client in resp.json()["clients"] if client["id"] == tenant_id)
    assert row["readiness"]["ready"] is False
    assert row["next_action"]["label"] == "配置品牌"
    assert row["next_action"]["href"] == "/brands"


@pytest.mark.anyio
async def test_ops_workbench_marks_overdue_tasks(
    self,
    platform_admin_client: AsyncClient,
    sample_tenants,
):
    await platform_admin_client.post(
        "/api/v1/ops/tasks",
        json={
            "tenant_id": sample_tenants[0]["id"],
            "title": "逾期配置产品",
            "priority": "high",
            "due_date": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
    )

    resp = await platform_admin_client.get("/api/v1/ops/workbench")

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["overdue_tasks"] >= 1
    row = next(client for client in data["clients"] if client["id"] == sample_tenants[0]["id"])
    assert row["task_summary"]["overdue"] == 1
    assert row["next_action"]["label"] == "处理逾期任务"
    assert row["next_action"]["href"] == "/agency"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
uv run pytest tests/test_api/test_operator_workbench.py::TestOpsWorkbench -q
```

Expected: FAIL until `/ops/workbench` exists.

- [ ] **Step 3: Implement aggregation helpers**

Add these constants and functions in `backend/app/services/ops.py`:

```python
from datetime import UTC, datetime

from app.models.tenant import OpsTask, OpsTaskPriority, OpsTaskStatus, Tenant, TenantStatus

READINESS_STEPS = [
    ("brand_configured", "配置品牌", "/brands"),
    ("product_created", "创建产品", "/products"),
    ("page_published", "发布扫码页", "/pages"),
    ("code_batch_activated", "激活码批次", "/codes"),
]


def build_readiness_summary(status: dict) -> dict:
    progress = status.get("onboarding_progress") or {}
    derived = {
        "brand_configured": status.get("brands", 0) >= 1,
        "product_created": status.get("products", 0) >= 1,
        "page_published": status.get("published_pages", 0) >= 1,
        "code_batch_activated": status.get("activated_batches", 0) >= 1,
    }
    merged = {**derived, **progress}
    missing = [(key, label, href) for key, label, href in READINESS_STEPS if not merged.get(key)]
    passed_count = len(READINESS_STEPS) - len(missing)
    total_count = len(READINESS_STEPS)
    return {
        "ready": passed_count == total_count,
        "passed_count": passed_count,
        "total_count": total_count,
        "percent": round((passed_count / total_count) * 100) if total_count else 0,
        "missing_keys": [key for key, _, _ in missing],
        "missing_labels": [label for _, label, _ in missing],
    }


def build_next_action(tenant_name: str, readiness: dict, task_summary: dict) -> dict:
    if task_summary["overdue"] > 0:
        return {
            "type": "overdue_task",
            "label": "处理逾期任务",
            "href": "/agency",
            "task_title": f"跟进{tenant_name}逾期任务",
        }
    if readiness["missing_keys"]:
        missing_key = readiness["missing_keys"][0]
        step = next((item for item in READINESS_STEPS if item[0] == missing_key), READINESS_STEPS[0])
        return {
            "type": missing_key,
            "label": step[1],
            "href": step[2],
            "task_title": f"为{tenant_name}{step[1]}",
        }
    if task_summary["pending"] > 0 or task_summary["in_progress"] > 0:
        return {
            "type": "task",
            "label": "处理任务",
            "href": "/agency",
            "task_title": f"跟进{tenant_name}待办任务",
        }
    return {
        "type": "checklist",
        "label": "查看上线检查",
        "href": "/agency",
        "task_title": f"复核{tenant_name}上线检查",
    }
```

- [ ] **Step 4: Implement `get_ops_workbench`**

Add the function in `backend/app/services/ops.py`. Use current service patterns and keep the query explicit:

```python
async def get_ops_workbench(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    readiness: str = "all",
    task_status: str = "all",
) -> dict:
    from app.utils import escape_like_pattern

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

    task_rows = []
    if tenant_ids:
        task_result = await db.execute(
            select(OpsTask).where(OpsTask.tenant_id.in_(tenant_ids)).order_by(OpsTask.created_at.desc())
        )
        task_rows = list(task_result.scalars().all())

    now = datetime.now(UTC)
    tasks_by_tenant: dict = {tenant.id: [] for tenant in tenants}
    for task in task_rows:
        tasks_by_tenant.setdefault(task.tenant_id, []).append(task)

    clients = []
    ready_clients = 0
    blocked_clients = 0
    pending_tasks = 0
    in_progress_tasks = 0
    overdue_tasks = 0

    for tenant in tenants:
        status = await get_tenant_status(db, tenant.id)
        readiness_summary = build_readiness_summary(status)
        tenant_tasks = tasks_by_tenant.get(tenant.id, [])
        task_summary = {
            "pending": 0,
            "in_progress": 0,
            "overdue": 0,
            "high_priority": 0,
        }
        for task in tenant_tasks:
            is_open = task.status in (OpsTaskStatus.pending, OpsTaskStatus.in_progress)
            is_overdue = bool(task.due_date and task.due_date < now and is_open)
            if task.status == OpsTaskStatus.pending:
                task_summary["pending"] += 1
                pending_tasks += 1
            if task.status == OpsTaskStatus.in_progress:
                task_summary["in_progress"] += 1
                in_progress_tasks += 1
            if is_overdue:
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

        clients.append(
            {
                "id": tenant.id,
                "name": tenant.name,
                "status": tenant.status.value if hasattr(tenant.status, "value") else str(tenant.status),
                "plan": tenant.plan.value if hasattr(tenant.plan, "value") else str(tenant.plan),
                "plan_expires_at": tenant.plan_expires_at,
                "created_at": tenant.created_at,
                "readiness": readiness_summary,
                "task_summary": task_summary,
                "next_action": build_next_action(tenant.name, readiness_summary, task_summary),
            }
        )

    actionable_tasks = []
    for task in task_rows:
        is_open = task.status in (OpsTaskStatus.pending, OpsTaskStatus.in_progress)
        if not is_open:
            continue
        tenant = next((item for item in tenants if item.id == task.tenant_id), None)
        actionable_tasks.append(
            {
                "id": task.id,
                "tenant_id": task.tenant_id,
                "tenant_name": tenant.name if tenant else None,
                "title": task.title,
                "description": task.description,
                "status": task.status.value if hasattr(task.status, "value") else str(task.status),
                "priority": task.priority.value if hasattr(task.priority, "value") else str(task.priority),
                "due_date": task.due_date,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "overdue": bool(task.due_date and task.due_date < now and is_open),
            }
        )

    actionable_tasks.sort(key=lambda item: (not item["overdue"], item["priority"] != "high", item["due_date"] or now))

    return {
        "summary": {
            "total_clients": total,
            "active_clients": sum(1 for tenant in tenants if tenant.status == TenantStatus.active),
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

- [ ] **Step 5: Add the route**

Modify imports in `backend/app/api/v1/ops.py`:

```python
from app.schemas.tenant import OpsTaskCreate, OpsTaskRead, OpsTaskUpdate, OpsWorkbenchResponse
from app.services.ops import get_launch_checklist, get_ops_workbench, get_tenant_status
```

Add this route before `/clients/{tenant_id}/status`:

```python
@ops_router.get("/workbench", response_model=OpsWorkbenchResponse, summary="代运营工作台聚合")
async def get_ops_workbench_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, description="搜索客户名称"),
    readiness: str = Query("all", pattern="^(all|ready|blocked)$"),
    task_status: str = Query("all", pattern="^(all|pending|in_progress|overdue)$"),
    db: AsyncSession = Depends(get_db),
):
    return await get_ops_workbench(db, page=page, page_size=page_size, q=q, readiness=readiness, task_status=task_status)
```

- [ ] **Step 6: Run backend tests**

Run:

```bash
cd backend
uv run pytest tests/test_api/test_operator_workbench.py -q
```

Expected: PASS.

---

## Task 3: Frontend Types And Fetch Flow

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/types.ts`
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/page.tsx`
- Test: `frontend/apps/admin/src/app/(dashboard)/agency/__tests__/page.test.tsx`

- [ ] **Step 1: Write failing frontend test for workbench fetch**

Add this test:

```tsx
it("loads the operator queue from the workbench endpoint", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url === "/ops/workbench") {
      return Promise.resolve({
        data: {
          summary: {
            total_clients: 1,
            active_clients: 1,
            ready_clients: 0,
            blocked_clients: 1,
            pending_tasks: 1,
            in_progress_tasks: 0,
            overdue_tasks: 0,
          },
          clients: [
            {
              id: "t1",
              name: "客户A",
              status: "active",
              plan: "pro",
              plan_expires_at: null,
              created_at: "2026-01-15T00:00:00Z",
              readiness: {
                ready: false,
                passed_count: 1,
                total_count: 4,
                percent: 25,
                missing_keys: ["product_created"],
                missing_labels: ["创建产品"],
              },
              task_summary: { pending: 1, in_progress: 0, overdue: 0, high_priority: 1 },
              next_action: {
                type: "product_created",
                label: "创建产品",
                href: "/products",
                task_title: "为客户A创建产品",
              },
            },
          ],
          tasks: [],
          total: 1,
          page: 1,
          page_size: 20,
        },
      });
    }
    return Promise.resolve({ data: { items: [], total: 0, page: 1, page_size: 20 } });
  });

  render(<AgencyPage />);

  await waitFor(() => {
    expect(mockGet).toHaveBeenCalledWith("/ops/workbench", { params: { page: 1, page_size: 100 } });
    expect(screen.getByText("客户A")).toBeInTheDocument();
    expect(screen.getByText("1/4")).toBeInTheDocument();
    expect(screen.getByText("创建产品")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd frontend/apps/admin
pnpm exec vitest run 'src/app/(dashboard)/agency/__tests__/page.test.tsx' -t 'loads the operator queue'
```

Expected: FAIL because `/ops/workbench` is not called.

- [ ] **Step 3: Add frontend types**

Add these interfaces in `frontend/apps/admin/src/app/(dashboard)/agency/_components/types.ts`:

```ts
export interface ReadinessSummary {
  ready: boolean;
  passed_count: number;
  total_count: number;
  percent: number;
  missing_keys: string[];
  missing_labels: string[];
}

export interface TaskSummary {
  pending: number;
  in_progress: number;
  overdue: number;
  high_priority: number;
}

export interface NextAction {
  type: string;
  label: string;
  href: string;
  task_title: string;
}

export interface AgencyClientRow extends Client {
  readiness: ReadinessSummary;
  task_summary: TaskSummary;
  next_action: NextAction;
}

export interface WorkbenchSummary {
  total_clients: number;
  active_clients: number;
  ready_clients: number;
  blocked_clients: number;
  pending_tasks: number;
  in_progress_tasks: number;
  overdue_tasks: number;
}

export interface WorkbenchTask extends Task {
  description?: string | null;
  overdue: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AgencyWorkbenchResponse {
  summary: WorkbenchSummary;
  clients: AgencyClientRow[];
  tasks: WorkbenchTask[];
  total: number;
  page: number;
  page_size: number;
}
```

- [ ] **Step 4: Refactor page fetch state**

In `frontend/apps/admin/src/app/(dashboard)/agency/page.tsx`, replace separate initial loading with a `fetchWorkbench` function:

```tsx
const [clients, setClients] = useState<AgencyClientRow[]>([]);
const [tasks, setTasks] = useState<WorkbenchTask[]>([]);
const [workbenchError, setWorkbenchError] = useState(false);
const [overview, setOverview] = useState<WorkbenchSummary>({
  total_clients: 0,
  active_clients: 0,
  ready_clients: 0,
  blocked_clients: 0,
  pending_tasks: 0,
  in_progress_tasks: 0,
  overdue_tasks: 0,
});

const fetchWorkbench = async (filter?: { q?: string; readiness?: string; task_status?: string }) => {
  setLoading(true);
  setWorkbenchError(false);
  try {
    const { data } = await api.get("/ops/workbench", {
      params: {
        page: 1,
        page_size: 100,
        ...(filter?.q ? { q: filter.q } : {}),
        ...(filter?.readiness ? { readiness: filter.readiness } : {}),
        ...(filter?.task_status ? { task_status: filter.task_status } : {}),
      },
    });
    setOverview(data.summary);
    setClients(data.clients || []);
    setTasks(data.tasks || []);
  } catch {
    setWorkbenchError(true);
    setClients([]);
    setTasks([]);
  } finally {
    setLoading(false);
  }
};
```

Update mutation callbacks to call `fetchWorkbench()` after create/update/delete operations.

- [ ] **Step 5: Run frontend targeted test**

Run:

```bash
cd frontend/apps/admin
pnpm exec vitest run 'src/app/(dashboard)/agency/__tests__/page.test.tsx' -t 'loads the operator queue'
```

Expected: PASS.

---

## Task 4: Frontend Client Queue UI

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/StatsCards.tsx`
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/ClientTable.tsx`
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/TaskModals.tsx`
- Test: `frontend/apps/admin/src/app/(dashboard)/agency/__tests__/page.test.tsx`

- [ ] **Step 1: Write failing tests for visible readiness and row actions**

Add:

```tsx
it("shows launch readiness, missing items, and next action in the client queue", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url === "/ops/workbench") {
      return Promise.resolve({
        data: {
          summary: {
            total_clients: 1,
            active_clients: 1,
            ready_clients: 0,
            blocked_clients: 1,
            pending_tasks: 1,
            in_progress_tasks: 0,
            overdue_tasks: 0,
          },
          clients: [
            {
              id: "t1",
              name: "客户A",
              status: "active",
              plan: "pro",
              plan_expires_at: null,
              created_at: "2026-01-15T00:00:00Z",
              readiness: {
                ready: false,
                passed_count: 2,
                total_count: 4,
                percent: 50,
                missing_keys: ["page_published", "code_batch_activated"],
                missing_labels: ["发布扫码页", "激活码批次"],
              },
              task_summary: { pending: 1, in_progress: 0, overdue: 0, high_priority: 1 },
              next_action: {
                type: "page_published",
                label: "发布扫码页",
                href: "/pages",
                task_title: "为客户A发布扫码页",
              },
            },
          ],
          tasks: [],
          total: 1,
          page: 1,
          page_size: 20,
        },
      });
    }
    return Promise.resolve({ data: { items: [], total: 0, page: 1, page_size: 20 } });
  });

  render(<AgencyPage />);

  await waitFor(() => {
    expect(screen.getByText("上线准备度")).toBeInTheDocument();
    expect(screen.getByText("2/4")).toBeInTheDocument();
    expect(screen.getByText("缺：发布扫码页、激活码批次")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /发布扫码页/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /上线检查/ })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd frontend/apps/admin
pnpm exec vitest run 'src/app/(dashboard)/agency/__tests__/page.test.tsx' -t 'shows launch readiness'
```

Expected: FAIL until UI is updated.

- [ ] **Step 3: Update stats cards**

Show these cards:

- `客户总数`
- `已具备上线条件`
- `需补齐配置`
- `逾期任务`
- `待办任务`

Use Ant Design `Statistic` and semantic colors from AntD tokens where possible. Keep card radius and density aligned with existing Admin pages.

- [ ] **Step 4: Update client queue columns**

Columns should be:

- `客户`
- `上线准备度`
- `缺失配置`
- `任务`
- `到期日`
- `下一步`

Render readiness as:

```tsx
<Space direction="vertical" size={2}>
  <Progress percent={record.readiness.percent} size="small" status={record.readiness.ready ? "success" : "active"} />
  <span>{record.readiness.passed_count}/{record.readiness.total_count}</span>
</Space>
```

Render missing labels as:

```tsx
record.readiness.ready
  ? <Tag color="green">已具备上线条件</Tag>
  : <span>缺：{record.readiness.missing_labels.join("、")}</span>
```

Render row actions:

```tsx
<Space size="small">
  <Button size="small" type="primary" onClick={() => onCreateTask(record.id, record.next_action.task_title)}>
    {record.next_action.label}
  </Button>
  <Button size="small" onClick={() => onOpenChecklist(record.id, record.name)}>
    上线检查
  </Button>
</Space>
```

- [ ] **Step 5: Add prefilled task creation**

In `CreateTaskModal`, accept:

```ts
initialTenantId?: string;
initialTitle?: string;
```

When modal opens with these props, call:

```tsx
form.setFieldsValue({
  tenant_id: initialTenantId,
  title: initialTitle,
  priority: "medium",
});
```

Clear those values when modal closes.

- [ ] **Step 6: Run component tests**

Run:

```bash
cd frontend/apps/admin
pnpm exec vitest run 'src/app/(dashboard)/agency/__tests__/page.test.tsx'
```

Expected: PASS.

---

## Task 5: Filters, Empty States, And Failure States

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/page.tsx`
- Modify: `frontend/apps/admin/src/app/(dashboard)/agency/_components/ClientTable.tsx`
- Test: `frontend/apps/admin/src/app/(dashboard)/agency/__tests__/page.test.tsx`

- [ ] **Step 1: Write failing tests for states**

Add:

```tsx
it("explains a failed workbench load", async () => {
  mockGet.mockRejectedValue(new Error("network"));

  render(<AgencyPage />);

  await waitFor(() => {
    expect(screen.getByText("工作台数据加载失败")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重新加载/ })).toBeInTheDocument();
  });
});

it("explains when filters return no clients", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url === "/ops/workbench") {
      return Promise.resolve({
        data: {
          summary: {
            total_clients: 1,
            active_clients: 1,
            ready_clients: 1,
            blocked_clients: 0,
            pending_tasks: 0,
            in_progress_tasks: 0,
            overdue_tasks: 0,
          },
          clients: [],
          tasks: [],
          total: 1,
          page: 1,
          page_size: 20,
        },
      });
    }
    return Promise.resolve({ data: { items: [], total: 0, page: 1, page_size: 20 } });
  });

  render(<AgencyPage />);

  await waitFor(() => {
    expect(screen.getByText("没有符合当前条件的客户")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Implement state copy**

Use these exact user-facing messages:

- Load failure title: `工作台数据加载失败`
- Load failure description: `请检查网络或后端服务状态后重试。`
- Retry button: `重新加载`
- No client title: `还没有客户`
- No client description: `初始化新客户后，这里会显示上线准备度和下一步动作。`
- No match title: `没有符合当前条件的客户`
- No match description: `调整搜索词或筛选条件后再试。`

- [ ] **Step 3: Add filters**

Client queue filters:

- Search customer name.
- Readiness filter: `全部客户`, `已具备上线条件`, `需补齐配置`.
- Task filter: `全部任务状态`, `待处理`, `进行中`, `逾期`.

Map filter changes to `/ops/workbench` query params:

```ts
{
  q,
  readiness: "all" | "ready" | "blocked",
  task_status: "all" | "pending" | "in_progress" | "overdue",
}
```

- [ ] **Step 4: Run frontend tests**

Run:

```bash
cd frontend/apps/admin
pnpm exec vitest run 'src/app/(dashboard)/agency/__tests__/page.test.tsx'
```

Expected: PASS.

---

## Task 6: Backend Filter And Ordering Tests

**Files:**
- Modify: `backend/tests/test_api/test_operator_workbench.py`
- Modify: `backend/app/services/ops.py`

- [ ] **Step 1: Write failing tests for filtering**

Add:

```python
@pytest.mark.anyio
async def test_ops_workbench_supports_search_filter(
    self,
    platform_admin_client: AsyncClient,
    sample_tenants,
):
    resp = await platform_admin_client.get("/api/v1/ops/workbench?q=测试客户1")

    assert resp.status_code == 200
    names = [client["name"] for client in resp.json()["clients"]]
    assert names
    assert all("测试客户1" in name for name in names)


@pytest.mark.anyio
async def test_ops_workbench_supports_blocked_filter(
    self,
    platform_admin_client: AsyncClient,
    sample_tenants,
):
    resp = await platform_admin_client.get("/api/v1/ops/workbench?readiness=blocked")

    assert resp.status_code == 200
    assert resp.json()["clients"]
    assert all(client["readiness"]["ready"] is False for client in resp.json()["clients"])
```

- [ ] **Step 2: Verify backend filter tests pass**

Run:

```bash
cd backend
uv run pytest tests/test_api/test_operator_workbench.py::TestOpsWorkbench -q
```

Expected: PASS.

- [ ] **Step 3: Run broader backend checks**

Run:

```bash
cd backend
uv run pytest tests/test_api/test_operator_workbench.py tests/test_api/test_ops_launch.py -q
uv run ruff check app tests/test_api/test_operator_workbench.py
```

Expected: PASS.

---

## Task 7: Browser Verification

**Files:**
- No code changes.

- [ ] **Step 1: Start or reuse Admin dev server**

If no dev server is running:

```bash
cd frontend
pnpm dev:admin
```

Expected: Admin available at `http://localhost:3000`.

- [ ] **Step 2: Open `/agency`**

Use the in-app browser and inspect:

```text
http://localhost:3000/agency
```

Expected:

- Heading: `代运营工作台`
- Cards include `已具备上线条件`, `需补齐配置`, `逾期任务`, `待办任务`
- Client queue includes `上线准备度`
- A client row shows `x/4`
- Row action includes `上线检查`

- [ ] **Step 3: Verify row-level task creation**

Click the row primary next action.

Expected:

- `新建待办任务` modal opens.
- `关联客户` is preselected.
- `任务标题` is prefilled with the row `task_title`.
- User can edit the title before creating.

- [ ] **Step 4: Verify checklist**

Click `上线检查`.

Expected:

- Modal title starts with `上线检查清单`.
- Progress and checklist items match backend response.

- [ ] **Step 5: Verify filters**

Use readiness and task filters.

Expected:

- Network call includes `readiness` or `task_status`.
- Empty filtered result shows `没有符合当前条件的客户`.

---

## Final Verification

Run:

```bash
cd backend
uv run pytest tests/test_api/test_operator_workbench.py tests/test_api/test_ops_launch.py -q
uv run ruff check app tests/test_api/test_operator_workbench.py
```

Run:

```bash
cd frontend/apps/admin
pnpm exec vitest run 'src/app/(dashboard)/agency/__tests__/page.test.tsx'
```

Run:

```bash
cd frontend
pnpm lint:admin
```

Browser smoke:

- `http://localhost:3000/agency`
- Verify cards, readiness, row action, task modal prefill, checklist modal, filters, failure/empty states.

---

## Beads

- Main implementation bead: `yimatong-2vm`
- Suggested close reason after implementation:
  - `已完成代运营工作台闭环优化：新增工作台聚合接口，前端展示客户上线准备度、缺失配置、任务摘要和下一步动作，支持筛选、空状态、任务预填创建和浏览器验收。`

## Self-Review

- Spec coverage: backend aggregation, frontend queue, task creation, checklist, filters, empty/failure states, tests, lint, browser smoke are covered.
- Placeholder scan: no unresolved placeholder steps are present.
- Type consistency: schema names match frontend type names and API response fields.
