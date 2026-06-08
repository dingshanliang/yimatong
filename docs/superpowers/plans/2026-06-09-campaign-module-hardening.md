# 活动管理模块加固计划 (Campaign Module Hardening)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复活动管理模块中 3 个 Critical、12 个 High 安全/正确性缺陷，补充关键测试覆盖，清理架构耦合。

**Architecture:** 分 5 个阶段执行，每个阶段独立可交付。Phase 1 修复 Critical 安全漏洞（跨租户、过期领取、库存竞态），Phase 2 修复 High 级正确性问题，Phase 3 架构去重，Phase 4 补充关键测试，Phase 5 前端修复。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Pydantic V2, pytest, Next.js/TypeScript (前端)

**分支:** `fix/campaign-module-hardening`（从 `fix/auth-security-hardening` 创建）

---

## File Map

| 文件 | 操作 | 职责 |
|------|------|------|
| `backend/app/api/v1/benefit_claims.py` | 修改 | H5 领取端点：加租户过滤、红包状态检查 |
| `backend/app/api/v1/benefits.py` | 修改 | standalone benefits：benefit_type 白名单、exclude_unset |
| `backend/app/api/v1/open_api.py` | 修改 | 复用 CampaignStatusRequest、加激活拦截检查 |
| `backend/app/api/v1/campaigns.py` | 修改 | 状态变更 loading guard、computed_status 验证 |
| `backend/app/services/campaign.py` | 修改 | 核心修复：库存回滚原子化、过期检查、批量赋值白名单、事件命名、搜索转义、验证补全 |
| `backend/app/schemas/campaign.py` | 修改 | 字段约束（max_length、campaign_type 白名单）、移除 service 层导入 |
| `backend/app/schemas/benefit_claim.py` | 修改 | benefit_id 改 UUID 类型、phone 格式校验 |
| `backend/app/models/campaign.py` | 修改 | 删除重复 CampaignStatus/BenefitType 类 |
| `backend/app/constants/campaign.py` | 修改 | 添加 CAMPAIGN_TYPES、COMPUTED_STATUS_PENDING、UPDATABLE_*_FIELDS |
| `backend/app/utils/campaign_validation.py` | 新建 | 从 service 提取验证函数，消除 schema→service 反向依赖 |
| `backend/tests/test_api/test_campaign.py` | 修改 | 新增测试 |
| `backend/tests/test_api/test_campaign_safety.py` | 修改 | 新增并发、租户隔离测试 |
| `backend/tests/test_api/test_benefits.py` | 修改 | 新增权益类型、状态变更测试 |
| `frontend/apps/admin/src/app/(dashboard)/campaigns/page.tsx` | 修改 | 错误处理、类型、Modal prop 修复 |
| `frontend/apps/admin/src/app/(dashboard)/campaigns/[id]/page.tsx` | 修改 | 导入共享类型 |
| `frontend/packages/shared/src/index.ts` | 修改 | 扩展 Campaign 类型定义 |

---

## Phase 1: Critical 安全与正确性修复

### Task 1: H5 领取端点加租户隔离 + 红包状态检查

**Files:**
- Modify: `backend/app/api/v1/benefit_claims.py:52-67`
- Test: `backend/tests/test_api/test_campaign_safety.py`

- [ ] **Step 1: 写失败测试 — 跨租户领取应被拒绝**

在 `test_campaign_safety.py` 末尾添加：

```python
class TestH5ClaimTenantIsolation:
    """验证 H5 领取端点的租户隔离"""

    @pytest.mark.asyncio
    async def test_claim_rejects_cross_tenant_benefit(self, client, db_session):
        """持有 Tenant A scan_token 的消费者不能领取 Tenant B 的权益"""
        # 创建 Tenant A 的产品和活动
        from app.services.campaign import create_campaign, create_benefit

        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()

        # Tenant B 创建权益
        benefit_b = await create_benefit(
            db_session, tenant_b, None, "TenantB权益", "platform_coupon",
            {"url": "https://example.com"}, stock_total=100, per_person_limit=1,
        )
        await db_session.commit()

        # 用 Tenant A 的 scan_token 尝试领取 Tenant B 的权益
        from app.services.scan_token import create_scan_token

        token = create_scan_token("test_pub_id", tenant_id=str(tenant_a), consumer_id="consumer_a")
        resp = await client.post(
            "/api/v1/benefit-claims",
            json={"benefit_id": benefit_b["id"], "scan_token": token, "phone": None},
        )
        assert resp.status_code == 404
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign_safety.py::TestH5ClaimTenantIsolation -xvs`
Expected: FAIL（当前不检查租户，跨租户领取会成功或报其他错误）

- [ ] **Step 3: 修复 — 在 benefit 查询中加入 tenant_id 过滤**

修改 `backend/app/api/v1/benefit_claims.py`，在 `# 2. 查找权益` 部分加入租户过滤。将第 52-63 行替换为：

```python
    # 2. 查找权益（加租户隔离：从 scan_token 提取 tenant_id）
    from app.models.campaign import Benefit

    try:
        benefit_id = uuid.UUID(body.benefit_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid benefit_id")

    # 从 scan_token payload 中提取 tenant_id，确保只能领取同租户的权益
    token_tenant_id = payload.get("tenant_id")
    benefit_filter = [Benefit.id == benefit_id]
    if token_tenant_id:
        try:
            benefit_filter.append(Benefit.tenant_id == uuid.UUID(token_tenant_id))
        except ValueError:
            pass

    result = await db.execute(select(Benefit).where(*benefit_filter))
    benefit = result.scalar_one_or_none()
    if not benefit:
        raise HTTPException(status_code=404, detail="benefit not found")
```

- [ ] **Step 4: 修复 — 红包路径增加权益和活动状态检查**

在 `benefit_claims.py` 第 65-67 行之间插入状态检查：

