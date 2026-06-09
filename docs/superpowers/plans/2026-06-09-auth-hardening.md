# Auth (认证模块) 改进计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复认证模块的 4 个 Critical、12 个 High 级别问题，重点解决安全漏洞（跨租户风险、权限缺失）、四层分离违规、测试覆盖不足，同时改善密码验证一致性和性能。

**Architecture:** 将 auth.py 和 password.py 中的业务逻辑抽取到 `AuthService` 和 `AccountService`；修复所有租户隔离漏洞；添加角色校验和速率限制；补充安全边界测试。

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, PostgreSQL, pytest, Next.js 16, Ant Design 6

---

## 文件变更映射

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `backend/app/services/auth.py` | 创建 | 认证业务逻辑（login, refresh, logout, password reset） |
| `backend/app/services/account.py` | 创建 | 账户操作（change_password, reset_password, get_account） |
| `backend/app/api/v1/auth.py` | 修改 | 瘦身为路由层，调用 AuthService；修复 C-1 tenant_id、添加审计日志 |
| `backend/app/api/v1/password.py` | 修改 | 瘦身为路由层，调用 AccountService；修复 C-4 角色校验、H-1 tenant_id |
| `backend/app/utils/security.py` | 修改 | 添加统一密码强度验证函数 |
| `backend/app/api/v1/agency_auth.py` | 修改 | 添加分页支持 |
| `backend/app/services/agency_auth.py` | 修改 | 添加分页参数 |
| `backend/app/core/dependencies.py` | 修改 | 添加 AsyncRedisCache 依赖注入 |
| `backend/tests/test_services/test_auth_service.py` | 创建 | AuthService 单元测试 |
| `backend/tests/test_api/test_auth_login.py` | 修改 | 添加速率限制、黑名单、锁定测试 |
| `backend/tests/test_api/test_password.py` | 修改 | 添加角色校验、tenant_id 验证测试 |

---

## Task 1: 统一密码强度验证函数 (H-3)

**Files:**
- Modify: `backend/app/utils/security.py`
- Modify: `backend/app/api/v1/auth.py`
- Modify: `backend/app/api/v1/password.py`

- [ ] **Step 1: 在 utils/security.py 中添加 validate_password_strength 函数**

```python
def validate_password_strength(password: str) -> None:
    """验证密码强度。不符合要求时抛出 ValueError。"""
    if len(password) < 8:
        raise ValueError("密码至少需要 8 位")
    if not re.search(r"[a-zA-Z]", password):
        raise ValueError("密码必须包含字母")
    if not re.search(r"\d", password):
        raise ValueError("密码必须包含数字")
```

- [ ] **Step 2: 替换 password.py 中的 validate_password_strength**

将 `password.py` 中的同名函数改为导入 `from app.utils.security import validate_password_strength`，API 层捕获 ValueError 转 HTTPException。

- [ ] **Step 3: 替换 auth.py 中 confirm_reset_password 的内联验证**

将 auth.py:412-416 的内联密码验证改为调用 `validate_password_strength(body.new_password)`，捕获 ValueError。

- [ ] **Step 4: 运行测试验证**

```bash
cd backend && python -m pytest tests/test_api/test_password.py tests/test_api/test_auth_login.py -q --tb=short
```

---

## Task 2: 修复 confirm_reset_password 跨租户风险 (C-1)

**Files:**
- Modify: `backend/app/api/v1/auth.py:432`

- [ ] **Step 1: 在 Redis token 记录中存储 tenant_id**

修改 `generate_reset_token` 函数，在 Redis 中存储时增加 `tenant_id` 字段：
```python
await cache.set(
    f"{RESET_TOKEN_KEY_PREFIX}:{account_uuid}",
    {"token_hash": _hash_reset_token(token), "account_id": str(account_uuid), "tenant_id": str(tenant_id)},
    ttl=RESET_TOKEN_TTL,
)
```

- [ ] **Step 2: 在 confirm_reset_password 中验证 tenant_id**

从 Redis 记录中获取 tenant_id，在查询 Account 时添加过滤：
```python
stored_tenant_id = record.get("tenant_id")
if stored_tenant_id:
    result = await db.execute(
        select(Account).where(Account.id == account_uuid, Account.tenant_id == uuid.UUID(stored_tenant_id))
    )
else:
    result = await db.execute(select(Account).where(Account.id == account_uuid))
```

- [ ] **Step 3: 添加测试验证跨租户拒绝**

```bash
cd backend && python -m pytest tests/test_api/test_auth_login.py -q --tb=short -k "reset"
```

---

## Task 3: 修复 /reset-password 权限控制缺失 (C-4) 和 change_password tenant_id (H-1)

**Files:**
- Modify: `backend/app/api/v1/password.py`

- [ ] **Step 1: 为 reset_password 添加角色校验**

添加 `get_current_role` 依赖，验证角色为 admin 或 platform_admin：
```python
@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    role: str = Depends(get_current_role),
):
    if role not in ("admin", "platform_admin"):
        raise HTTPException(status_code=403, detail="仅管理员可重置密码")
```

- [ ] **Step 2: 为 change_password 添加 tenant_id 验证**

添加 `get_current_tenant` 依赖，在查询中过滤 tenant_id：
```python
result = await db.execute(
    select(Account).where(Account.id == account_id, Account.tenant_id == tenant_id)
)
```

- [ ] **Step 3: 运行测试验证**

```bash
cd backend && python -m pytest tests/test_api/test_password.py -q --tb=short
```

---

## Task 4: 修复 LoginRequest.password 最小长度 (H-2)

**Files:**
- Modify: `backend/app/api/v1/auth.py:62`

- [ ] **Step 1: 修改 min_length**

```python
password: str = Field(..., min_length=6, description="密码", examples=["SecurePass123!"])
```

