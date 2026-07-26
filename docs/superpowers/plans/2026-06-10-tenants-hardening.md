# Tenants (租户管理) 改进计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复 tenants 模块的 9 个 High 级别问题，重点解决安全权限（/me 敏感字段越权）、性能（缺失索引、多次 flush）、功能缺失（多维过滤、暂停/恢复）和测试覆盖不足。

**Architecture:** 创建 TenantUpdateSelf schema 限制 /me 端点字段；添加数据库索引；增强列表过滤；添加审计日志；补充安全边界测试。

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, PostgreSQL, pytest, Next.js 16, Ant Design 6

---

## 文件变更映射

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `backend/app/schemas/tenant.py` | 修改 | 添加 TenantUpdateSelf schema，增强密码验证 |
| `backend/app/api/v1/tenants.py` | 修改 | /me 端点使用 TenantUpdateSelf，添加过滤参数，添加审计日志 |
| `backend/app/services/tenant.py` | 修改 | 优化 flush，添加 slug 唯一性校验，添加审计日志 |
| `backend/app/models/tenant.py` | 修改 | 添加索引定义 |
| `backend/alembic/versions/xxxx_add_tenant_indexes.py` | 创建 | 新增索引迁移（延期：需人工确认） |
| `backend/tests/test_api/test_tenant_security.py` | 创建 | 安全边界测试 |

---

## Task 1: 创建 TenantUpdateSelf schema 限制 /me 端点 (H-1)

**Files:**
- Modify: `backend/app/schemas/tenant.py`
- Modify: `backend/app/api/v1/tenants.py`

- [ ] **Step 1: 创建 TenantUpdateSelf schema**

```python
class TenantUpdateSelf(BaseModel):
    """租户用户自助更新 — 仅允许非敏感字段"""
    name: str | None = Field(None, min_length=1, max_length=100)
    industry: str | None = Field(None, max_length=50)
    notes: str | None = Field(None, max_length=1000)
    categories: list[str] | None = None
    onboarding_progress: dict | None = None
    enabled_features: dict | None = None
```

- [ ] **Step 2: 更新 /me PATCH 端点使用 TenantUpdateSelf**

- [ ] **Step 3: 运行测试验证**

---

## Task 2: admin_password 添加强度验证 (H-7)

**Files:**
- Modify: `backend/app/schemas/tenant.py`

- [ ] **Step 1: 在 TenantCreate 中添加密码强度验证**

使用 `validate_password_strength` 函数添加 field_validator。

- [ ] **Step 2: 运行测试验证**

---

## Task 3: 列表搜索添加 plan/tenant_type/status 过滤 (H-4)

**Files:**
- Modify: `backend/app/api/v1/tenants.py`

- [ ] **Step 1: 添加 plan、tenant_type、status 查询参数**

```python
plan: str | None = Query(None, description="按计划过滤")
tenant_type: str | None = Query(None, description="按类型过滤")
status: str | None = Query(None, description="按状态过滤"),
```

- [ ] **Step 2: 在查询中添加过滤条件**

- [ ] **Step 3: 运行测试验证**

---

## Task 4: 添加审计日志 (H-6)

**Files:**
- Modify: `backend/app/services/tenant.py`
- Modify: `backend/app/api/v1/tenants.py`

- [ ] **Step 1: 在 create_tenant、update_tenant、soft_delete_tenant 中添加审计日志**

- [ ] **Step 2: 运行测试验证**

---

## Task 5: 优化 create_tenant flush 次数 (H-3)

**Files:**
- Modify: `backend/app/services/tenant.py`

- [ ] **Step 1: 将 tenant + org + account 的 add 合并后只 flush 一次**

```python
db.add(tenant)
db.add(org)
db.add(account)
await db.flush()  # 单次 flush 获取所有 ID
```

- [ ] **Step 2: 运行测试验证**

---

## Task 6: Tenant 表添加索引 (H-2) + AgencyAuthorization 复合索引 (H-9)

**Files:**
- Modify: `backend/app/models/tenant.py`（添加 index=True）
- 创建: alembic 迁移文件

- [ ] **Step 1: 在 Tenant 模型中添加 status、plan 索引**

- [ ] **Step 2: 在 AgencyAuthorization 模型中添加复合索引**

- [ ] **Step 3: 生成并验证迁移**

注意：迁移文件创建需要人工确认，标记为延期。

---

## Task 7: slug 唯一性在 Service 层校验 (M-3)

**Files:**
- Modify: `backend/app/services/tenant.py`

- [ ] **Step 1: 在 create_tenant 中先查询 slug 是否已存在**

- [ ] **Step 2: 运行测试验证**

---

## Task 8: 补充安全边界测试 (H-8)

**Files:**
- Create: `backend/tests/test_api/test_tenant_security.py`

- [ ] **Step 1: 添加普通用户修改敏感字段测试**

测试 /me PATCH 无法修改 plan、quota、tenant_type。

- [ ] **Step 2: 添加 platform_admin CRUD 测试**

- [ ] **Step 3: 添加 slug 唯一性冲突测试**

- [ ] **Step 4: 运行测试验证**

---

## 延期决策（需人工）

1. **H-5: 添加租户暂停/恢复功能** — 需要产品确认 API 设计
2. **H-6 迁移文件** — 新索引的迁移需要人工确认
3. **M-4: LIKE 搜索优化** — pg_trgm 或全文搜索是全局架构决策
4. **M-6: get_tenant 缓存** — 需要设计缓存失效策略
5. **M-8: brands 页面概念混淆** — 需要产品确认命名规范