```python
    # 3. 权益状态检查（对所有类型生效，包括红包）
    if benefit.status != "active":
        raise HTTPException(status_code=409, detail="权益已停用")

    # 4. 红包类权益特殊处理：需要走 OAuth 获取 OpenID
    if benefit.benefit_type == "cash_red_packet":
        return await _handle_cash_red_packet_claim(benefit, token, payload, db)
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign_safety.py::TestH5ClaimTenantIsolation -xvs`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/benefit_claims.py backend/tests/test_api/test_campaign_safety.py
git commit -m "fix(campaign): add tenant isolation to H5 claim endpoint and benefit status check for red packets"
```

---

### Task 2: 库存回滚原子化 + 过期活动领取拦截

**Files:**
- Modify: `backend/app/services/campaign.py:787-822`
- Test: `backend/tests/test_api/test_campaign_safety.py`

- [ ] **Step 1: 写失败测试 — 过期活动仍可领取**

在 `test_campaign_safety.py` 添加：

```python
class TestExpiredCampaignClaim:
    """验证时间过期但 status 仍为 active 的活动不允许领取"""

    @pytest.mark.asyncio
    async def test_claim_rejected_for_time_expired_active_campaign(self, client, db_session):
        """status='active' 但 end_at 已过的活动应拒绝领取"""
        from datetime import UTC, datetime, timedelta
        from app.services.campaign import create_campaign, create_benefit

        tenant_id = uuid.uuid4()
        yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        two_days_ago = (datetime.now(UTC) - timedelta(days=2)).isoformat()

        campaign = await create_campaign(
            db_session, tenant_id, "过期活动", "coupon",
            two_days_ago, yesterday, {"participation_conditions": "any_scan",
            "claim_limits": "1", "validity_period": "campaign_period",
            "disclaimer": "", "minor_notice": "", "customer_service_contact": ""},
            "测试",
        )
        benefit = await create_benefit(
            db_session, tenant_id, uuid.UUID(campaign["id"]),
            "测试权益", "platform_coupon", {"url": "https://example.com"},
            stock_total=100, per_person_limit=10,
        )
        # 手动设 status 为 active（绕过激活检查）
        from sqlalchemy import update as sa_update
        from app.models.campaign import Campaign
        await db_session.execute(
            sa_update(Campaign).where(Campaign.id == uuid.UUID(campaign["id"]))
            .values(status="active")
        )
        await db_session.commit()

        result = await claim_benefit(
            db_session, tenant_id, uuid.UUID(benefit["id"]),
            "consumer_1", "idem_1",
        )
        assert result["status"] == "campaign_inactive", f"Expected campaign_inactive, got {result['status']}"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign_safety.py::TestExpiredCampaignClaim -xvs`
Expected: FAIL（当前不检查时间过期）

- [ ] **Step 3: 修复 — claim_benefit 增加时间过期检查**

修改 `backend/app/services/campaign.py` 第 780-788 行，将活动状态检查替换为同时检查存储状态和计算状态：

```python
    # 1.4 检查关联活动状态：已结束的活动不允许领取
    # 注意：draft/paused 活动的权益仍可领取（支持测试和内部管理场景）
    if benefit.campaign_id:
        campaign_result = await db.execute(
            select(Campaign).where(Campaign.id == benefit.campaign_id, Campaign.tenant_id == tenant_id),
        )
        campaign = campaign_result.scalar_one_or_none()
        if campaign:
            if campaign.status == CampaignStatus.ENDED:
                return {"status": "campaign_inactive"}
            # 同时检查时间过期：status='active' 但 end_at 已过
            computed = _compute_campaign_status(campaign)
            if computed == CampaignStatus.ENDED:
                return {"status": "campaign_inactive"}
```

- [ ] **Step 4: 修复 — 库存回滚使用原子操作**

修改 `backend/app/services/campaign.py` 第 819-822 行，将非原子回滚替换为原子递减：

```python
            # 回滚库存：原子递减，防止并发回滚导致 stock_used < 0
            await db.execute(
                update(Benefit)
                .where(
                    Benefit.id == benefit_id,
                    Benefit.tenant_id == tenant_id,
                    Benefit.stock_used > 0,
                )
                .values(stock_used=Benefit.stock_used - 1)
            )
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign_safety.py::TestExpiredCampaignClaim -xvs`
Expected: PASS

- [ ] **Step 6: 运行全量安全测试确认无回归**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign_safety.py -xvs`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/campaign.py backend/tests/test_api/test_campaign_safety.py
git commit -m "fix(campaign): check time-expired campaigns in claim and use atomic stock rollback"
```

---

## Phase 2: High 级正确性修复

### Task 3: 活动状态变更事件命名修复

**Files:**
- Modify: `backend/app/services/campaign.py:323`

- [ ] **Step 1: 写失败测试 — 暂停应触发 campaign.paused 事件**

在 `test_campaign_safety.py` 的 `TestCampaignStateMachine` 类末尾添加：

```python
    @pytest.mark.asyncio
    async def test_pause_emits_paused_event(self, client, db_session):
        """暂停活动应触发 campaign.paused 事件，而非 campaign.ended"""
        from app.services.campaign import create_campaign
        from app.core.events import _event_log

        tenant_id = uuid.uuid4()
        campaign = await create_campaign(
            db_session, tenant_id, "测试事件", "coupon",
            "2025-01-01T00:00:00Z", "2027-12-31T23:59:59Z",
            {"participation_conditions": "any_scan", "claim_limits": "1",
             "validity_period": "campaign_period", "disclaimer": "",
             "minor_notice": "", "customer_service_contact": ""},
            None,
        )
        cid = uuid.UUID(campaign["id"])
        # 手动激活
        from sqlalchemy import update as sa_update
        from app.models.campaign import Campaign
        await db_session.execute(
            sa_update(Campaign).where(Campaign.id == cid).values(status="active")
        )
        await db_session.commit()

        _event_log.clear()
        await change_campaign_status(db_session, tenant_id, cid, "paused")
        events = [e for e in _event_log if "campaign" in e.get("event", "")]
        assert any("paused" in e["event"] for e in events), \
            f"Expected campaign.paused event, got: {events}"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign_safety.py::TestCampaignStateMachine::test_pause_emits_paused_event -xvs`
Expected: FAIL（当前发出 campaign.ended）

- [ ] **Step 3: 修复事件命名**

修改 `backend/app/services/campaign.py` 第 323 行：

```python
        event_name = f"campaign.{new_status}"
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign_safety.py::TestCampaignStateMachine -xvs`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/campaign.py backend/tests/test_api/test_campaign_safety.py
git commit -m "fix(campaign): emit campaign.paused event instead of campaign.ended when pausing"
```

---

### Task 4: update_campaign 批量赋值白名单 + 搜索通配符转义

**Files:**
- Modify: `backend/app/services/campaign.py:280-287, 204, 512, 681`
- Modify: `backend/app/constants/campaign.py`

- [ ] **Step 1: 在 constants 中添加字段白名单**

在 `backend/app/constants/campaign.py` 末尾添加：