注：使用 6 而非 8，因为旧密码可能短于 8 位，过长的最小长度会阻止合法登录。

- [ ] **Step 2: 运行测试确保无破坏**

```bash
cd backend && python -m pytest tests/test_api/test_auth_login.py -q --tb=short
```

---

## Task 5: 创建 AuthService 抽取业务逻辑 (C-2)

**Files:**
- Create: `backend/app/services/auth.py`
- Modify: `backend/app/api/v1/auth.py`

- [ ] **Step 1: 创建 AuthService**

将 login、refresh、logout、generate_reset_token、confirm_reset_password 的业务逻辑抽取到 `backend/app/services/auth.py`。API 层只保留路由定义、请求解析和响应构建。

关键函数签名：
```python
async def authenticate_login(db, email, password, tenant_slug, client_ip, cache) -> dict
async def refresh_access_token(db, refresh_token, cache) -> dict
async def logout_session(access_token, refresh_token_str, cache) -> dict
async def generate_password_reset(db, account_id, tenant_id, cache) -> dict
async def confirm_password_reset(db, token, account_id, new_password, client_ip, cache) -> dict
```

- [ ] **Step 2: 瘦身 auth.py 路由层**

API 函数简化为：解析请求 → 调用 Service → 构建响应。

- [ ] **Step 3: 运行全量测试验证无回归**

```bash
cd backend && python -m pytest tests/test_api/test_auth_login.py tests/test_api/test_password.py tests/test_api/test_agency_auth.py -q --tb=short
```

---

## Task 6: Redis 缓存依赖注入 (H-6)

**Files:**
- Modify: `backend/app/core/dependencies.py`
- Modify: `backend/app/api/v1/auth.py`
- Modify: `backend/app/services/auth.py`（Task 5 创建后）

- [ ] **Step 1: 添加 get_redis_cache 依赖**

在 `dependencies.py` 中添加：
```python
from app.services.redis_cache import AsyncRedisCache

async def get_redis_cache() -> AsyncRedisCache:
    return AsyncRedisCache()
```

- [ ] **Step 2: 在 auth.py 路由中使用依赖注入**

替换所有 `cache = AsyncRedisCache()` 为 `cache: AsyncRedisCache = Depends(get_redis_cache)`。

- [ ] **Step 3: 运行测试验证**

```bash
cd backend && python -m pytest tests/test_api/test_auth_login.py -q --tb=short
```

---

## Task 7: Agency 授权列表添加分页 (H-5)

**Files:**
- Modify: `backend/app/services/agency_auth.py`
- Modify: `backend/app/api/v1/agency_auth.py`
- Modify: `backend/app/schemas/agency_auth.py`

- [ ] **Step 1: 修改 service 函数签名添加分页参数**

```python
async def list_authorizations_for_agency(db, agency_tenant_id, page=1, page_size=50) -> tuple[list[dict], int]
```

- [ ] **Step 2: 更新 API 端点添加 page/page_size 查询参数**

- [ ] **Step 3: 更新 AuthorizationListResponse 使用 PaginatedResponse**

- [ ] **Step 4: 运行测试验证**

```bash
cd backend && python -m pytest tests/test_api/test_agency_auth.py -q --tb=short
```

---

## Task 8: generate_reset_token 添加审计日志 (H-12)

**Files:**
- Modify: `backend/app/api/v1/auth.py` 或 `backend/app/services/auth.py`

- [ ] **Step 1: 在 generate_reset_token 中添加审计日志**

```python
from app.services.audit import write_audit_log
await write_audit_log(
    db,
    operator_id=str(account_id),
    target_tenant_id=str(tenant_id),
    action="generate_reset_token",
    resource=f"account:{account_uuid}",
)
```

- [ ] **Step 2: 运行测试验证**

---

## Task 9: reset-password 端点添加速率限制 (H-11)

**Files:**
- Modify: `backend/app/api/v1/password.py`

- [ ] **Step 1: 在 reset_password 中添加速率限制**

```python
cache = AsyncRedisCache()
allowed, _ = await cache.rate_limit_check(f"admin_reset:{account_id}", max_attempts=10, window_seconds=60)
if not allowed:
    raise HTTPException(status_code=429, detail="重置操作过于频繁", headers={"Retry-After": "60"})
```

- [ ] **Step 2: 运行测试验证**

---

## Task 10: 补充安全边界测试 (H-9, H-10)

**Files:**
- Create: `backend/tests/test_api/test_auth_security_boundary.py`

- [ ] **Step 1: 添加 IP 速率限制测试**

测试 login 端点超过 20 次/分钟返回 429。

- [ ] **Step 2: 添加 JWT 黑名单有效性测试**

测试 logout 后 access token 被加入黑名单，再次使用返回 401。

- [ ] **Step 3: 添加 refresh token 轮换测试**

测试 refresh 后旧 refresh token 不可重用。

- [ ] **Step 4: 添加账户锁定测试完善**

测试锁定时间过期后自动解锁。

- [ ] **Step 5: 运行新增测试**

```bash
cd backend && python -m pytest tests/test_api/test_auth_security_boundary.py -q --tb=short
```

---

## 延期决策（需人工）

以下问题需要人工决策，不在自动执行范围内：

1. **C-3: Agency 上下文切换前端 UI** — 需要设计评审确定 UI 方案后再实现
2. **H-4: Demo 账号密码明文** — 需决策是否保留 Demo 快捷登录功能
3. **M-1: agency_auth.py 双 router 前缀** — 需确认是否拆分为独立文件，涉及前端路由同步
4. **M-2: 统一错误响应格式** — 属于全局改进，不应在单模块内处理
5. **M-4: JWT UUID uuid4 vs uuid7** — 安全性 vs 可排序性权衡，需架构决策
