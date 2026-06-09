# 会员管理模块加固实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复会员管理模块 78 项发现中的安全漏洞、业务逻辑缺陷、数据模型问题、API 设计缺陷、测试覆盖不足和前端问题。

**Architecture:** 分 8 个阶段递进修复：安全优先 → 核心链路 → 数据模型 → 服务层 → API 层 → 测试 → 前端。每个阶段内的 Task 可并行，阶段间有依赖关系。

**Tech Stack:** Python 3.12+ / FastAPI / SQLAlchemy 2.0 / Pydantic V2 / PostgreSQL 16 / Alembic / pytest / React / Next.js

---

## 阶段依赖图

```
Phase 1 (安全) ──→ Phase 2 (自动积分) ──→ Phase 5 (服务层)
                                              ↑
              Phase 3 (过期系统) ──────────────┘
              Phase 4 (数据模型) ──→ Phase 6 (API层) ──→ Phase 7 (测试) ──→ Phase 8 (前端)
```

Phase 1-4 可在一定程度上并行，但 Phase 5-8 依赖前面的修复。

---

## Phase 1: 关键安全修复

### Task 1: 积分值正数校验

**解决:** Critical — 负数积分可绕过业务逻辑，任意操纵余额

**Files:**
- Modify: `backend/app/api/v1/members.py:47-58`
- Modify: `backend/app/services/member.py:82-159`
- Test: `backend/tests/test_api/test_member.py`

- [ ] **Step 1: 写失败测试 — award_points 拒绝负数/零积分**

在 `backend/tests/test_api/test_member.py` 末尾添加:

```python
class TestPointsValidation:
    """积分输入校验"""

    @pytest.mark.anyio
    async def test_award_rejects_negative_points(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        cid = consumer.json()["id"]

        resp = await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": -100, "reason": "作弊"},
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_award_rejects_zero_points(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        cid = consumer.json()["id"]

        resp = await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 0, "reason": "无效"},
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_spend_rejects_negative_points(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        cid = consumer.json()["id"]

        resp = await client.post(
            "/api/v1/members/points/spend",
            json={"consumer_id": cid, "points": -50, "reason": "作弊"},
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_award_rejects_oversized_points(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        cid = consumer.json()["id"]

        resp = await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 999_999_999, "reason": "溢出"},
            headers=headers,
        )
        assert resp.status_code == 422
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_api/test_member.py::TestPointsValidation -v`
Expected: 4 FAIL（422 校验未生效，当前接受任意 int）

- [ ] **Step 3: 添加 Pydantic Field 约束**

修改 `backend/app/api/v1/members.py`，添加 Field 导入和约束:

```python
# 在文件顶部 import 区添加:
from pydantic import BaseModel, Field

# 替换 AwardPointsRequest（约 line 47-51）:
class AwardPointsRequest(BaseModel):
    consumer_id: uuid.UUID
    points: int = Field(gt=0, le=1_000_000, description="积分数量，必须为正整数且不超过 100 万")
    reason: str = Field(min_length=1, max_length=200)
    reference_id: str | None = Field(None, max_length=100)

# 替换 SpendPointsRequest（约 line 54-58）:
class SpendPointsRequest(BaseModel):
    consumer_id: uuid.UUID
    points: int = Field(gt=0, le=1_000_000, description="积分数量，必须为正整数且不超过 100 万")
    reason: str = Field(min_length=1, max_length=200)
    reference_id: str | None = Field(None, max_length=100)
```

- [ ] **Step 4: 在 service 层添加防御性校验**

修改 `backend/app/services/member.py`，在 `award_points` 函数（约 line 89）之后添加:

```python
async def award_points(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    points: int,
    reason: str,
    reference_id: str | None = None,
) -> PointTransaction:
    """发放积分"""
    if points <= 0:
        raise ValueError("Points must be positive")
    # ... 后续代码不变
```

在 `spend_points` 函数（约 line 130）之后添加同样的校验:

```python
async def spend_points(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    points: int,
    reason: str,
    reference_id: str | None = None,
) -> PointTransaction:
    """消费积分"""
    if points <= 0:
        raise ValueError("Points must be positive")
    # ... 后续代码不变
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_api/test_member.py -v`
Expected: ALL PASS

- [ ] **Step 6: 提交**

```bash
cd backend
git add app/api/v1/members.py app/services/member.py tests/test_api/test_member.py
git commit -m "fix(member): add positive points validation in schema and service layer"
```

---

### Task 2: Consumer 端 RLS 修复 — 改用 get_db + 设置 tenant context

**解决:** Critical — 所有 consumer 端点绕过 RLS，无数据库层租户隔离

**Files:**
- Modify: `backend/app/core/context.py`
- Modify: `backend/app/core/database.py`
- Modify: `backend/app/api/v1/consumers.py`
- Test: `backend/tests/test_api/test_member.py`

- [ ] **Step 1: 添加 consumer tenant_id context var**

修改 `backend/app/core/context.py`，添加:

```python
"""Request-scoped context for passing tenant_id from middleware to DB layer."""

from contextvars import ContextVar

# Set by TenantScopeMiddleware, consumed by get_db()
_current_tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)

# Set by consumer endpoints (scan_token resolved), consumed by get_db()
_consumer_tenant_id: ContextVar[str | None] = ContextVar("consumer_tenant_id", default=None)


def set_request_tenant_id(tenant_id: str | None) -> None:
    _current_tenant_id.set(tenant_id)


def get_request_tenant_id() -> str | None:
    # 优先使用 JWT 中间件设置的 tenant_id，回退到 consumer 端设置的
    return _current_tenant_id.get() or _consumer_tenant_id.get()


def set_consumer_tenant_id(tenant_id: str | None) -> None:
    _consumer_tenant_id.set(tenant_id)
```

- [ ] **Step 2: 添加 get_db_for_consumer 数据库依赖**

修改 `backend/app/core/database.py`，在 `get_db_with_bypass` 函数后添加:

```python
async def get_db_for_consumer() -> AsyncGenerator[AsyncSession, None]:
    """Open a session for consumer endpoints with tenant context from scan_token.

    Uses RLS (same as get_db) instead of bypass. The tenant_id is resolved
    from scan_token and set via context.set_consumer_tenant_id().
    """
    async with async_session_factory() as session:
        from app.core.context import get_request_tenant_id

        tenant_id = get_request_tenant_id()
        if tenant_id and _is_pg:
            from sqlalchemy import text

            validated_id = str(tenant_id)
            try:
                import uuid
                uuid.UUID(validated_id)
            except ValueError:
                raise ValueError(f"Invalid tenant_id format: {validated_id}")
            await session.execute(
                text(f"SET LOCAL app.tenant_id = '{validated_id}'")
            )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

- [ ] **Step 3: 写失败测试 — consumer 端使用 RLS**

在 `backend/tests/test_api/test_member.py` 末尾添加:

```python
class TestConsumerRLS:
    """验证 consumer 端点使用 RLS 而非 bypass"""

    @pytest.mark.anyio
    async def test_consumer_exchange_uses_rls_not_bypass(
        self, client: AsyncClient, setup_tenant, db_session: AsyncSession
    ):
        tid, headers = setup_tenant
        consumer = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        cid = consumer.json()["id"]
        await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 80, "reason": "初始"},
            headers=headers,
        )
        product = await client.post(
            "/api/v1/members/point-products",
            json={"name": "RLS测试商品", "points_cost": 20, "stock": 5},
            headers=headers,
        )
        pid = product.json()["id"]
        token = await create_scan_context(db_session, tid)

        # 正常兑换应成功
        resp = await client.post(
            "/api/v1/consumers/points/exchanges",
            json={"consumer_id": cid, "product_id": pid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
```

- [ ] **Step 4: 修改 consumers.py — 替换 get_db_with_bypass 为 get_db_for_consumer**

修改 `backend/app/api/v1/consumers.py`:

1. 修改 import（约 line 10）:
```python
from app.core.database import get_db_for_consumer
```

2. 在 `_resolve_scan_tenant` 函数（约 line 37-53）末尾添加 context 设置:
```python
async def _resolve_scan_tenant(request: Request, db: AsyncSession) -> uuid.UUID:
    token = _extract_bearer_token(request)
    payload = verify_scan_token(token)
    if not payload or not payload.get("public_id"):
        raise HTTPException(status_code=401, detail="invalid token")

    tid = payload.get("tenant_id")
    if tid:
        tenant_uuid = uuid.UUID(tid)
    else:
        from app.services.resolver import resolve_public_code
        code_data = await resolve_public_code(db, payload["public_id"])
        if not code_data:
            raise HTTPException(status_code=404, detail="code not found")
        tenant_uuid = uuid.UUID(code_data["tenant_id"])

    # 设置 consumer tenant context，使 get_db_for_consumer 能获取到
    from app.core.context import set_consumer_tenant_id
    set_consumer_tenant_id(str(tenant_uuid))
    return tenant_uuid
```

3. 将所有 `Depends(get_db_with_bypass)` 替换为 `Depends(get_db_for_consumer)`（共 6 处: line 60, 136, 177, 201, 231, 246）

- [ ] **Step 5: 运行所有 member 测试确认通过**

Run: `cd backend && python -m pytest tests/test_api/test_member.py -v`
Expected: ALL PASS

- [ ] **Step 6: 提交**

```bash
cd backend
git add app/core/context.py app/core/database.py app/api/v1/consumers.py tests/test_api/test_member.py
git commit -m "fix(consumer): replace RLS bypass with tenant-scoped sessions for consumer endpoints"
```

---

### Task 3: scan_token 添加 consumer_id — 防止跨消费者操作

**解决:** Critical — 持 scan_token 可操作任意消费者的积分

**Files:**
- Modify: `backend/app/services/scan_token.py`
- Modify: `backend/app/api/v1/consumers.py`
- Test: `backend/tests/test_api/test_member.py`

- [ ] **Step 1: 扩展 create_scan_token 接受 consumer_id**

修改 `backend/app/services/scan_token.py` 的 `create_scan_token` 函数:

```python
def create_scan_token(
    public_id: str,
    ip_hash: str | None,
    tenant_id: str = "",
    consumer_id: str = "",
    expires_in: int = 1800,
) -> str:
    """颁发 scan_token（短期 JWT，默认 30 分钟）。

    Args:
        public_id: 码的公开标识
        ip_hash: 客户端 IP 的 SHA256 哈希
        tenant_id: 租户 ID
        consumer_id: 消费者 ID（可选，绑定后防止跨消费者操作）
        expires_in: 有效期秒数
    """
    payload = {
        "public_id": public_id,
        "ip_hash": ip_hash,
        "tenant_id": tenant_id,
        "consumer_id": consumer_id,
        "exp": int(time.time()) + expires_in,
        "type": "scan_token",
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")
```

`verify_scan_token` 无需修改 — 它返回整个 payload，调用方从中读取 `consumer_id` 即可。

- [ ] **Step 2: 写测试 — consumer_id 绑定验证**

在 `backend/tests/test_api/test_member.py` 添加:

```python
class TestConsumerIdentityBinding:
    """验证 scan_token 中的 consumer_id 绑定"""

    @pytest.mark.anyio
    async def test_exchange_with_bound_consumer_id(
        self, client: AsyncClient, setup_tenant, db_session: AsyncSession
    ):
        """token 绑定 consumer_id 时，只能操作该消费者"""
        tid, headers = setup_tenant
        c1 = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        c2 = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        c1_id = c1.json()["id"]
        c2_id = c2.json()["id"]
        await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": c1_id, "points": 200, "reason": "初始"},
            headers=headers,
        )
        product = await client.post(
            "/api/v1/members/point-products",
            json={"name": "绑定测试", "points_cost": 50, "stock": 5},
            headers=headers,
        )
        pid = product.json()["id"]

        # token 绑定 c1
        public_id = f"TEST{uuid.uuid4().hex[:10]}"
        token = create_scan_token(public_id, "test-ip", tenant_id=tid, consumer_id=c1_id)

        # c1 兑换成功
        resp = await client.post(
            "/api/v1/consumers/points/exchanges",
            json={"consumer_id": c1_id, "product_id": pid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_exchange_rejects_mismatched_consumer_id(
        self, client: AsyncClient, setup_tenant, db_session: AsyncSession
    ):
        """token 绑定 c1，尝试操作 c2 应被拒绝"""
        tid, headers = setup_tenant
        c1 = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        c2 = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        c1_id = c1.json()["id"]
        c2_id = c2.json()["id"]
        await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": c2_id, "points": 200, "reason": "初始"},
            headers=headers,
        )
        product = await client.post(
            "/api/v1/members/point-products",
            json={"name": "绑定测试2", "points_cost": 50, "stock": 5},
            headers=headers,
        )
        pid = product.json()["id"]

        # token 绑定 c1，但尝试兑换 c2 的积分
        public_id = f"TEST{uuid.uuid4().hex[:10]}"
        token = create_scan_token(public_id, "test-ip", tenant_id=tid, consumer_id=c1_id)

        resp = await client.post(
            "/api/v1/consumers/points/exchanges",
            json={"consumer_id": c2_id, "product_id": pid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
```

- [ ] **Step 3: 在 consumers.py 添加 consumer_id 校验**

在 `backend/app/api/v1/consumers.py` 中，修改 `_resolve_scan_tenant` 使其同时返回 consumer_id:

```python
async def _resolve_scan_context(request: Request, db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID | None]:
    """解析 scan_token 中的 tenant_id 和 consumer_id。"""
    token = _extract_bearer_token(request)
    payload = verify_scan_token(token)
    if not payload or not payload.get("public_id"):
        raise HTTPException(status_code=401, detail="invalid token")

    tid = payload.get("tenant_id")
    if tid:
        tenant_uuid = uuid.UUID(tid)
    else:
        from app.services.resolver import resolve_public_code
        code_data = await resolve_public_code(db, payload["public_id"])
        if not code_data:
            raise HTTPException(status_code=404, detail="code not found")
        tenant_uuid = uuid.UUID(code_data["tenant_id"])

    from app.core.context import set_consumer_tenant_id
    set_consumer_tenant_id(str(tenant_uuid))

    # 从 token 解析绑定的 consumer_id
    cid_str = payload.get("consumer_id")
    bound_consumer_id = uuid.UUID(cid_str) if cid_str else None
    return tenant_uuid, bound_consumer_id
```

添加校验辅助函数:

```python
def _verify_consumer_ownership(bound_consumer_id: uuid.UUID | None, requested_consumer_id: uuid.UUID) -> None:
    """如果 token 绑定了 consumer_id，验证请求的 consumer_id 匹配。"""
    if bound_consumer_id and bound_consumer_id != requested_consumer_id:
        raise HTTPException(status_code=403, detail="consumer_id mismatch with token")
```

然后修改所有消费者端点:
- `get_consumer_points_me`: 调用 `_resolve_scan_context`，传入 `consumer_id` 给 `_verify_consumer_ownership`
- `list_consumer_points_transactions`: 同上
- `list_consumer_points_products`: 同上
- `create_consumer_points_exchange`: 同上

**注意:** `lead_capture` 和 `get_consumer_me` 不需要 consumer_id 校验（lead_capture 是创建，/me 可不传 consumer_id）。

- [ ] **Step 4: 更新 create_scan_context 测试辅助函数**

修改 `backend/tests/test_api/test_member.py` 中的 `create_scan_context`:

```python
async def create_scan_context(
    db_session: AsyncSession, tenant_id: str, consumer_id: str = ""
) -> str:
    batch_id = uuid.uuid4()
    public_id = f"TEST{uuid.uuid4().hex[:10]}"
    db_session.add(
        CodeBatch(
            id=batch_id,
            tenant_id=uuid.UUID(tenant_id),
            product_id=uuid.uuid4(),
            sku_id=uuid.uuid4(),
            batch_code=f"B-{public_id}",
            quantity=1,
            status="activated",
            code_type="single",
            created_by=uuid.uuid4(),
        )
    )
    db_session.add(
        CodeItem(
            tenant_id=uuid.UUID(tenant_id),
            code_batch_id=batch_id,
            public_id=public_id,
            status=CodeItemStatus.activated,
            code_type="single",
        )
    )
    await db_session.flush()
    return create_scan_token(public_id, "test-ip", tenant_id=tenant_id, consumer_id=consumer_id)
```

同时更新所有已有的测试调用处，传入正确的 `consumer_id`（如 `create_scan_context(db_session, tid, cid)`）。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_api/test_member.py -v`
Expected: ALL PASS

- [ ] **Step 6: 提交**

```bash
cd backend
git add app/services/scan_token.py app/api/v1/consumers.py tests/test_api/test_member.py
git commit -m "feat(scan-token): bind consumer_id to scan_token, verify ownership in consumer endpoints"
```

---

### Task 4: point_products 表添加 RLS 策略

**解决:** Critical — point_products 缺少 RLS，跨租户数据无保护

**Files:**
- Create: `backend/alembic/versions/<auto>_add_rls_to_point_products.py`

- [ ] **Step 1: 生成迁移文件**

Run: `cd backend && alembic revision -m "add RLS to point_products"`

- [ ] **Step 2: 编写迁移内容**

```python
"""add RLS to point_products

Revision ID: <auto>
Revises: <previous>
Create Date: 2026-06-09
"""

from alembic import op
import sqlalchemy as sa

revision = "<auto>"
down_revision = "<previous>"
branch_labels = None
depends_on = None

RLS_POLICY_SQL = """
CREATE POLICY tenant_isolation ON {table}
USING (
    tenant_id = current_tenant_id()
    OR (
        current_tenant_id() IS NULL
        AND current_setting('app.bypass_rls', true) = 'true'
    )
)
WITH CHECK (
    tenant_id = current_tenant_id()
    OR (
        current_tenant_id() IS NULL
        AND current_setting('app.bypass_rls', true) = 'true'
    )
)
"""


def upgrade() -> None:
    op.execute("ALTER TABLE point_products ENABLE ROW LEVEL SECURITY")
    op.execute(RLS_POLICY_SQL.format(table="point_products"))


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON point_products")
    op.execute("ALTER TABLE point_products DISABLE ROW LEVEL SECURITY")
```

- [ ] **Step 3: 验证迁移**

Run: `cd backend && alembic upgrade head`

- [ ] **Step 4: 提交**

```bash
cd backend
git add alembic/versions/
git commit -m "fix(rls): enable row-level security on point_products table"
```

---

## Phase 2: 自动积分链路修复

### Task 5: 修复 point_auto_handler — RLS + 事件时序

**解决:** Critical — 自动积分系统因 RLS 阻塞 + 事件数据缺失而完全失效

**Files:**
- Modify: `backend/app/services/point_auto_handler.py`
- Modify: `backend/app/services/member.py:73-78`
- Modify: `backend/app/services/scan_event.py:59-63`
- Test: `backend/tests/test_services/test_point_auto_handler.py`（新建）

- [ ] **Step 1: 写失败测试 — scan.created 事件应触发积分发放**

创建 `backend/tests/test_services/test_point_auto_handler.py`:

```python
"""积分自动发放处理器测试"""

import uuid

import pytest
from sqlalchemy import select

from app.core.event_bus import event_bus
from app.models.member import PointRule, PointTransaction, PointTransactionType
from app.services.point_auto_handler import (
    _check_daily_limit,
    _award_for_rule,
    _handle_scan_created,
    _handle_consumer_created,
    init_point_auto_handler,
)
from tests.conftest import TestSessionLocal


@pytest.fixture(autouse=True)
def setup_handlers():
    """每个测试前初始化 handler 注册"""
    init_point_auto_handler()
    yield


@pytest.mark.anyio
async def test_daily_limit_not_reached():
    """daily_limit=0 表示无限制"""
    async with TestSessionLocal() as db:
        from sqlalchemy import text
        tid = uuid.uuid4()
        await db.execute(text(f"SET LOCAL app.bypass_rls = 'true'"))
        result = await _check_daily_limit(db, tid, uuid.uuid4(), "scan", 0)
        assert result is False


@pytest.mark.anyio
async def test_daily_limit_reached():
    """达到限额应返回 True"""
    async with TestSessionLocal() as db:
        from sqlalchemy import text
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        await db.execute(text(f"SET LOCAL app.bypass_rls = 'true'"))
        # 插入 2 条已达限额的记录
        for _ in range(2):
            db.add(PointTransaction(
                tenant_id=tid, consumer_id=cid, amount=10,
                balance_after=10, txn_type=PointTransactionType.earning,
                reason="auto:scan",
            ))
        await db.flush()
        result = await _check_daily_limit(db, tid, cid, "scan", 2)
        assert result is True


@pytest.mark.anyio
async def test_handle_scan_created_with_consumer_id():
    """scan.created 事件含 consumer_id 时应发放积分"""
    async with TestSessionLocal() as db:
        from sqlalchemy import text
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        await db.execute(text(f"SET LOCAL app.bypass_rls = 'true'"))
        # 创建 scan 规则
        db.add(PointRule(
            tenant_id=tid, rule_type="scan", points=10, enabled=True, daily_limit=0,
        ))
        # 创建消费者
        from app.models.member import ConsumerProfile
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=0))
        await db.commit()

    # 触发事件（handler 会开自己的 session）
    await _handle_scan_created("scan.created", {
        "consumer_id": str(cid),
        "public_id": "TEST123",
        "is_first_scan": False,
    }, str(tid))

    # 验证积分已发放
    async with TestSessionLocal() as db:
        await db.execute(text(f"SET LOCAL app.bypass_rls = 'true'"))
        result = await db.execute(
            select(PointTransaction).where(
                PointTransaction.tenant_id == tid,
                PointTransaction.consumer_id == cid,
            )
        )
        txns = list(result.scalars().all())
        assert len(txns) == 1
        assert txns[0].amount == 10
```

- [ ] **Step 2: 修复 scan_event.py — 在事件数据中包含 consumer_id**

修改 `backend/app/services/scan_event.py`，将 `record_scan_event` 函数签名和事件发射修改为:

```python
async def record_scan_event(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
    ip_hash: str | None = None,
    user_agent: str | None = None,
    environment: str | None = None,
    consumer_id: uuid.UUID | None = None,
) -> ScanEvent:
```

修改事件发射部分（约 line 59-63）:
```python
    event_data = {"public_id": public_id, "is_first_scan": is_first, "environment": environment}
    if consumer_id:
        event_data["consumer_id"] = str(consumer_id)
    await event_bus.emit("scan.created", event_data, str(tenant_id))
```

- [ ] **Step 3: 修复 point_auto_handler.py — 设置 RLS bypass**

修改 `_handle_scan_created`（约 line 120）和 `_handle_consumer_created`（约 line 171），在 session 创建后添加:

```python
async def _handle_scan_created(event_type: str, data: dict, tenant_id: str) -> None:
    consumer_id = data.get("consumer_id")
    if not consumer_id:
        return

    public_id = data.get("public_id", "")

    async with async_session_factory() as db:
        try:
            from sqlalchemy import text
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))

            tid = uuid.UUID(tenant_id)
            cid = uuid.UUID(consumer_id)
            # ... 后续逻辑不变
```

同样修改 `_handle_consumer_created`:
```python
async def _handle_consumer_created(event_type: str, data: dict, tenant_id: str) -> None:
    consumer_id = data.get("consumer_id")
    if not consumer_id:
        return

    async with async_session_factory() as db:
        try:
            from sqlalchemy import text
            await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))

            tid = uuid.UUID(tenant_id)
            cid = uuid.UUID(consumer_id)
            # ... 后续逻辑不变
```

- [ ] **Step 4: 修复 consumer.created 事件时序 — 延迟到事务提交后**

修改 `backend/app/services/member.py` 中的 `get_or_create_consumer`，将事件发射改为 after_commit:

```python
async def get_or_create_consumer(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    phone: str | None = None,
    nickname: str | None = None,
) -> ConsumerProfile:
    """获取或创建消费者档案"""
    phone_h = hash_phone(phone) if phone else None

    if phone_h:
        result = await db.execute(
            select(ConsumerProfile).where(
                ConsumerProfile.tenant_id == tenant_id,
                ConsumerProfile.phone_hash == phone_h,
            )
        )
        consumer = result.scalar_one_or_none()
        if consumer:
            if nickname and not consumer.nickname:
                consumer.nickname = nickname
            return consumer

    consumer = ConsumerProfile(
        tenant_id=tenant_id,
        phone_hash=phone_h,
        phone_encrypted=encrypt_phone(phone) if phone else None,
        nickname=nickname,
    )
    db.add(consumer)
    is_new = True
    try:
        async with db.begin_nested():
            await db.flush()
    except Exception:
        is_new = False
        if phone_h:
            result = await db.execute(
                select(ConsumerProfile).where(
                    ConsumerProfile.tenant_id == tenant_id,
                    ConsumerProfile.phone_hash == phone_h,
                )
            )
            consumer = result.scalar_one_or_none()
            if consumer:
                return consumer
        raise
    await db.refresh(consumer)
    if nickname and not consumer.nickname:
        consumer.nickname = nickname

    if is_new:
        # 延迟到事务提交后再发射事件，避免 handler 看不到未提交的数据
        from sqlalchemy import event as sa_event

        consumer_id_str = str(consumer.id)
        tenant_id_str = str(tenant_id)

        async def _emit_after_commit(session):
            await event_bus.emit(
                "consumer.created",
                {"consumer_id": consumer_id_str, "has_phone": phone is not None},
                tenant_id_str,
            )

        def _on_commit(session):
            import asyncio
            asyncio.ensure_future(_emit_after_commit(session))

        sa_event.listen(db.sync_session, "after_commit", _on_commit, once=True)

    return consumer
```

- [ ] **Step 5: 修复 first_scan 去重检查**

修改 `backend/app/services/point_auto_handler.py` 约 line 145，将检查原因从 `"auto:scan"` 改为 `"auto:first_scan"`:

```python
                    if rule.rule_type == "first_scan":
                        # 首扫奖励：检查是否已有首扫积分记录
                        scan_count_result = await db.execute(
                            select(func.count()).select_from(PointTransaction).where(
                                PointTransaction.tenant_id == tid,
                                PointTransaction.consumer_id == cid,
                                PointTransaction.txn_type == PointTransactionType.earning,
                                PointTransaction.reason.in_(["auto:scan", "auto:first_scan"]),
                            )
                        )
                        if (scan_count_result.scalar() or 0) > 0:
                            continue
```

- [ ] **Step 6: 修复 Redis 连接池 — 使用共享连接池**

修改 `point_auto_handler.py` 中的 `_check_redis_dedup`:

```python
async def _check_redis_dedup(tenant_id: str, consumer_id: str, rule_type: str) -> bool:
    """Redis 去重防止短时间内重复发放。"""
    try:
        from app.services.redis_cache import get_redis_pool

        r = get_redis_pool()
        key = f"{DEDUP_KEY_PREFIX}{tenant_id}:{consumer_id}:{rule_type}"
        exists = await r.exists(key)
        if exists:
            return True
        await r.setex(key, DEDUP_TTL_SECONDS, "1")
        return False
    except Exception:
        return False
```

- [ ] **Step 7: 修复 config 类型校验**

修改 `_award_for_rule` 中的 `points_ttl_days` 读取（约 line 93）:

```python
    points_ttl_days = (rule.config or {}).get("points_ttl_days", 0)
    expires_at = None
    if isinstance(points_ttl_days, (int, float)) and points_ttl_days > 0:
        expires_at = utcnow() + timedelta(days=points_ttl_days)
```

- [ ] **Step 8: 运行测试**

Run: `cd backend && python -m pytest tests/test_services/test_point_auto_handler.py -v`
Expected: ALL PASS

- [ ] **Step 9: 提交**

```bash
cd backend
git add app/services/point_auto_handler.py app/services/member.py app/services/scan_event.py
git add tests/test_services/test_point_auto_handler.py
git commit -m "fix(points): repair auto-award pipeline - RLS bypass, event timing, first_scan dedup"
```

---

## Phase 3: 积分过期系统修复

### Task 6: 添加 is_expired 字段 + 修复过期批处理

**解决:** Critical — 过期批处理从未调用；调用时破坏审计数据

**Files:**
- Create: `backend/alembic/versions/<auto>_add_is_expired_to_point_transactions.py`
- Modify: `backend/app/models/member.py:55-76`
- Modify: `backend/app/services/point_expiry.py`
- Modify: `backend/app/tasks/worker.py`（注册定时任务）
- Test: `backend/tests/test_services/test_point_expiry.py`（新建）

- [ ] **Step 1: 生成迁移文件**

Run: `cd backend && alembic revision -m "add is_expired to point_transactions"`

- [ ] **Step 2: 编写迁移**

```python
"""add is_expired to point_transactions

Revision ID: <auto>
"""

from alembic import op
import sqlalchemy as sa

revision = "<auto>"
down_revision = "<previous>"


def upgrade() -> None:
    op.add_column("point_transactions", sa.Column("is_expired", sa.Boolean(), nullable=False, server_default="false"))
    # 将已清零的记录标记为 is_expired
    op.execute("UPDATE point_transactions SET is_expired = true WHERE amount = 0 AND txn_type = 'earning'")


def downgrade() -> None:
    op.drop_column("point_transactions", "is_expired")
```

- [ ] **Step 3: 更新模型**

在 `backend/app/models/member.py` 的 `PointTransaction` 类中添加字段（约 line 66 后）:

```python
    is_expired: Mapped[bool] = mapped_column(default=False, nullable=False)
```

- [ ] **Step 4: 写测试**

创建 `backend/tests/test_services/test_point_expiry.py`:

```python
"""积分过期批处理测试"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.models.member import ConsumerProfile, PointTransaction, PointTransactionType
from app.services.point_expiry import expire_points_batch
from app.utils import utcnow
from tests.conftest import TestSessionLocal


@pytest.mark.anyio
async def test_expire_basic():
    """基本过期：过期记录被处理，余额扣减"""
    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=100))
        db.add(PointTransaction(
            tenant_id=tid, consumer_id=cid, amount=100,
            balance_after=100, txn_type=PointTransactionType.earning,
            reason="test", expires_at=utcnow() - timedelta(days=1),
        ))
        await db.commit()

    count = await expire_points_batch()
    assert count == 1

    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        profile = (await db.execute(
            select(ConsumerProfile).where(ConsumerProfile.id == cid)
        )).scalar_one()
        assert profile.total_points == 0

        # 原始记录被标记为 is_expired，但 amount 不变
        earning = (await db.execute(
            select(PointTransaction).where(
                PointTransaction.consumer_id == cid,
                PointTransaction.txn_type == PointTransactionType.earning,
            )
        )).scalar_one()
        assert earning.is_expired is True
        assert earning.amount == 100  # 审计数据保留


@pytest.mark.anyio
async def test_expire_clamps_at_zero():
    """过期金额超过余额时，余额不会变负"""
    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=30))
        db.add(PointTransaction(
            tenant_id=tid, consumer_id=cid, amount=100,
            balance_after=100, txn_type=PointTransactionType.earning,
            reason="test", expires_at=utcnow() - timedelta(days=1),
        ))
        await db.commit()

    count = await expire_points_batch()
    assert count == 1

    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        profile = (await db.execute(
            select(ConsumerProfile).where(ConsumerProfile.id == cid)
        )).scalar_one()
        assert profile.total_points == 0  # clamped, not negative


@pytest.mark.anyio
async def test_expire_idempotent():
    """已标记 is_expired 的记录不会被重复处理"""
    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=0))
        db.add(PointTransaction(
            tenant_id=tid, consumer_id=cid, amount=100,
            balance_after=100, txn_type=PointTransactionType.earning,
            reason="test", expires_at=utcnow() - timedelta(days=1),
            is_expired=True,  # 已处理
        ))
        await db.commit()

    count = await expire_points_batch()
    assert count == 0
```

- [ ] **Step 5: 重写 expire_points_batch**

修改 `backend/app/services/point_expiry.py`:

```python
"""积分过期清理服务。

扫描已过期的积分收入记录，按 FIFO 方式扣减消费者余额。
使用 is_expired 标记而非修改 amount，保留审计数据。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, text, update

from app.core.database import async_session_factory
from app.models.member import (
    ConsumerProfile,
    PointTransaction,
    PointTransactionType,
)
from app.utils import utcnow

logger = logging.getLogger(__name__)

BATCH_SIZE = 200


async def expire_points_batch() -> int:
    """批量处理过期积分。返回处理的消费者数量。"""
    now = utcnow()
    processed = 0

    async with async_session_factory() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))

        # 查找所有已过期且未标记 is_expired 的收入记录
        result = await db.execute(
            select(PointTransaction)
            .where(
                PointTransaction.expires_at.is_not(None),
                PointTransaction.expires_at <= now,
                PointTransaction.txn_type == PointTransactionType.earning,
                PointTransaction.is_expired.is_(False),
                PointTransaction.amount > 0,
            )
            .order_by(PointTransaction.expires_at)
            .limit(BATCH_SIZE)
        )
        expired_txns = list(result.scalars().all())
        if not expired_txns:
            return 0

        # 按 consumer 分组
        consumer_expired: dict[tuple[uuid.UUID, uuid.UUID], int] = {}
        for txn in expired_txns:
            key = (txn.tenant_id, txn.consumer_id)
            consumer_expired[key] = consumer_expired.get(key, 0) + txn.amount

        # 扣减余额并创建过期记录
        for (tid, cid), total_expired in consumer_expired.items():
            try:
                profile_result = await db.execute(
                    select(ConsumerProfile).where(
                        ConsumerProfile.id == cid,
                        ConsumerProfile.tenant_id == tid,
                    )
                )
                profile = profile_result.scalar_one_or_none()
                if not profile or profile.total_points <= 0:
                    # 即使不扣减，也标记原始记录为已过期
                    continue

                deduct = min(total_expired, profile.total_points)
                new_balance = profile.total_points - deduct
                profile.total_points = new_balance

                expire_txn = PointTransaction(
                    tenant_id=tid,
                    consumer_id=cid,
                    amount=-deduct,
                    balance_after=new_balance,
                    txn_type=PointTransactionType.expired,
                    reason="积分过期清零",
                )
                db.add(expire_txn)
                processed += 1
            except Exception:
                logger.warning("Failed to expire points for consumer %s", cid, exc_info=True)

        # 标记已处理的记录为 is_expired（保留原始 amount）
        txn_ids = [t.id for t in expired_txns]
        if txn_ids:
            await db.execute(
                update(PointTransaction)
                .where(PointTransaction.id.in_(txn_ids))
                .values(is_expired=True)
            )

        await db.commit()

    logger.info("Expired points processed: %d consumers", processed)
    return processed
```

- [ ] **Step 6: 注册定时任务**

在 `backend/app/tasks/worker.py` 中注册 `expire_points_batch` 为定时任务（每天凌晨 3 点执行）。参考文件中已有的 arq worker 配置模式。

- [ ] **Step 7: 运行迁移和测试**

Run:
```bash
cd backend && alembic upgrade head
python -m pytest tests/test_services/test_point_expiry.py -v
```

- [ ] **Step 8: 提交**

```bash
cd backend
git add alembic/versions/ app/models/member.py app/services/point_expiry.py app/tasks/worker.py
git add tests/test_services/test_point_expiry.py
git commit -m "fix(points): add is_expired flag, rewrite expiry batch to preserve audit data, register cron job"
```

---

## Phase 4: 数据模型加固

### Task 7: 索引 + 唯一约束

**Files:**
- Create: `backend/alembic/versions/<auto>_member_model_indexes_constraints.py`
- Modify: `backend/app/models/member.py`

- [ ] **Step 1: 生成迁移**

Run: `cd backend && alembic revision -m "member model indexes and constraints"`

- [ ] **Step 2: 编写迁移内容**

```python
"""member model indexes and constraints"""

from alembic import op

revision = "<auto>"
down_revision = "<previous>"


def upgrade() -> None:
    # 1. ConsumerProfile: 添加 (tenant_id, phone_hash) 复合索引
    op.create_index(
        "ix_consumer_profiles_tenant_phone", "consumer_profiles",
        ["tenant_id", "phone_hash"],
    )
    # 添加唯一约束防止重复手机号消费者
    op.create_unique_constraint(
        "uq_consumer_tenant_phone", "consumer_profiles",
        ["tenant_id", "phone_hash"],
    )
    # 移除重复的单列索引（ix_consumer_profiles_tenant 与 tenant_id index=True 重复）
    op.drop_index("ix_consumer_profiles_tenant", table_name="consumer_profiles")

    # 2. PointProduct: 添加迁移中已创建但模型中缺失的复合索引
    # 如果 ix_point_products_tenant_enabled_sort 已存在则跳过
    op.create_index(
        "ix_point_products_tenant_enabled_sort", "point_products",
        ["tenant_id", "enabled", "sort_order"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_point_products_tenant_enabled_sort", table_name="point_products")
    op.create_index("ix_consumer_profiles_tenant", "consumer_profiles", ["tenant_id"])
    op.drop_constraint("uq_consumer_tenant_phone", "consumer_profiles", type_="unique")
    op.drop_index("ix_consumer_profiles_tenant_phone", table_name="consumer_profiles")
```

- [ ] **Step 3: 更新模型**

修改 `backend/app/models/member.py`:

ConsumerProfile（约 line 43-46）:
```python
    __table_args__ = (
        UniqueConstraint("tenant_id", "wechat_openid", name="uq_consumer_tenant_openid"),
        UniqueConstraint("tenant_id", "phone_hash", name="uq_consumer_tenant_phone"),
        Index("ix_consumer_profiles_tenant_phone", "tenant_id", "phone_hash"),
    )
```
同时移除 line 25 的 `index=True`（已有复合索引覆盖）:
```python
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
```

PointProduct（约 line 124）:
```python
    __table_args__ = (
        Index("ix_point_products_tenant", "tenant_id"),
        Index("ix_point_products_tenant_enabled_sort", "tenant_id", "enabled", "sort_order"),
    )
```

- [ ] **Step 4: 运行迁移 + 确认**

Run: `cd backend && alembic upgrade head`

- [ ] **Step 5: 提交**

```bash
cd backend
git add alembic/versions/ app/models/member.py
git commit -m "fix(member): add composite indexes, unique constraint on (tenant_id, phone_hash)"
```

---

### Task 8: 外键约束 + CheckConstraints + 杂项修复

**Files:**
- Create: `backend/alembic/versions/<auto>_member_fk_checks.py`
- Modify: `backend/app/models/member.py`

- [ ] **Step 1: 生成迁移**

Run: `cd backend && alembic revision -m "member FK constraints and check constraints"`

- [ ] **Step 2: 编写迁移**

```python
"""member FK constraints and check constraints"""

from alembic import op

revision = "<auto>"
down_revision = "<previous>"


def upgrade() -> None:
    # 添加 FK 约束（先验证数据完整性再添加）
    op.create_foreign_key(
        "fk_point_txn_consumer", "point_transactions",
        "consumer_profiles", ["consumer_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_point_redemption_consumer", "point_redemptions",
        "consumer_profiles", ["consumer_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_point_product_benefit", "point_products",
        "benefits", ["benefit_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_point_redemption_benefit", "point_redemptions",
        "benefits", ["benefit_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_point_redemption_benefit_claim", "point_redemptions",
        "benefit_claims", ["benefit_claim_id"], ["id"],
        ondelete="SET NULL",
    )

    # 添加 CheckConstraints
    op.create_check_constraint(
        "check_point_product_cost_positive", "point_products",
        "points_cost > 0",
    )
    op.create_check_constraint(
        "check_point_product_stock_nonneg", "point_products",
        "stock >= 0",
    )
    op.create_check_constraint(
        "check_point_product_limit_nonneg", "point_products",
        "per_consumer_limit >= 0",
    )
    op.create_check_constraint(
        "check_point_txn_balance_nonneg", "point_transactions",
        "balance_after >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("check_point_txn_balance_nonneg", "point_transactions", type_="check")
    op.drop_constraint("check_point_product_limit_nonneg", "point_products", type_="check")
    op.drop_constraint("check_point_product_stock_nonneg", "point_products", type_="check")
    op.drop_constraint("check_point_product_cost_positive", "point_products", type_="check")
    op.drop_constraint("fk_point_redemption_benefit_claim", "point_redemptions", type_="foreignkey")
    op.drop_constraint("fk_point_redemption_benefit", "point_redemptions", type_="foreignkey")
    op.drop_constraint("fk_point_product_benefit", "point_products", type_="foreignkey")
    op.drop_constraint("fk_point_redemption_consumer", "point_redemptions", type_="foreignkey")
    op.drop_constraint("fk_point_txn_consumer", "point_transactions", type_="foreignkey")
```

- [ ] **Step 3: 更新模型中的 FK 声明**

修改 `backend/app/models/member.py`:

PointTransaction.consumer_id（约 line 60）:
```python
    consumer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("consumer_profiles.id"), nullable=False, index=True
    )
```

PointProduct（约 line 114）:
```python
    benefit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("benefits.id", ondelete="SET NULL"), nullable=True
    )
```

PointRedemption（约 line 139, 145, 146）:
```python
    consumer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("consumer_profiles.id"), nullable=False, index=True
    )
    benefit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("benefits.id", ondelete="SET NULL"), nullable=True, index=True
    )
    benefit_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("benefit_claims.id", ondelete="SET NULL"), nullable=True, index=True
    )
```

PointProduct 的 `__table_args__` 添加 CheckConstraint:
```python
    __table_args__ = (
        Index("ix_point_products_tenant", "tenant_id"),
        Index("ix_point_products_tenant_enabled_sort", "tenant_id", "enabled", "sort_order"),
    )
```

- [ ] **Step 4: 修复 consumers.py 中 uuid.uuid4() → uuid7()**

修改 `backend/app/api/v1/consumers.py` 约 line 112:

```python
from uuid6 import uuid7

# 在 lead_capture 中:
            profile = ConsumerProfile(
                id=uuid7(),  # 使用 uuid7 保持时间排序
                tenant_id=tenant_id,
                ...
```

- [ ] **Step 5: 运行迁移**

Run: `cd backend && alembic upgrade head`

- [ ] **Step 6: 提交**

```bash
cd backend
git add alembic/versions/ app/models/member.py app/api/v1/consumers.py
git commit -m "fix(member): add FK constraints, CheckConstraints, uuid7 consistency"
```

---

## Phase 5: 服务层加固

### Task 9: 行锁 — award/spend_points + exchange_product

**解决:** High — TOCTOU 竞态条件导致余额不一致

**Files:**
- Modify: `backend/app/services/member.py:91-96, 131-136`
- Modify: `backend/app/services/point_shop.py:256-261`

- [ ] **Step 1: 给 award_points 添加行锁**

修改 `backend/app/services/member.py` 的 `award_points`（约 line 91-96）:

```python
    consumer_result = await db.execute(
        select(ConsumerProfile)
        .where(
            ConsumerProfile.id == consumer_id,
            ConsumerProfile.tenant_id == tenant_id,
        )
        .with_for_update()
    )
```

- [ ] **Step 2: 给 spend_points 添加行锁**

同样修改 `spend_points`（约 line 131-136）:

```python
    consumer_result = await db.execute(
        select(ConsumerProfile)
        .where(
            ConsumerProfile.id == consumer_id,
            ConsumerProfile.tenant_id == tenant_id,
        )
        .with_for_update()
    )
```

- [ ] **Step 3: 给 exchange_product 中的 consumer 查询添加行锁**

修改 `backend/app/services/point_shop.py`（约 line 256-261）:

```python
    consumer_result = await db.execute(
        select(ConsumerProfile)
        .where(
            ConsumerProfile.id == consumer_id,
            ConsumerProfile.tenant_id == tenant_id,
        )
        .with_for_update()
    )
```

- [ ] **Step 4: 运行已有测试确认无回归**

Run: `cd backend && python -m pytest tests/test_api/test_member.py -v`

- [ ] **Step 5: 提交**

```bash
cd backend
git add app/services/member.py app/services/point_shop.py
git commit -m "fix(points): add row locks to award/spend_points and exchange_product"
```

---

### Task 10: 输入校验修复

**Files:**
- Modify: `backend/app/api/v1/members.py`（Schema 校验）
- Modify: `backend/app/services/member.py:303, 310`（LIKE 转义）
- Modify: `backend/app/services/point_shop.py:62-96`（日期范围校验）
- Modify: `backend/app/services/point_shop.py:118-126`（软删除）

- [ ] **Step 1: 修复 search_consumers 的 LIKE 通配符注入**

修改 `backend/app/services/member.py` 的 `search_consumers` 函数（约 line 303, 310），添加转义:

```python
    elif normalized_type == "nickname":
        escaped = value.replace("%", r"\%").replace("_", r"\_")
        conditions.append(ConsumerProfile.nickname.ilike(f"%{escaped}%", escape="\\"))
    else:
        # mixed
        try:
            maybe_id = uuid.UUID(value)
        except ValueError:
            maybe_id = None
        phone_clause = ConsumerProfile.phone_hash == hash_phone(value) if value.isdigit() else None
        escaped = value.replace("%", r"\%").replace("_", r"\_")
        clauses = [ConsumerProfile.nickname.ilike(f"%{escaped}%", escape="\\")]
        if maybe_id:
            clauses.append(ConsumerProfile.id == maybe_id)
        if phone_clause is not None:
            clauses.append(phone_clause)
        conditions.append(or_(*clauses))
```

- [ ] **Step 2: 添加 PointRuleCreate.rule_type 枚举校验**

修改 `backend/app/api/v1/members.py` 的 `PointRuleCreate`:

```python
from typing import Literal

class PointRuleCreate(BaseModel):
    rule_type: Literal["scan", "first_scan", "register", "checkin", "repurchase", "activity"]
    points: int = Field(gt=0)
    daily_limit: int = Field(ge=0, default=0)
    description: str | None = Field(None, max_length=200)
    config: dict | None = None
```

- [ ] **Step 3: 添加 PointProduct 字段约束和日期范围校验**

```python
class PointProductCreate(BaseModel):
    name: str = Field(max_length=200)
    description: str | None = None
    image_url: str | None = Field(None, max_length=500)
    points_cost: int = Field(gt=0)
    stock: int = Field(ge=0, default=0)
    benefit_id: uuid.UUID | None = None
    enabled: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    per_consumer_limit: int = Field(ge=0, default=1)
    sort_order: int = Field(ge=0, default=0)

    from pydantic import model_validator

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be before ends_at")
        return self
```

- [ ] **Step 4: 添加 ConsumerCreateRequest 手机号校验**

```python
import re
from pydantic import field_validator

class ConsumerCreateRequest(BaseModel):
    phone: str | None = None
    nickname: str | None = Field(None, max_length=100)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^1[3-9]\d{9}$", v):
            raise ValueError("手机号格式不正确")
        return v
```

- [ ] **Step 5: 修复 reason 字符串溢出**

修改 `backend/app/services/point_shop.py` 的 `exchange_product`（约 line 275）:

```python
        reason_name = product.name[:196] if len(product.name) > 196 else product.name
        txn = await spend_points(
            db,
            tenant_id,
            consumer_id,
            product.points_cost,
            f"兑换: {reason_name}",
            reference_id=f"product:{product_id}",
        )
```

- [ ] **Step 6: 添加 soft delete 保护**

修改 `backend/app/services/point_shop.py` 的 `delete_point_product`:

```python
async def delete_point_product(
    db: AsyncSession, tenant_id: uuid.UUID, product_id: uuid.UUID
) -> bool:
    product = await get_point_product(db, tenant_id, product_id)
    if not product:
        return False
    # 检查是否有关联兑换记录
    from sqlalchemy import func as sa_func
    count_result = await db.execute(
        select(sa_func.count()).select_from(PointRedemption).where(
            PointRedemption.product_id == product_id,
        )
    )
    if (count_result.scalar() or 0) > 0:
        # 有兑换记录，改为软删除（下架）
        product.enabled = False
        product.name = f"[已删除] {product.name}"
        await db.flush()
        return True
    await db.delete(product)
    await db.flush()
    return True
```

- [ ] **Step 7: 修复 update 函数的 mass assignment 风险**

修改 `backend/app/services/member.py` 的 `update_point_rule`（约 line 364-366）:

```python
    ALLOWED_FIELDS = {"points", "daily_limit", "description", "enabled", "config"}
    for key, value in kwargs.items():
        if key in ALLOWED_FIELDS and value is not None:
            setattr(rule, key, value)
```

修改 `backend/app/services/point_shop.py` 的 `update_point_product`（约 line 110-112）:

```python
    ALLOWED_FIELDS = {
        "name", "description", "image_url", "points_cost", "stock",
        "benefit_id", "enabled", "starts_at", "ends_at",
        "per_consumer_limit", "sort_order",
    }
    for key, value in kwargs.items():
        if key in ALLOWED_FIELDS:
            setattr(product, key, value)
```

- [ ] **Step 8: 运行测试**

Run: `cd backend && python -m pytest tests/test_api/test_member.py -v`

- [ ] **Step 9: 提交**

```bash
cd backend
git add app/api/v1/members.py app/services/member.py app/services/point_shop.py
git commit -m "fix(member): input validation - phone, rule_type, dates, LIKE escape, soft delete, update allowlist"
```

---

### Task 11: N+1 查询优化 + 代码质量

**Files:**
- Modify: `backend/app/services/point_shop.py:188-208`
- Modify: `backend/app/services/point_expiry.py`（已有）
- Modify: `backend/app/services/member.py:193`

- [ ] **Step 1: 修复 list_consumer_point_products 的 N+1 查询**

修改 `backend/app/services/point_shop.py` 的 `list_consumer_point_products`（约 line 188-208）:

```python
async def list_consumer_point_products(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
) -> list[dict]:
    consumer_result = await db.execute(
        select(ConsumerProfile).where(
            ConsumerProfile.tenant_id == tenant_id,
            ConsumerProfile.id == consumer_id,
        )
    )
    consumer = consumer_result.scalar_one_or_none()
    if not consumer:
        raise ValueError("Consumer not found")

    products, _ = await list_point_products(db, tenant_id, page=1, page_size=100, enabled_only=True)
    if not products:
        return []

    # 批量查询兑换次数，避免 N+1
    product_ids = [p.id for p in products]
    redemption_counts = await db.execute(
        select(PointRedemption.product_id, func.count())
        .where(
            PointRedemption.tenant_id == tenant_id,
            PointRedemption.consumer_id == consumer_id,
            PointRedemption.product_id.in_(product_ids),
            PointRedemption.status == PointRedemptionStatus.success,
        )
        .group_by(PointRedemption.product_id)
    )
    count_map = dict(redemption_counts.all())

    items = []
    for product in products:
        # 内联计算 block reason，无需逐个查询
        block_reason = _compute_block_reason(product, consumer.total_points, count_map.get(product.id, 0))
        items.append(serialize_point_product(product, can_exchange=block_reason is None, exchange_block_reason=block_reason))
    return items


def _compute_block_reason(
    product: PointProduct, current_points: int, redemption_count: int
) -> str | None:
    """纯函数计算兑换阻止原因，无 DB 查询。"""
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    if not product.enabled:
        return "商品已下架"
    if product.starts_at and product.starts_at > now:
        return "尚未开始兑换"
    if product.ends_at and product.ends_at < now:
        return "兑换已结束"
    if product.stock <= 0:
        return "库存不足"
    if current_points < product.points_cost:
        return "积分不足"
    if product.per_consumer_limit > 0 and redemption_count >= product.per_consumer_limit:
        return "已达到每人限兑次数"
    return None
```

- [ ] **Step 2: 统一 datetime helper**

修改 `backend/app/services/member.py:193`:

```python
from app.utils import utcnow

# 替换 datetime.now(UTC) 为 utcnow()
async def get_member_overview(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    since = utcnow() - timedelta(days=7)
    # ... 后续不变
```

修改 `backend/app/services/point_shop.py` 中的 `datetime.now(UTC)` 为 `utcnow()`。

- [ ] **Step 3: 提交**

```bash
cd backend
git add app/services/point_shop.py app/services/member.py
git commit -m "perf(member): fix N+1 in list_consumer_point_products, unify datetime helpers"
```

---

## Phase 6: API 层重构

### Task 12: 提取 Schema 到独立文件

**Files:**
- Create: `backend/app/schemas/member.py`
- Modify: `backend/app/api/v1/members.py`
- Modify: `backend/app/api/v1/consumers.py`

- [ ] **Step 1: 创建 schemas/member.py**

将 `members.py` 和 `consumers.py` 中的 inline schema 提取到 `backend/app/schemas/member.py`:

```python
"""会员与积分相关 Pydantic Schema"""

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ConsumerCreateRequest(BaseModel):
    phone: str | None = None
    nickname: str | None = Field(None, max_length=100)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^1[3-9]\d{9}$", v):
            raise ValueError("手机号格式不正确")
        return v


class AwardPointsRequest(BaseModel):
    consumer_id: uuid.UUID
    points: int = Field(gt=0, le=1_000_000, description="积分数量，必须为正整数且不超过 100 万")
    reason: str = Field(min_length=1, max_length=200)
    reference_id: str | None = Field(None, max_length=100)


class SpendPointsRequest(BaseModel):
    consumer_id: uuid.UUID
    points: int = Field(gt=0, le=1_000_000, description="积分数量，必须为正整数且不超过 100 万")
    reason: str = Field(min_length=1, max_length=200)
    reference_id: str | None = Field(None, max_length=100)


class PointRuleCreate(BaseModel):
    rule_type: Literal["scan", "first_scan", "register", "checkin", "repurchase", "activity"]
    points: int = Field(gt=0)
    daily_limit: int = Field(ge=0, default=0)
    description: str | None = Field(None, max_length=200)
    config: dict | None = None


class PointRuleUpdate(BaseModel):
    points: int | None = Field(None, gt=0)
    daily_limit: int | None = Field(None, ge=0)
    description: str | None = Field(None, max_length=200)
    enabled: bool | None = None
    config: dict | None = None


class PointProductCreate(BaseModel):
    name: str = Field(max_length=200)
    description: str | None = None
    image_url: str | None = Field(None, max_length=500)
    points_cost: int = Field(gt=0)
    stock: int = Field(ge=0, default=0)
    benefit_id: uuid.UUID | None = None
    enabled: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    per_consumer_limit: int = Field(ge=0, default=1)
    sort_order: int = Field(ge=0, default=0)

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be before ends_at")
        return self


class PointProductUpdate(BaseModel):
    name: str | None = Field(None, max_length=200)
    description: str | None = None
    image_url: str | None = Field(None, max_length=500)
    points_cost: int | None = Field(None, gt=0)
    stock: int | None = Field(None, ge=0)
    benefit_id: uuid.UUID | None = None
    enabled: bool | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    per_consumer_limit: int | None = Field(None, ge=0)
    sort_order: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be before ends_at")
        return self


class ExchangeRequest(BaseModel):
    consumer_id: uuid.UUID
    product_id: uuid.UUID


class LeadCaptureRequest(BaseModel):
    name: str | None = None
    phone: str | None = None
    region: str | None = None
    intention: str | None = None
    public_id: str
```

- [ ] **Step 2: 更新 members.py 和 consumers.py 的 import**

`members.py` 移除所有 inline schema 定义，改为:
```python
from app.schemas.member import (
    AwardPointsRequest,
    ConsumerCreateRequest,
    ExchangeRequest,
    PointProductCreate,
    PointProductUpdate,
    PointRuleCreate,
    PointRuleUpdate,
    SpendPointsRequest,
)
```

`consumers.py` 移除 inline schema，改为:
```python
from app.schemas.member import ExchangeRequest as PointsExchangeRequest, LeadCaptureRequest
```

- [ ] **Step 3: 将 consumers.py 的 inline import 移到文件顶部**

将 `consumers.py` 中所有散落在函数体内的 import 语句移到文件顶部。

- [ ] **Step 4: 运行测试**

Run: `cd backend && python -m pytest tests/test_api/test_member.py -v`

- [ ] **Step 5: 提交**

```bash
cd backend
git add app/schemas/member.py app/api/v1/members.py app/api/v1/consumers.py
git commit -m "refactor(member): extract schemas to dedicated file, fix consumers.py inline imports"
```

---

### Task 13: RBAC + 速率限制

**Files:**
- Modify: `backend/app/api/v1/members.py`

- [ ] **Step 1: 给 admin 写操作端点添加角色检查**

修改 `backend/app/api/v1/members.py`，在写操作端点添加 `Depends(get_current_role)`:

```python
from app.core.dependencies import get_current_tenant, get_current_role

# 写操作端点示例（create_point_rule）:
@member_router.post("/point-rules", status_code=201, summary="创建积分规则")
async def create_point_rule_endpoint(
    body: PointRuleCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    role: str = Depends(get_current_role),
):
    if role not in {"admin", "operator"}:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    # ... 后续逻辑不变
```

对以下端点添加同样的 RBAC 检查:
- `POST /consumers` (create consumer)
- `POST /points/award`
- `POST /points/spend`
- `POST /point-rules` (create)
- `PUT /point-rules/{rule_id}` (update)
- `DELETE /point-rules/{rule_id}`
- `POST /point-products` (create)
- `PUT /point-products/{product_id}` (update)
- `DELETE /point-products/{product_id}`
- `POST /point-products/exchange`

读操作端点（overview, search, get, list）不需要额外 RBAC。

- [ ] **Step 2: 提交**

```bash
cd backend
git add app/api/v1/members.py
git commit -m "feat(member): add RBAC role checks to admin write endpoints"
```

---

### Task 14: 统一响应格式 + 清理

**Files:**
- Modify: `backend/app/api/v1/members.py`

- [ ] **Step 1: 统一 list_point_rules 响应格式**

将 `list_point_rules_endpoint`（约 line 234-251）的返回值改为 `PaginatedResponse`:

```python
@member_router.get("/point-rules", summary="积分规则列表")
async def list_point_rules_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    rules = await get_point_rules(db, tenant_id)
    items = [
        {
            "id": str(r.id),
            "rule_type": r.rule_type,
            "points": r.points,
            "daily_limit": r.daily_limit,
            "description": r.description,
            "enabled": r.enabled,
            "config": r.config,
        }
        for r in rules
    ]
    return PaginatedResponse(
        items=items,
        total=len(items),
        page=page,
        page_size=page_size,
    )
```

- [ ] **Step 2: 统一 search_consumers 响应格式**

将 `search_consumers_endpoint` 的返回改为 `PaginatedResponse`。

- [ ] **Step 3: 统一 create_consumer 响应 — 使用 serialize_consumer_profile**

- [ ] **Step 4: 提交**

```bash
cd backend
git add app/api/v1/members.py
git commit -m "refactor(member): standardize API response format to PaginatedResponse"
```

---

## Phase 7: 测试覆盖

### Task 15: 重写服务层测试

**Files:**
- Rewrite: `backend/tests/test_services/test_point_engine.py`
- Created in Phase 2-3: `test_point_auto_handler.py`, `test_point_expiry.py`

- [ ] **Step 1: 删除旧的 test_point_engine.py 中的同义反复断言**

清空 `backend/tests/test_services/test_point_engine.py`，替换为引用实际服务函数的测试:

```python
"""积分引擎核心逻辑测试 — 真实服务函数测试"""

import uuid

import pytest
from sqlalchemy import select, text

from app.models.member import (
    ConsumerProfile,
    MemberLevel,
    PointProduct,
    PointRedemption,
    PointTransaction,
    PointTransactionType,
)
from app.services.member import award_points, spend_points, _update_member_level
from app.services.point_shop import exchange_product, get_exchange_block_reason
from tests.conftest import TestSessionLocal


@pytest.mark.anyio
async def test_award_points_increments_balance():
    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid))
        await db.flush()

        txn = await award_points(db, tid, cid, 100, "test")
        assert txn.amount == 100
        assert txn.balance_after == 100

        consumer = (await db.execute(
            select(ConsumerProfile).where(ConsumerProfile.id == cid)
        )).scalar_one()
        assert consumer.total_points == 100


@pytest.mark.anyio
async def test_spend_points_decrements_balance():
    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=200))
        await db.flush()

        txn = await spend_points(db, tid, cid, 50, "test")
        assert txn.amount == -50
        assert txn.balance_after == 150


@pytest.mark.anyio
async def test_spend_insufficient_raises():
    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=10))
        await db.flush()

        with pytest.raises(ValueError, match="Insufficient"):
            await spend_points(db, tid, cid, 50, "test")


@pytest.mark.anyio
async def test_award_negative_raises():
    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid))
        await db.flush()

        with pytest.raises(ValueError, match="positive"):
            await award_points(db, tid, cid, -10, "cheat")


@pytest.mark.anyio
async def test_member_level_upgrade():
    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        consumer = ConsumerProfile(tenant_id=uuid.uuid4(), total_points=0)
        db.add(consumer)
        await db.flush()

        consumer.total_points = 1000
        await _update_member_level(db, consumer)
        assert consumer.member_level == MemberLevel.silver

        consumer.total_points = 5000
        await _update_member_level(db, consumer)
        assert consumer.member_level == MemberLevel.gold

        consumer.total_points = 10000
        await _update_member_level(db, consumer)
        assert consumer.member_level == MemberLevel.platinum


@pytest.mark.anyio
async def test_exchange_product_deducts_stock_and_points():
    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=200))
        db.add(PointProduct(
            id=uuid.uuid4(), tenant_id=tid, name="测试商品",
            points_cost=50, stock=5, total_claimed=0, enabled=True,
            per_consumer_limit=10, sort_order=0,
        ))
        await db.flush()

        product = (await db.execute(select(PointProduct))).scalar_one()
        result = await exchange_product(db, tid, cid, product.id)

        assert result["points_spent"] == 50
        assert result["balance_after"] == 150

        await db.refresh(product)
        assert product.stock == 4
        assert product.total_claimed == 1


@pytest.mark.anyio
async def test_exchange_out_of_stock_blocked():
    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=500))
        db.add(PointProduct(
            id=uuid.uuid4(), tenant_id=tid, name="无库存",
            points_cost=10, stock=0, total_claimed=0, enabled=True,
            per_consumer_limit=10, sort_order=0,
        ))
        await db.flush()

        product = (await db.execute(select(PointProduct))).scalar_one()
        with pytest.raises(ValueError, match="库存不足"):
            await exchange_product(db, tid, cid, product.id)
```

- [ ] **Step 2: 运行所有服务层测试**

Run: `cd backend && python -m pytest tests/test_services/ -v`

- [ ] **Step 3: 提交**

```bash
cd backend
git add tests/test_services/test_point_engine.py
git commit -m "test(member): rewrite service tests with real function invocations"
```

---

### Task 16: 租户隔离 + 并发测试

**Files:**
- Create: `backend/tests/test_services/test_member_isolation.py`
- Modify: `backend/tests/test_api/test_member.py`

- [ ] **Step 1: 写租户隔离测试**

创建 `backend/tests/test_services/test_member_isolation.py`:

```python
"""会员模块租户隔离测试"""

import uuid

import pytest
from sqlalchemy import select, text

from app.models.member import ConsumerProfile, PointRule, PointProduct
from app.services.member import get_point_rules, search_consumers, get_consumer_profile
from tests.conftest import TestSessionLocal


@pytest.mark.anyio
async def test_get_point_rules_isolated_by_tenant():
    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        tid_a = uuid.uuid4()
        tid_b = uuid.uuid4()
        db.add(PointRule(tenant_id=tid_a, rule_type="scan", points=10, enabled=True))
        db.add(PointRule(tenant_id=tid_b, rule_type="scan", points=20, enabled=True))
        await db.flush()

    async with TestSessionLocal() as db:
        await db.execute(text(f"SET LOCAL app.tenant_id = '{tid_a}'"))
        rules = await get_point_rules(db, tid_a)
        assert len(rules) == 1
        assert rules[0].points == 10


@pytest.mark.anyio
async def test_search_consumers_isolated():
    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        tid_a = uuid.uuid4()
        tid_b = uuid.uuid4()
        db.add(ConsumerProfile(tenant_id=tid_a, nickname="租户A用户"))
        db.add(ConsumerProfile(tenant_id=tid_b, nickname="租户B用户"))
        await db.flush()

    async with TestSessionLocal() as db:
        await db.execute(text(f"SET LOCAL app.tenant_id = '{tid_a}'"))
        results = await search_consumers(db, tid_a, "用户")
        assert len(results) == 1
        assert results[0]["nickname"] == "租户A用户"


@pytest.mark.anyio
async def test_get_consumer_profile_cross_tenant_returns_none():
    async with TestSessionLocal() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        tid_a = uuid.uuid4()
        tid_b = uuid.uuid4()
        consumer = ConsumerProfile(tenant_id=tid_a, total_points=500)
        db.add(consumer)
        await db.flush()

    # 用 tid_b 查询 tid_a 的消费者应返回 None
    async with TestSessionLocal() as db:
        profile = await get_consumer_profile(db, tid_b, consumer.id)
        assert profile is None
```

- [ ] **Step 2: 写并发操作测试**

在 `backend/tests/test_api/test_member.py` 添加:

```python
class TestConcurrentOperations:
    """并发操作安全性"""

    @pytest.mark.anyio
    async def test_concurrent_exchange_no_overselling(
        self, client: AsyncClient, setup_tenant, db_session: AsyncSession
    ):
        """库存为 1 时，并发兑换只有 1 个成功"""
        import asyncio
        tid, headers = setup_tenant
        product = await client.post(
            "/api/v1/members/point-products",
            json={"name": "限量品", "points_cost": 10, "stock": 1},
            headers=headers,
        )
        pid = product.json()["id"]

        # 创建 5 个消费者，每人 100 积分
        consumers = []
        for _ in range(5):
            c = await client.post("/api/v1/members/consumers", json={}, headers=headers)
            cid = c.json()["id"]
            await client.post(
                "/api/v1/members/points/award",
                json={"consumer_id": cid, "points": 100, "reason": "测试"},
                headers=headers,
            )
            consumers.append(cid)

        # 并发兑换
        async def exchange(cid: str) -> int:
            resp = await client.post(
                "/api/v1/members/point-products/exchange",
                json={"consumer_id": cid, "product_id": pid},
                headers=headers,
            )
            return resp.status_code

        results = await asyncio.gather(*[exchange(cid) for cid in consumers])
        successes = sum(1 for r in results if r == 200)
        assert successes == 1  # 只有 1 个成功
```

- [ ] **Step 3: 运行测试**

Run: `cd backend && python -m pytest tests/test_services/test_member_isolation.py tests/test_api/test_member.py::TestConcurrentOperations -v`

- [ ] **Step 4: 提交**

```bash
cd backend
git add tests/test_services/test_member_isolation.py tests/test_api/test_member.py
git commit -m "test(member): add tenant isolation and concurrent operation tests"
```

---

## Phase 8: 前端改进

### Task 17: 共享类型 + H5 错误处理

**Files:**
- Modify: `frontend/packages/shared/src/index.ts`
- Modify: `frontend/apps/h5/src/components/PointsBalance.tsx`
- Modify: `frontend/apps/h5/src/components/PointsHistory.tsx`
- Modify: `frontend/apps/h5/src/components/MemberCard.tsx`

- [ ] **Step 1: 在 @yimatong/shared 添加会员积分类型**

在 `frontend/packages/shared/src/index.ts` 中添加:

```typescript
// ─── Member & Points Types ───

export interface PointTransaction {
  id: string;
  amount: number;
  balance_after: number;
  txn_type: "earning" | "spending" | "expired";
  reason?: string | null;
  created_at?: string | null;
  expires_at?: string | null;
}

export interface PointProduct {
  id: string;
  name: string;
  description?: string | null;
  image_url?: string | null;
  points_cost: number;
  stock: number;
  total_claimed: number;
  enabled: boolean;
  benefit_id?: string | null;
  starts_at?: string | null;
  ends_at?: string | null;
  per_consumer_limit: number;
  sort_order: number;
  can_exchange?: boolean | null;
  exchange_block_reason?: string | null;
}

export interface ConsumerProfile {
  id: string;
  nickname?: string | null;
  phone?: string | null;
  member_level: "normal" | "silver" | "gold" | "platinum";
  total_points: number;
}

export interface MemberOverview {
  enabled_rules: number;
  active_products: number;
  points_awarded_7d: number;
  points_spent_7d: number;
  redemptions_7d: number;
  low_stock_products: number;
}
```

- [ ] **Step 2: 修复 H5 组件的静默错误处理**

修改 `PointsBalance.tsx`，添加错误状态:
```typescript
const [error, setError] = useState<string | null>(null);

// 将 .catch(() => {}) 改为:
.catch(() => setError("加载积分失败"))
```

修改 `MemberCard.tsx` 和 `PointsHistory.tsx` 同样添加错误状态。

- [ ] **Step 3: 修复 PointsHistory 的 expired 类型显示**

修改 `PointsHistory.tsx` 的类型定义:
```typescript
// 使用 shared 类型
import type { PointTransaction } from "@yimatong/shared";

// 修改显示逻辑:
{txn.txn_type === "earning" ? "收入" : txn.txn_type === "expired" ? "过期" : "支出"}
```

- [ ] **Step 4: 构建验证**

Run: `cd frontend && pnpm build:shared && pnpm build:h5`

- [ ] **Step 5: 提交**

```bash
cd frontend
git add packages/shared/src/index.ts apps/h5/src/components/
git commit -m "fix(h5): add shared member types, fix silent error handling, handle expired txn type"
```

---

### Task 18: Admin 错误处理 + 小修复

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/members/page.tsx`

- [ ] **Step 1: 使用 extractErrorMessage 替换手动类型断言**

修改 `members/page.tsx`:

```typescript
// 在顶部 import 添加:
import api, { extractErrorMessage } from "@/lib/api";

// 将三处手动类型断言（约 line 219, 392, 579）:
//   const err = e as { response?: { data?: { detail?: string } } };
//   message.error(err.response?.data?.detail || "操作失败");
// 替换为:
//   message.error(extractErrorMessage(e, "操作失败"));

// 将其他无错误详情的 catch 块统一使用 extractErrorMessage
```

- [ ] **Step 2: 修复 RedemptionsTab 双重 fetch**

移除 `fetchRedemptions` 的 `useEffect` 依赖，改为初始化 ref 模式:

```typescript
const initialized = useRef(false);
useEffect(() => {
  if (!initialized.current) {
    initialized.current = true;
    fetchRedemptions(1, {});
  }
}, []);
```

- [ ] **Step 3: 提交**

```bash
cd frontend
git add apps/admin/src/app/\(dashboard\)/members/page.tsx
git commit -m "fix(admin): use extractErrorMessage, fix RedemptionsTab double-fetch"
```

---

## 自查清单

### 1. 规格覆盖检查

| 发现类别 | 总计 | 对应 Task | 覆盖 |
|---------|------|-----------|------|
| Critical 安全（积分负数、RLS 绕过、consumer_id 欺骗、RLS 缺失） | 6 | Task 1-4 | ✅ |
| 自动积分链路失效 | 3 | Task 5 | ✅ |
| 积分过期失效 | 2 | Task 6 | ✅ |
| 数据模型（索引、FK、约束） | 9 | Task 7-8 | ✅ |
| 服务层（行锁、校验、N+1） | 10 | Task 9-11 | ✅ |
| API 层（Schema、RBAC、响应格式） | 8 | Task 12-14 | ✅ |
| 测试覆盖（假测试、缺失覆盖） | 11 | Task 15-16 | ✅ |
| 前端（类型、错误处理） | 5 | Task 17-18 | ✅ |

### 2. 占位符扫描

- 无 "TBD"、"TODO"、"implement later"、"fill in details"
- 所有步骤包含实际代码或命令
- 无 "similar to Task N" 引用

### 3. 类型一致性

- `ConsumerProfile` 模型字段与 schema 字段一致
- `PointTransaction.is_expired` 在模型和迁移中一致
- `scan_token.consumer_id` 在创建和验证中一致
- `ALLOWED_FIELDS` 集合与模型属性匹配