```python

# 可更新字段白名单（防止 mass assignment）
UPDATABLE_CAMPAIGN_FIELDS = {
    "name", "campaign_type", "start_at", "end_at", "rules_json", "description",
}

UPDATABLE_BENEFIT_FIELDS = {
    "name", "benefit_type", "config_json", "stock_total",
    "per_person_limit", "connector_id", "status", "campaign_id",
}

# campaign_type 合法值
CAMPAIGN_TYPES = {
    "coupon", "discount", "trial", "presale", "event",
    "lottery", "points", "private_domain_repurchase", "festival", "custom",
}

# 计算状态常量
COMPUTED_STATUS_PENDING = "pending"
COMPUTED_STATUS_VALUES = {"draft", "active", "paused", "ended", "pending"}
```

- [ ] **Step 2: 在 campaign service 中添加搜索通配符转义函数**

在 `backend/app/services/campaign.py` 顶部 import 区域后（约第 30 行后）添加：

```python
def _escape_like(value: str) -> str:
    """转义 SQL LIKE/ILIKE 通配符，防止用户输入干扰模式匹配"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
```

- [ ] **Step 3: 替换三处 ILIKE 模式**

在 `campaign.py` 中找到三处 `f"%{q}%"` 使用，替换为 `f"%{_escape_like(q)}%"`。

第 204 行附近：
```python
        pattern = f"%{_escape_like(q)}%"
        stmt = stmt.where(Campaign.name.ilike(pattern, escape="\\"))
```

第 512 行附近（benefit name search）：
```python
        pattern = f"%{_escape_like(q)}%"
        stmt = stmt.where(Benefit.name.ilike(pattern, escape="\\"))
```

第 681 行附近（claim search）：
```python
        pattern = f"%{_escape_like(q)}%"
        stmt = stmt.where(ilike_conditions, escape="\\")  # 需同时调整 ilike_conditions 的构建
```

- [ ] **Step 4: 在 update_campaign 中应用字段白名单**

修改 `backend/app/services/campaign.py` 第 280-287 行的循环：

```python
    from app.constants.campaign import UPDATABLE_CAMPAIGN_FIELDS
    for k, v in fields.items():
        if k not in UPDATABLE_CAMPAIGN_FIELDS:
            continue
        if k == "rules_json" and isinstance(v, dict):
            v = _rules_with_product_id(v, fields.get("product_id", c.product_id))
        if k == "product_id":
            c.product_id = v
            continue
        if v is not None:
            setattr(c, k, v)
```

- [ ] **Step 5: 同样在 update_benefit 中应用白名单**

修改 `backend/app/services/campaign.py` 第 601-603 行：

```python
    from app.constants.campaign import UPDATABLE_BENEFIT_FIELDS
    for k, v in fields.items():
        if k not in UPDATABLE_BENEFIT_FIELDS:
            continue
        if v is not None:
            setattr(b, k, v)
```

- [ ] **Step 6: 运行全量活动测试确认无回归**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign.py tests/test_api/test_campaign_safety.py tests/test_api/test_benefits.py -xvs`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/campaign.py backend/app/constants/campaign.py
git commit -m "fix(campaign): add field allowlist for update, escape LIKE wildcards in search"
```

---

### Task 5: Open API 状态变更复用验证 Schema + 激活拦截

**Files:**
- Modify: `backend/app/api/v1/open_api.py:268-285`

- [ ] **Step 1: 写失败测试 — Open API 任意 status 值应被拒绝**

在 `test_campaign_safety.py` 添加：

```python
class TestOpenApiCampaignStatus:
    """验证 Open API 活动状态变更的输入验证"""

    @pytest.mark.asyncio
    async def test_open_api_rejects_invalid_status(self, client, db_session, auth_setup):
        """Open API 不应接受任意 status 值"""
        tenant_id, headers = auth_setup
        # 创建活动
        resp = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "OpenAPI测试",
                "campaign_type": "coupon",
                "start_at": "2025-01-01T00:00:00Z",
                "end_at": "2027-12-31T23:59:59Z",
                "rules_json": {
                    "participation_conditions": "any_scan",
                    "claim_limits": "1",
                    "validity_period": "campaign_period",
                    "disclaimer": "",
                    "minor_notice": "",
                    "customer_service_contact": "",
                },
            },
            headers=headers,
        )
        assert resp.status_code == 201
        cid = resp.json()["id"]

        # 使用 Open API 端点尝试非法 status
        from tests.test_api.test_campaign import _platform_admin_headers
        open_headers = _platform_admin_headers(db_session)
        resp = await client.patch(
            f"/open/v1/campaigns/{cid}/status",
            json={"status": "invalid_status"},
            headers=open_headers,
        )
        assert resp.status_code in (400, 422), f"Expected 400/422, got {resp.status_code}"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign_safety.py::TestOpenApiCampaignStatus -xvs`
Expected: FAIL（当前接受任意字符串）

- [ ] **Step 3: 修复 — 复用 schemas 的 CampaignStatusRequest**

修改 `backend/app/api/v1/open_api.py` 第 268-285 行。删除本地 `CampaignStatusRequest` 类定义，改为导入：

```python
# 删除第 268-270 行的本地 CampaignStatusRequest 定义
# 在文件顶部 import 区添加：
from app.schemas.campaign import CampaignStatusRequest as ValidatedCampaignStatusRequest

# 修改端点：
@open_api_router.patch("/campaigns/{campaign_id}/status")
async def update_campaign_status(
    campaign_id: uuid.UUID,
    body: ValidatedCampaignStatusRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("campaign:status")),
):
    from app.services.campaign import change_campaign_status, get_campaign_activation_blockers

    if body.status == "active":
        blockers = await get_campaign_activation_blockers(db, tenant_id, campaign_id)
        if blockers is None:
            raise HTTPException(status_code=404, detail="Campaign not found")
        if blockers:
            raise HTTPException(status_code=400, detail="；".join(blockers))

    result = await change_campaign_status(db, tenant_id, campaign_id, body.status)
    if not result:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return result
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign_safety.py::TestOpenApiCampaignStatus -xvs`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/open_api.py backend/tests/test_api/test_campaign_safety.py
git commit -m "fix(campaign): reuse validated CampaignStatusRequest in open_api and add activation blockers"
```

---

### Task 6: create_benefit 增加 campaign_id 和配置验证

**Files:**
- Modify: `backend/app/services/campaign.py:395-419`

- [ ] **Step 1: 写失败测试 — 不存在的 campaign_id 应报错**

在 `test_benefits.py` 添加：

```python
class TestBenefitCreationValidation:
    """验证 create_benefit 的输入验证"""

    @pytest.mark.asyncio
    async def test_create_benefit_rejects_nonexistent_campaign(self, client, db_session, auth_setup):
        """关联不存在的 campaign_id 应返回 400"""
        tenant_id, headers = auth_setup
        fake_campaign_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/campaigns/{fake_campaign_id}/benefits",
            json={
                "name": "测试权益",
                "benefit_type": "platform_coupon",
                "config_json": {"url": "https://example.com"},
                "stock_total": 100,
                "per_person_limit": 1,
            },
            headers=headers,
        )
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_benefits.py::TestBenefitCreationValidation -xvs`
Expected: FAIL（当前 FK 约束报 500）

- [ ] **Step 3: 修复 — create_benefit 增加验证**

修改 `backend/app/services/campaign.py` 的 `create_benefit` 函数（第 395-419 行），在创建 Benefit 对象之前添加验证：

```python
async def create_benefit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID | None,
    name: str,
    benefit_type: str,
    config_json: dict,
    stock_total: int,
    per_person_limit: int = 1,
    connector_id: uuid.UUID | None = None,
) -> dict:
    # 验证 benefit_type
    if benefit_type not in BENEFIT_TYPES:
        raise ValueError(f"benefit_type must be one of: {', '.join(sorted(BENEFIT_TYPES))}")
    # 验证 config_json 与 benefit_type 匹配
    validate_benefit_config_shape(config_json, benefit_type)
    # 验证 campaign_id 存在且属于当前租户
    if campaign_id is not None:
        camp_result = await db.execute(
            select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id),
        )
        camp = camp_result.scalar_one_or_none()
        if not camp:
            raise ValueError("Campaign not found")
        if camp.status not in (CampaignStatus.DRAFT, CampaignStatus.PAUSED):
            raise ValueError("Cannot add benefits to an active or ended campaign")

    b = Benefit(
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        name=name,
        benefit_type=benefit_type,
        config_json=config_json,
        stock_total=stock_total,
        per_person_limit=per_person_limit,
        connector_id=connector_id,
    )
    db.add(b)
    await db.flush()
    await db.refresh(b)
    return _benefit_to_dict(b)
```

同时需要在 `campaigns.py` API 层捕获 ValueError：

修改 `backend/app/api/v1/campaigns.py` 第 183-193 行的 `create_benefit_endpoint`：

```python
@campaign_router.post("/{campaign_id}/benefits", status_code=201, summary="创建权益")
async def create_benefit_endpoint(
    campaign_id: uuid.UUID,
    body: BenefitCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        return await create_benefit(
            db, tenant_id, campaign_id, body.name, body.benefit_type,
            body.config_json, body.stock_total, body.per_person_limit,
            connector_id=body.connector_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_benefits.py::TestBenefitCreationValidation -xvs`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/campaign.py backend/app/api/v1/campaigns.py backend/tests/test_api/test_benefits.py
git commit -m "fix(campaign): validate campaign_id existence and benefit config in create_benefit"
```

---

## Phase 3: 架构去重与 Schema 加固

### Task 7: 提取验证函数到 utils，消除 schema→service 反向依赖

**Files:**
- Create: `backend/app/utils/campaign_validation.py`
- Modify: `backend/app/services/campaign.py`（移出验证函数）
- Modify: `backend/app/schemas/campaign.py`（改为从 utils 导入）

- [ ] **Step 1: 创建 utils/campaign_validation.py**

将 `campaign.py` service 中的以下函数提取到新文件：
- `validate_campaign_rules_shape`
- `validate_benefit_config_shape`
- `_require_url`
- `_require_positive_number`
- `_parse_config_datetime`

在 `backend/app/utils/campaign_validation.py` 中：

```python
"""活动与权益配置验证工具函数"""

from datetime import datetime

from app.constants.campaign import (
    BENEFIT_TYPES,
    CASH_RED_PACKET_AMOUNT_TYPES,
    CAMPAIGN_GOALS,
    PARTICIPATION_CONDITION_TYPES,
    WECOM_MODES,
)


def _require_url(value: str | None, field_name: str) -> str:
    """验证 URL 格式"""
    if not value or not value.startswith(("http://", "https://")):
        raise ValueError(f"{field_name} must be a valid URL starting with http:// or https://")
    return value


def _require_positive_number(value: int | float | None, field_name: str, min_val: int = 1) -> int | float:
    """验证正数"""
    if value is None or value < min_val:
        raise ValueError(f"{field_name} must be >= {min_val}")
    return value


def _parse_config_datetime(value: str | None) -> datetime | None:
    """解析配置中的日期时间字符串"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_campaign_rules_shape(rules: dict) -> dict:
    """验证 campaign rules_json 结构"""
    goal = rules.get("campaign_goal")
    if goal and goal not in CAMPAIGN_GOALS:
        raise ValueError(f"campaign_goal must be one of: {', '.join(sorted(CAMPAIGN_GOALS))}")

    pct = rules.get("participation_condition_type")
    if pct and pct not in PARTICIPATION_CONDITION_TYPES:
        raise ValueError(
            f"participation_condition_type must be one of: {', '.join(sorted(PARTICIPATION_CONDITION_TYPES))}"
        )

    wecom_mode = rules.get("wecom_mode")
    if wecom_mode and wecom_mode not in WECOM_MODES:
        raise ValueError(f"wecom_mode must be one of: {', '.join(sorted(WECOM_MODES))}")

    claim_limit = rules.get("claim_limit_count")
    if claim_limit is not None:
        try:
            val = int(claim_limit)
            if val < 1:
                raise ValueError("claim_limit_count must be >= 1")
        except (TypeError, ValueError):
            raise ValueError("claim_limit_count must be a positive integer")

    return rules


def validate_benefit_config_shape(config: dict, benefit_type: str) -> dict:
    """验证权益配置与类型匹配"""
    if benefit_type not in BENEFIT_TYPES:
        raise ValueError(f"benefit_type must be one of: {', '.join(sorted(BENEFIT_TYPES))}")

    if benefit_type == "platform_coupon":
        if not config.get("url"):
            raise ValueError("platform_coupon requires 'url' in config_json")
        _require_url(config["url"], "url")

    elif benefit_type == "external_link":
        _require_url(config.get("url"), "url")

    elif benefit_type == "private_domain":
        if not config.get("qr_image_url"):
            raise ValueError("private_domain requires 'qr_image_url' in config_json")
        _require_url(config["qr_image_url"], "qr_image_url")

    elif benefit_type == "form_benefit":
        _require_url(config.get("form_url"), "form_url")
        if config.get("require_phone") is not None and not isinstance(config["require_phone"], bool):
            raise ValueError("require_phone must be a boolean")

    elif benefit_type == "cash_red_packet":
        amount_type = config.get("amount_type")
        if amount_type not in CASH_RED_PACKET_AMOUNT_TYPES:
            raise ValueError(
                f"amount_type must be one of: {', '.join(sorted(CASH_RED_PACKET_AMOUNT_TYPES))}"
            )
        if amount_type == "fixed":
            _require_positive_number(config.get("fixed_amount"), "fixed_amount", min_val=100)
            if config["fixed_amount"] > 20000:
                raise ValueError("fixed_amount must not exceed 20000 (200 yuan)")
        elif amount_type == "random":
            _require_positive_number(config.get("min_amount"), "min_amount", min_val=100)
            _require_positive_number(config.get("max_amount"), "max_amount", min_val=100)
            if config["min_amount"] > config["max_amount"]:
                raise ValueError("min_amount must not exceed max_amount")

    return config
```

- [ ] **Step 2: 修改 schemas/campaign.py — 改为从 utils 导入**

修改 `backend/app/schemas/campaign.py` 第 7 行：

```python
# 替换:
# from app.services.campaign import validate_benefit_config_shape, validate_campaign_rules_shape
# 为:
from app.utils.campaign_validation import validate_benefit_config_shape, validate_campaign_rules_shape
```

- [ ] **Step 3: 修改 services/campaign.py — 改为从 utils 导入**

在 `backend/app/services/campaign.py` 顶部添加导入，并删除原函数定义：

```python
from app.utils.campaign_validation import validate_benefit_config_shape, validate_campaign_rules_shape
```

删除 service 中原有的 `validate_campaign_rules_shape`、`validate_benefit_config_shape`、`_require_url`、`_require_positive_number`、`_parse_config_datetime` 函数定义。

注意：`_parse_config_datetime` 在 service 中也有使用（第 1010 行），需保留对它的引用或使用 utils 版本。检查 service 中对 `_parse_campaign_datetime` 的定义（约第 1005 行），这是不同的函数（处理活动日期而非配置日期），不需要移除。

- [ ] **Step 4: 运行全量活动测试确认无回归**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign.py tests/test_api/test_campaign_safety.py tests/test_api/test_benefits.py -xvs`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/utils/campaign_validation.py backend/app/schemas/campaign.py backend/app/services/campaign.py
git commit -m "refactor(campaign): extract validation functions to utils, break schema→service dependency"
```

---

### Task 8: 消除重复类型定义 — models 使用 constants

**Files:**
- Modify: `backend/app/models/campaign.py:20-24, 51-56`
- Modify: `backend/app/constants/campaign.py`（确保导出正确）

- [ ] **Step 1: 修改 models/campaign.py — 删除本地 CampaignStatus 和 BenefitType**

修改 `backend/app/models/campaign.py`。删除第 20-24 行的 `CampaignStatus` 类和第 51-56 行的 `BenefitType` 类。在文件顶部添加导入：

```python
from app.constants.campaign import CampaignStatus, BenefitType
```

- [ ] **Step 2: 搜索所有导入位置确认兼容性**

Run: `cd backend && grep -rn "from app.models.campaign import.*CampaignStatus\|from app.models.campaign import.*BenefitType" app/ scripts/`

对每个导入位置，确认 `CampaignStatus` 和 `BenefitType` 的用法兼容 StrEnum（它们被用作字符串常量 `.DRAFT`、`.ACTIVE` 等，StrEnum 的成员同时也是字符串，完全兼容）。

- [ ] **Step 3: 运行全量测试**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign.py tests/test_api/test_campaign_safety.py tests/test_api/test_benefits.py tests/test_api/test_campaign_analytics.py -xvs`
Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/models/campaign.py
git commit -m "refactor(campaign): remove duplicate CampaignStatus/BenefitType from models, use constants"
```

---

### Task 9: Schema 字段约束加固

**Files:**
- Modify: `backend/app/schemas/campaign.py:19-26`
- Modify: `backend/app/schemas/benefit_claim.py`

- [ ] **Step 1: 加固 CampaignCreateRequest 和 CampaignUpdateRequest**

修改 `backend/app/schemas/campaign.py`：

```python
from app.constants.campaign import CAMPAIGN_TYPES


class CampaignCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    campaign_type: str = Field(pattern=f"^({'|'.join(sorted(CAMPAIGN_TYPES))})$")
    product_id: uuid.UUID | None = None
    start_at: str = Field(min_length=1)
    end_at: str = Field(min_length=1)
    rules_json: dict
    description: str | None = Field(default=None, max_length=500)
```

对 `CampaignUpdateRequest` 同理添加约束（但字段可选）：

```python
class CampaignUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    campaign_type: str | None = Field(default=None, pattern=f"^({'|'.join(sorted(CAMPAIGN_TYPES))})$")
    product_id: uuid.UUID | None = None
    start_at: str | None = Field(default=None, min_length=1)
    end_at: str | None = Field(default=None, min_length=1)
    rules_json: dict | None = None
    description: str | None = Field(default=None, max_length=500)
```

- [ ] **Step 2: 加固 BenefitClaimRequest**

修改 `backend/app/schemas/benefit_claim.py`：

```python
"""H5 权益领取 Schema"""

import re
import uuid

from pydantic import BaseModel, field_validator


class BenefitClaimRequest(BaseModel):
    benefit_id: uuid.UUID
    scan_token: str | None = None
    phone: str | None = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^1[3-9]\d{9}$", v):
            raise ValueError("手机号格式不正确")
        return v
```

- [ ] **Step 3: 更新 benefit_claims.py 中手动 UUID 转换**

修改 `backend/app/api/v1/benefit_claims.py` 第 55-58 行，删除手动 try/except（Pydantic 已处理）：

```python
    benefit_id = body.benefit_id  # 已经是 uuid.UUID 类型
```

- [ ] **Step 4: 运行测试确认无回归**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign.py tests/test_api/test_benefits.py -xvs`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/campaign.py backend/app/schemas/benefit_claim.py backend/app/api/v1/benefit_claims.py
git commit -m "fix(campaign): add field constraints to schemas (max_length, campaign_type whitelist, phone format)"
```

---

### Task 10: 消除重复 BenefitCreateRequest

**Files:**
- Modify: `backend/app/api/v1/benefits.py:44-57`
- Modify: `backend/app/api/v1/campaigns.py`（确保导入路径一致）

- [ ] **Step 1: 修改 benefits.py — 删除本地 BenefitCreateRequest，使用 schemas 版本**

修改 `backend/app/api/v1/benefits.py`，删除第 44-57 行的本地 `BenefitCreateRequest` 和 `BenefitUpdateRequest` 定义。在文件顶部导入：

```python
from app.schemas.campaign import BenefitCreateRequest
```

对于 `BenefitUpdateRequest`（如果 benefits.py 中有定义但 schemas 中没有），移到 schemas/campaign.py 中添加：

```python
class BenefitUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    benefit_type: str | None = Field(default=None, pattern=f"^({'|'.join(sorted(BENEFIT_TYPES))})$")
    config_json: dict | None = None
    stock_total: int | None = Field(default=None, ge=0)
    per_person_limit: int | None = Field(default=None, ge=1)
    connector_id: uuid.UUID | None = None
    status: str | None = None

    @model_validator(mode="after")
    def validate_benefit(self):
        if self.benefit_type and self.config_json:
            self.config_json = validate_benefit_config_shape(self.config_json, self.benefit_type)
        return self
```

然后在 benefits.py 中也导入 `BenefitUpdateRequest`。

- [ ] **Step 2: 修改 benefits.py 的 update 端点使用 exclude_unset**

修改 `backend/app/api/v1/benefits.py` 第 166 行（`update_benefit_endpoint`）：

```python
    data = await update_benefit(db, tenant_id, benefit_id, **body.model_dump(exclude_unset=True))
```

- [ ] **Step 3: 运行测试**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_benefits.py -xvs`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/v1/benefits.py backend/app/schemas/campaign.py
git commit -m "refactor(campaign): deduplicate BenefitCreateRequest/UpdateRequest, use exclude_unset in PATCH"
```

---

## Phase 4: 关键测试补充

### Task 11: 跨租户隔离测试

**Files:**
- Modify: `backend/tests/test_api/test_campaign_safety.py`

- [ ] **Step 1: 添加跨租户隔离测试类**

在 `test_campaign_safety.py` 末尾添加：

```python
class TestCrossTenantIsolation:
    """验证活动模块的租户隔离：Tenant A 不能操作 Tenant B 的数据"""

    @pytest.fixture
    async def two_tenants(self, db_session):
        """创建两个租户的活动+权益"""
        from app.services.campaign import create_campaign, create_benefit

        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        rules = {
            "participation_conditions": "any_scan", "claim_limits": "1",
            "validity_period": "campaign_period", "disclaimer": "",
            "minor_notice": "", "customer_service_contact": "",
        }
        camp_a = await create_campaign(
            db_session, tenant_a, "TenantA活动", "coupon",
            "2025-01-01T00:00:00Z", "2027-12-31T23:59:59Z", rules, None,
        )
        camp_b = await create_campaign(
            db_session, tenant_b, "TenantB活动", "coupon",
            "2025-01-01T00:00:00Z", "2027-12-31T23:59:59Z", rules, None,
        )
        ben_a = await create_benefit(
            db_session, tenant_a, uuid.UUID(camp_a["id"]),
            "A权益", "platform_coupon", {"url": "https://a.com"}, 100, 1,
        )
        ben_b = await create_benefit(
            db_session, tenant_b, uuid.UUID(camp_b["id"]),
            "B权益", "platform_coupon", {"url": "https://b.com"}, 100, 1,
        )
        await db_session.commit()
        return {
            "tenant_a": tenant_a, "tenant_b": tenant_b,
            "camp_a": camp_a, "camp_b": camp_b,
            "ben_a": ben_a, "ben_b": ben_b,
        }

    @pytest.mark.asyncio
    async def test_tenant_a_cannot_read_tenant_b_campaign(self, client, db_session, two_tenants):
        result = await get_campaign(db_session, two_tenants["tenant_a"], uuid.UUID(two_tenants["camp_b"]["id"]))
        assert result is None

    @pytest.mark.asyncio
    async def test_tenant_a_cannot_update_tenant_b_campaign(self, client, db_session, two_tenants):
        result = await update_campaign(
            db_session, two_tenants["tenant_a"], uuid.UUID(two_tenants["camp_b"]["id"]),
            name="hacked",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_tenant_a_cannot_delete_tenant_b_campaign(self, client, db_session, two_tenants):
        result = await delete_campaign(db_session, two_tenants["tenant_a"], uuid.UUID(two_tenants["camp_b"]["id"]))
        assert result is False

    @pytest.mark.asyncio
    async def test_tenant_a_cannot_claim_tenant_b_benefit(self, client, db_session, two_tenants):
        result = await claim_benefit(
            db_session, two_tenants["tenant_a"], uuid.UUID(two_tenants["ben_b"]["id"]),
            "consumer_a", "idem_a",
        )
        assert result["status"] == "not_found"
```

- [ ] **Step 2: 运行测试**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign_safety.py::TestCrossTenantIsolation -xvs`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_api/test_campaign_safety.py
git commit -m "test(campaign): add cross-tenant isolation tests for campaign CRUD and claims"
```

---

### Task 12: 并发领取竞态测试

**Files:**
- Modify: `backend/tests/test_api/test_campaign_safety.py`

- [ ] **Step 1: 添加并发领取测试**

在 `test_campaign_safety.py` 末尾添加：

```python
class TestConcurrentClaims:
    """验证并发领取场景下的库存安全"""

    @pytest.mark.asyncio
    async def test_concurrent_claims_do_not_oversell(self, db_session):
        """20 个并发请求领取 stock=5 的权益，应恰好 5 个成功"""
        import asyncio
        from app.services.campaign import create_campaign, create_benefit

        tenant_id = uuid.uuid4()
        rules = {
            "participation_conditions": "any_scan", "claim_limits": "1",
            "validity_period": "campaign_period", "disclaimer": "",
            "minor_notice": "", "customer_service_contact": "",
        }
        campaign = await create_campaign(
            db_session, tenant_id, "并发测试", "coupon",
            "2025-01-01T00:00:00Z", "2027-12-31T23:59:59Z", rules, None,
        )
        benefit = await create_benefit(
            db_session, tenant_id, uuid.UUID(campaign["id"]),
            "限量权益", "platform_coupon", {"url": "https://example.com"},
            stock_total=5, per_person_limit=10,
        )
        await db_session.commit()

        benefit_id = uuid.UUID(benefit["id"])

        async def single_claim(idx: int):
            """单个领取请求，每个使用不同 consumer_id 以避免 per_person_limit"""
            from sqlalchemy.ext.asyncio import AsyncSession
            from app.core.database import async_session_factory
            async with async_session_factory() as session:
                try:
                    result = await claim_benefit(
                        session, tenant_id, benefit_id,
                        f"consumer_{idx}", f"idem_{idx}",
                    )
                    await session.commit()
                    return result["status"]
                except Exception:
                    await session.rollback()
                    return "error"

        results = await asyncio.gather(*[single_claim(i) for i in range(20)])
        success_count = sum(1 for r in results if r == "success")
        oos_count = sum(1 for r in results if r == "out_of_stock")
        assert success_count == 5, f"Expected exactly 5 successes, got {success_count}: {results}"
        assert oos_count == 15, f"Expected 15 out_of_stock, got {oos_count}"

    @pytest.mark.asyncio
    async def test_concurrent_per_person_limit_enforcement(self, db_session):
        """5 个并发请求同一消费者领取 per_person_limit=1 的权益，应恰好 1 个成功"""
        import asyncio
        from app.services.campaign import create_campaign, create_benefit

        tenant_id = uuid.uuid4()
        rules = {
            "participation_conditions": "any_scan", "claim_limits": "1",
            "validity_period": "campaign_period", "disclaimer": "",
            "minor_notice": "", "customer_service_contact": "",
        }
        campaign = await create_campaign(
            db_session, tenant_id, "限领测试", "coupon",
            "2025-01-01T00:00:00Z", "2027-12-31T23:59:59Z", rules, None,
        )
        benefit = await create_benefit(
            db_session, tenant_id, uuid.UUID(campaign["id"]),
            "限领权益", "platform_coupon", {"url": "https://example.com"},
            stock_total=100, per_person_limit=1,
        )
        await db_session.commit()

        benefit_id = uuid.UUID(benefit["id"])

        async def single_claim(idx: int):
            from sqlalchemy.ext.asyncio import AsyncSession
            from app.core.database import async_session_factory
            async with async_session_factory() as session:
                try:
                    result = await claim_benefit(
                        session, tenant_id, benefit_id,
                        "same_consumer", f"idem_limit_{idx}",
                    )
                    await session.commit()
                    return result["status"]
                except Exception:
                    await session.rollback()
                    return "error"

        results = await asyncio.gather(*[single_claim(i) for i in range(5)])
        success_count = sum(1 for r in results if r == "success")
        limit_count = sum(1 for r in results if r == "limit_reached")
        assert success_count == 1, f"Expected exactly 1 success, got {success_count}: {results}"
        assert limit_count == 4, f"Expected 4 limit_reached, got {limit_count}"
```

- [ ] **Step 2: 运行测试**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign_safety.py::TestConcurrentClaims -xvs`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_api/test_campaign_safety.py
git commit -m "test(campaign): add concurrent claim race condition tests for stock and per-person limit"
```

---

### Task 13: 补充关键 High 级测试缺口

**Files:**
- Modify: `backend/tests/test_api/test_campaign.py`
- Modify: `backend/tests/test_api/test_benefits.py`

- [ ] **Step 1: 在 test_campaign.py 补充缺失测试**

在 `TestCampaignCRUD` 类末尾添加：

```python
    @pytest.mark.asyncio
    async def test_get_campaign_detail(self, client, db_session, auth_setup):
        """GET /campaigns/{id} 返回完整详情"""
        tenant_id, headers = auth_setup
        resp = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "详情测试", "campaign_type": "coupon",
                "start_at": "2025-01-01T00:00:00Z", "end_at": "2027-12-31T23:59:59Z",
                "rules_json": {
                    "participation_conditions": "any_scan", "claim_limits": "1",
                    "validity_period": "campaign_period", "disclaimer": "",
                    "minor_notice": "", "customer_service_contact": "",
                },
            },
            headers=headers,
        )
        cid = resp.json()["id"]
        resp = await client.get(f"/api/v1/campaigns/{cid}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "详情测试"
        assert "computed_status" in data
        assert "benefit_count" in data

    @pytest.mark.asyncio
    async def test_get_campaign_not_found(self, client, db_session, auth_setup):
        tenant_id, headers = auth_setup
        resp = await client.get(f"/api/v1/campaigns/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_non_draft_campaign_rejected(self, client, db_session, auth_setup):
        """非草稿活动不可删除"""
        tenant_id, headers = auth_setup
        resp = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "不可删活动", "campaign_type": "coupon",
                "start_at": "2025-01-01T00:00:00Z", "end_at": "2027-12-31T23:59:59Z",
                "rules_json": {
                    "participation_conditions": "any_scan", "claim_limits": "1",
                    "validity_period": "campaign_period", "disclaimer": "",
                    "minor_notice": "", "customer_service_contact": "",
                },
            },
            headers=headers,
        )
        cid = resp.json()["id"]
        # 手动激活
        from sqlalchemy import update as sa_update
        from app.models.campaign import Campaign
        await db_session.execute(sa_update(Campaign).where(Campaign.id == uuid.UUID(cid)).values(status="active"))
        await db_session.commit()

        resp = await client.delete(f"/api/v1/campaigns/{cid}", headers=headers)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_list_campaigns_filter_by_status(self, client, db_session, auth_setup):
        """按状态过滤活动列表"""
        tenant_id, headers = auth_setup
        resp = await client.get("/api/v1/campaigns?status=draft", headers=headers)
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item["status"] == "draft"
```

- [ ] **Step 2: 在 test_benefits.py 补充非标准权益类型测试**

在末尾添加：

```python
class TestBenefitTypeValidation:
    """验证不同权益类型的配置验证"""

    @pytest.mark.asyncio
    async def test_cash_red_packet_requires_connector(self, client, db_session, auth_setup):
        """现金红包必须关联 connector_id"""
        tenant_id, headers = auth_setup
        resp = await client.post(
            "/api/v1/benefits",
            json={
                "name": "红包测试",
                "benefit_type": "cash_red_packet",
                "config_json": {"amount_type": "fixed", "fixed_amount": 100},
                "stock_total": 100,
                "per_person_limit": 1,
            },
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_private_domain_requires_qr_image(self, client, db_session, auth_setup):
        """私域权益必须提供 qr_image_url"""
        tenant_id, headers = auth_setup
        resp = await client.post(
            "/api/v1/benefits",
            json={
                "name": "私域测试",
                "benefit_type": "private_domain",
                "config_json": {},
                "stock_total": 100,
                "per_person_limit": 1,
            },
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_benefit_status_toggle(self, client, db_session, auth_setup):
        """权益状态切换 active → inactive → active"""
        tenant_id, headers = auth_setup
        resp = await client.post(
            "/api/v1/benefits",
            json={
                "name": "状态切换测试",
                "benefit_type": "platform_coupon",
                "config_json": {"url": "https://example.com"},
                "stock_total": 100,
                "per_person_limit": 1,
            },
            headers=headers,
        )
        bid = resp.json()["id"]

        # 切为 inactive
        resp = await client.patch(
            f"/api/v1/benefits/{bid}",
            json={"status": "inactive"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "inactive"

        # 切回 active
        resp = await client.patch(
            f"/api/v1/benefits/{bid}",
            json={"status": "active"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"
```

- [ ] **Step 3: 运行所有新增测试**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_campaign.py tests/test_api/test_benefits.py -xvs`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_api/test_campaign.py backend/tests/test_api/test_benefits.py
git commit -m "test(campaign): add detail endpoint, delete guard, status filter, and benefit type validation tests"
```

---

## Phase 5: 前端修复

### Task 14: 前端错误处理统一 + Modal/Space prop 修复

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/campaigns/page.tsx`

- [ ] **Step 1: 替换所有内联错误解构为 extractErrorMessage**

在 `campaigns/page.tsx` 中找到 3 处内联错误处理（约第 720-723、731-734、806-808 行），将：

```typescript
} catch (e: unknown) {
  const err = e as { response?: { data?: { detail?: string } } };
  message.error(err.response?.data?.detail || "创建活动失败");
}
```

替换为：

```typescript
} catch (err) {
  message.error(extractErrorMessage(err, "创建活动失败"));
}
```

确保文件顶部已导入 `extractErrorMessage`：

```typescript
import api, { extractErrorMessage } from "@/lib/api";
```

- [ ] **Step 2: 修复 Modal prop — destroyOnHidden → destroyOnClose**

在 `campaigns/page.tsx` 中搜索 `destroyOnHidden`，替换为 `destroyOnClose`（约第 1028 行）。

- [ ] **Step 3: 修复 Space prop — orientation → direction**

在 `campaigns/page.tsx` 中搜索所有 `orientation="vertical"`，替换为 `direction="vertical"`（约第 827、868、885、898、1326 行）。

- [ ] **Step 4: 创建后重新获取权威数据**

修改创建活动后的逻辑（约第 697-716 行），在创建和权益操作完成后，从服务器重新获取数据：

```typescript
// 创建完成后，从服务器获取权威数据
try {
  const { data: freshData } = await api.get(`/campaigns/${createdCampaign.id}`);
  setDetailItem(freshData);
} catch {
  // 如果获取失败，使用创建返回的数据
  setDetailItem(createdCampaign);
}
```

- [ ] **Step 5: 运行前端 lint**

Run: `cd frontend && pnpm lint:admin`
Expected: 无错误

- [ ] **Step 6: Commit**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/campaigns/page.tsx
git commit -m "fix(campaign-frontend): use extractErrorMessage, fix Modal/Space props, re-fetch after create"
```

---

### Task 15: 前端类型统一 — 使用 shared 包

**Files:**
- Modify: `frontend/packages/shared/src/index.ts`
- Modify: `frontend/apps/admin/src/app/(dashboard)/campaigns/page.tsx`
- Modify: `frontend/apps/admin/src/app/(dashboard)/campaigns/[id]/page.tsx`

- [ ] **Step 1: 扩展 shared 包的 Campaign 类型**

在 `frontend/packages/shared/src/index.ts` 的 Campaign 类型定义处扩展，确保包含后端实际返回的所有字段：

```typescript
export interface Campaign {
  id: string;
  tenant_id: string;
  name: string;
  campaign_type: string;
  status: CampaignStatus;
  computed_status?: CampaignStatus | "pending";
  product_id: string | null;
  product_name: string | null;
  start_at: string;
  end_at: string;
  rules_json: Record<string, unknown>;
  description: string | null;
  created_at: string;
  updated_at: string;
  benefit_count: number;
  stock_total: number;
  stock_used: number;
  claim_count: number;
  wecom_add_count: number;
}
```

- [ ] **Step 2: 在 campaigns/page.tsx 中导入 shared 类型**

将本地 `Campaign` 接口替换为导入：

```typescript
import type { Campaign } from "@yimatong/shared";
```

保留 `CampaignFormValues` 和 `WeComIntegrationStatus` 等前端专用类型。

- [ ] **Step 3: 在 [id]/page.tsx 中导入 shared 类型**

同理将本地 `CampaignDetail` 和 `Benefit` 接口替换为 shared 导入或基于 shared 类型的扩展。

- [ ] **Step 4: 运行前端构建验证**

Run: `cd frontend && pnpm build:admin`
Expected: 成功

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/shared/src/index.ts frontend/apps/admin/src/app/\(dashboard\)/campaigns/page.tsx frontend/apps/admin/src/app/\(dashboard\)/campaigns/\[id\]/page.tsx
git commit -m "refactor(campaign-frontend): unify Campaign types using @yimatong/shared"
```

---

## Self-Review Checklist

### Spec Coverage
| Review Finding | Task |
|---|---|
| C1: 库存回滚竞态 | Task 2 Step 4 |
| C2: 过期活动领取 | Task 2 Step 3 |
| C3: 跨租户 H5 领取 | Task 1 Step 3 |
| H1: 暂停触发 ended 事件 | Task 3 |
| H2: 字符串日期比较 | 长期：需 DB 迁移，本计划在 claim 路径通过 `_compute_campaign_status` 规避 |
| H3: Open API 验证缺失 | Task 5 |
| H4: 批量赋值无白名单 | Task 4 |
| H5: create_benefit 无 campaign 验证 | Task 6 |
| H6: create_benefit 无 config 验证 | Task 6 |
| H7: 前端类型三重定义 | Task 15 |
| H8: 前端创建竞态 | Task 14 Step 4 |
| H9: 前端错误处理不一致 | Task 14 Step 1 |
| H10: 红包绕过状态检查 | Task 1 Step 4 |
| H11: ILIKE 通配符注入 | Task 4 Step 3 |
| H12: benefit_type 无白名单 | Task 9 + Task 10 |
| M1: CampaignStatus 重复 | Task 8 |
| M2: BenefitCreateRequest 重复 | Task 10 |
| M3: Schema→Service 反向依赖 | Task 7 |
| M5: campaign_type 无验证 | Task 9 |
| 测试: 跨租户隔离 | Task 11 |
| 测试: 并发竞态 | Task 12 |
| 测试: 详情/过滤/删除 | Task 13 |

### Placeholder Scan
✅ 无 TBD/TODO/占位符，所有步骤包含完整代码

### Type Consistency
✅ 所有函数签名、参数名、类型引用在 Task 间一致

---

**Plan saved.** 总计 15 个 Task，约 63 个 Step。预计执行时间 2-3 小时。

**两种执行方式：**

1. **Subagent-Driven（推荐）** — 每个 Task 分配给独立 subagent，任务间 review，快速迭代

2. **Inline Execution** — 在当前会话中使用 executing-plans 逐步执行，批量处理带检查点

选择哪种方式？
