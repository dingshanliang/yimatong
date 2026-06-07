# 认证与权限模块安全加固 实施计划

> **Status:** ✅ Completed
> **Completed:** 2026-06-07
> **Created:** 2026-06-07

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复认证与权限模块审查发现的 30 个安全问题，涵盖 JWT 安全加固、RBAC 系统统一、HttpOnly Cookie 迁移、数据模型修复和代码质量改进。

**Architecture:** 分 5 个阶段（Phase）逐步实施。Phase 1 修 Critical 安全漏洞；Phase 2 统一 RBAC；Phase 3 迁移 HttpOnly Cookie；Phase 4 修数据模型；Phase 5 代码质量。每个 Phase 完成后可独立部署验证。

**Tech Stack:** FastAPI、SQLAlchemy 2.0 async、Alembic、PyJWT/jose、bcrypt、Redis、Zustand、Next.js middleware、Axios

**审查报告来源:** 2026-06-07 认证与权限模块多维度审查

---

## 问题索引

| ID | 严重度 | 简述 | 所属 Phase |
|----|--------|------|-----------|
| C1 | Critical | JWT 弱默认密钥 | 1 |
| C2 | Critical | Refresh Token 无轮换 | 1 |
| C3 | Critical | 密码重置无速率限制 | 1 |
| C4 | Critical | JWT 用户权限未加载 | 1 |
| C5 | Critical | Roles API 无权限守卫 | 1 |
| C6 | Critical | Agency 撤销无归属校验 | 1 |
| C7 | Critical | 平台配置存储在进程内存 | 2 |
| H1 | High | 登录缺少 IP 速率限制 | 1 |
| H2 | High | 平台登录无安全防护 | 1 |
| H3 | High | Token 存 localStorage (XSS) | 3 |
| H4 | High | 两套独立 RBAC 系统 | 2 |
| H5 | High | tenant_id 缺外键约束 | 4 |
| H6 | High | POST /tenants 公开无保护 | 1 |
| H7 | High | scope 类型 Mapped[dict] 错误 | 4 |
| H8 | High | 前端 JWT 解码无验证 | 3 |
| M1 | Medium | 登录时序攻击 | 5 |
| M2 | Medium | 密码重置令牌明文存 Redis | 5 |
| M3 | Medium | _resolve_account_role 不安全 fallback | 5 |
| M4 | Medium | 事务管理模式不一致 | 5 |
| M5 | Medium | 前端无登录防抖 | 3 |
| M6 | Medium | Platform 无 refresh 机制 | 3 |
| M7 | Medium | Platform 中间件不验证 token | 3 |
| M8 | Medium | Scan Token 不验证 IP | 2 |
| M9 | Medium | 登出不撤销 refresh token | 1 |
| M10 | Medium | Agency context URL 可能不匹配 | 3 |
| L1 | Low | 错误消息语言不一致 | 5 |
| L2 | Low | Account.email 无唯一约束 | 4 |
| L3 | Low | require_role 懒导入 | 5 |
| L4 | Low | 模块级 hydrate 风险 | 3 |
| L5 | Low | 密码重置无审计日志 | 5 |

---

## File Structure

| File | Action | Responsibility | Phase |
|------|--------|---------------|-------|
| `backend/app/core/config.py` | Modify | 移除 secret_key 默认值，添加新配置项 | 1 |
| `backend/app/utils/security.py` | Modify | Refresh token 轮换、登出撤销 | 1 |
| `backend/app/api/v1/auth.py` | Modify | 权限加载、速率限制、时序攻击修复 | 1 |
| `backend/app/middleware/tenant.py` | Modify | JWT 用户权限加载到 request.state | 1 |
| `backend/app/middleware/auth.py` | Modify | 统一权限检查接口 | 2 |
| `backend/app/core/permissions.py` | Delete → Merge | 合并到 rbac.py | 2 |
| `backend/app/utils/rbac.py` | Modify → Rename | 统一 RBAC 系统 | 2 |
| `backend/app/api/v1/roles.py` | Modify | 添加权限守卫 | 1 |
| `backend/app/api/v1/agency_auth.py` | Modify | 撤销归属校验、审计日志 | 1 |
| `backend/app/services/agency_auth.py` | Modify | 撤销时校验归属 | 1 |
| `backend/app/api/v1/platform.py` | Modify | 登录安全、配置持久化、refresh | 2 |
| `backend/app/services/scan_token.py` | Modify | IP 验证 | 2 |
| `backend/app/models/tenant.py` | Modify | 外键约束、scope 类型、email 唯一约束 | 4 |
| `backend/app/services/redis_cache.py` | Modify | 新增速率限制方法 | 1 |
| `backend/app/models/platform_config.py` | Create | 平台配置持久化模型 | 2 |
| `backend/alembic/versions/xxx_*.py` | Create | 数据模型变更迁移 | 4 |
| `backend/tests/unit/test_auth_security.py` | Create | 认证安全单元测试 | 1 |
| `backend/tests/unit/test_rbac_unified.py` | Create | 统一 RBAC 测试 | 2 |
| `backend/tests/unit/test_agency_auth.py` | Modify | 归属校验测试 | 1 |
| `frontend/apps/admin/src/lib/auth.ts` | Modify | HttpOnly Cookie 适配、登录防抖 | 3 |
| `frontend/apps/admin/src/lib/api.ts` | Modify | 移除手动 token 注入，改为 credentials | 3 |
| `frontend/apps/admin/src/middleware.ts` | Modify | Token 验证增强 | 3 |
| `frontend/apps/platform/src/lib/platform-auth.ts` | Modify | HttpOnly Cookie 适配 + refresh | 3 |
| `frontend/apps/platform/src/middleware.ts` | Modify | Token 验证增强 | 3 |
| `frontend/apps/platform/src/lib/api.ts` | Modify | credentials 适配 | 3 |

---

## Phase 1: Critical 安全漏洞修复

> 修复 7 个 Critical + 3 个 High 问题，完成后可独立部署。

### Task 1: JWT 弱默认密钥 + 权限加载 [C1, C4]

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/middleware/tenant.py`
- Create: `backend/tests/unit/test_auth_security.py`

- [ ] **Step 1: 移除 secret_key 默认值**

修改 `backend/app/core/config.py`，找到 `secret_key` 配置项：

```python
# 修改前（约第 7 行附近）:
secret_key: str = "dev-secret-key-change-in-production"

# 修改后:
secret_key: str = ""  # 必须通过环境变量 SECRET_KEY 设置
```

在同一文件的 model_validator 中添加启动检查：

```python
from pydantic import model_validator

@model_validator(mode="after")
def _validate_auth_config(self) -> "Settings":
    if not self.secret_key:
        raise ValueError(
            "SECRET_KEY 环境变量未设置。"
            "生成方法: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )
    return self
```

- [ ] **Step 2: 验证 secret_key 验证生效**

运行: `cd backend && SECRET_KEY="" uvicorn app.main:app 2>&1 | head -5`
预期: 启动失败，显示 SECRET_KEY 未设置错误

运行: `SECRET_KEY="test-key-at-least-32-chars-long" uvicorn app.main:app 2>&1 | head -5`
预期: 正常启动（或连接数据库失败，但不报 SECRET_KEY 错误）

- [ ] **Step 3: 编写 JWT 权限加载测试**

Create `backend/tests/unit/test_auth_security.py`:

```python
"""认证安全模块单元测试"""

import uuid

import pytest


@pytest.mark.asyncio
async def test_jwt_auth_loads_permissions_to_request_state():
    """JWT 认证应将账户的权限列表加载到 request.state.permissions"""
    # 这个测试验证 middleware 在 JWT 认证后设置了 permissions
    # 具体实现依赖 mock Request/DB
    # 占位：将在 Step 5 中补充完整实现
    pass


def test_config_rejects_empty_secret_key():
    """空 secret_key 应在启动时拒绝"""
    import os

    from pydantic import ValidationError

    from app.core.config import Settings

    original = os.environ.get("SECRET_KEY")
    os.environ["SECRET_KEY"] = ""
    try:
        with pytest.raises((ValidationError, ValueError)):
            Settings()
    finally:
        if original:
            os.environ["SECRET_KEY"] = original
        else:
            os.environ.pop("SECRET_KEY", None)
```

- [ ] **Step 4: 在 JWT 认证中间件中加载用户权限**

修改 `backend/app/middleware/tenant.py` 的 `_authenticate_jwt` 方法。在设置 `request.state.auth_method = "jwt"` 之后（约第 70 行），添加权限加载逻辑：

```python
        request.state.auth_method = "jwt"
        # 加载数据库中的权限到 request.state.permissions
        request.state.permissions = await self._load_permissions(
            payload.get("sub"), payload.get("role")
        )
```

在 `TenantScopeMiddleware` 类中添加权限加载方法：

```python
    async def _load_permissions(self, account_id: str | None, role: str | None) -> list[str]:
        """从数据库加载账户的权限列表"""
        if not account_id:
            return []
        try:
            from app.core.database import async_session_factory
            from app.models.tenant import Account
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            async with async_session_factory() as db:
                result = await db.execute(
                    select(Account)
                    .options(selectinload(Account.roles).selectinload("permissions"))
                    .where(Account.id == uuid.UUID(account_id))
                )
                account = result.scalar_one_or_none()
                if not account:
                    return []
                permissions = set()
                for role_obj in account.roles:
                    for perm in role_obj.permissions:
                        permissions.add(perm.code)
                return list(permissions)
        except Exception:
            # 权限加载失败不应阻断请求，降级为空权限
            return []
```

- [ ] **Step 5: 补充权限加载测试**

更新 `backend/tests/unit/test_auth_security.py` 中的测试：

```python
@pytest.mark.asyncio
async def test_jwt_auth_loads_permissions_to_request_state():
    """JWT 认证应将账户的权限列表加载到 request.state.permissions"""
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.middleware.tenant import TenantScopeMiddleware

    middleware = TenantScopeMiddleware(app=MagicMock())

    # Mock DB 返回带权限的账户
    mock_perm = MagicMock()
    mock_perm.code = "product:create"

    mock_role = MagicMock()
    mock_role.permissions = [mock_perm]

    mock_account = MagicMock()
    mock_account.roles = [mock_role]

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_account

    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.middleware.tenant.async_session_factory", return_value=mock_session):
        permissions = await middleware._load_permissions("some-account-id", "admin")

    assert "product:create" in permissions


@pytest.mark.asyncio
async def test_load_permissions_returns_empty_on_failure():
    """权限加载失败应降级为空列表"""
    from app.middleware.tenant import TenantScopeMiddleware

    middleware = TenantScopeMiddleware(app=MagicMock())

    with patch("app.middleware.tenant.async_session_factory", side_effect=Exception("DB error")):
        permissions = await middleware._load_permissions("some-id", "admin")

    assert permissions == []


@pytest.mark.asyncio
async def test_load_permissions_returns_empty_for_no_account():
    """找不到账户应返回空列表"""
    from app.middleware.tenant import TenantScopeMiddleware

    middleware = TenantScopeMiddleware(app=MagicMock())

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.middleware.tenant.async_session_factory", return_value=mock_session):
        permissions = await middleware._load_permissions("nonexistent-id", "admin")

    assert permissions == []
```

- [ ] **Step 6: 运行测试验证**

Run: `cd backend && python -m pytest tests/unit/test_auth_security.py -v`
预期: 4 个测试全部通过

- [ ] **Step 7: Commit**

```bash
git add backend/app/core/config.py backend/app/middleware/tenant.py backend/tests/unit/test_auth_security.py
git commit -m "fix(auth): force SECRET_KEY env var and load JWT user permissions into request.state (C1, C4)"
```

---

### Task 2: Refresh Token 轮换 + 登出撤销 [C2, M9]

**Files:**
- Modify: `backend/app/utils/security.py`
- Modify: `backend/app/api/v1/auth.py`
- Modify: `backend/tests/unit/test_auth_security.py`

- [ ] **Step 1: 编写 refresh token 轮换的失败测试**

在 `backend/tests/unit/test_auth_security.py` 中添加：

```python
def test_verify_refresh_token_rejects_blacklisted():
    """已撤销的 refresh token 应被拒绝"""
    from app.utils.security import create_refresh_token, verify_refresh_token
    from unittest.mock import AsyncMock, patch

    token = create_refresh_token("test-account-id")
    payload = verify_refresh_token(token)
    assert payload is not None  # 未撤销时有效

    # 模拟 token 已被撤销
    with patch("app.utils.security.AsyncRedisCache") as mock_cache_cls:
        mock_cache = AsyncMock()
        mock_cache.is_token_revoked.return_value = True  # Redis 中已撤销
        mock_cache_cls.return_value = mock_cache
        result = verify_refresh_token(token)
        assert result is None  # 应被拒绝
```

- [ ] **Step 2: 修改 verify_refresh_token 检查黑名单**

修改 `backend/app/utils/security.py` 的 `verify_refresh_token` 函数：

```python
def verify_refresh_token(token: str) -> dict | None:
    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            return None
        return payload
    except JWTError:
        return None
```

改为：

```python
async def verify_refresh_token(token: str) -> dict | None:
    """验证 refresh token，检查黑名单。"""
    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            return None
        jti = payload.get("jti")
        if jti:
            from app.services.redis_cache import AsyncRedisCache
            cache = AsyncRedisCache()
            if await cache.is_token_revoked(jti):
                return None
        return payload
    except JWTError:
        return None
```

- [ ] **Step 3: 修改 /refresh 端点实现轮换**

修改 `backend/app/api/v1/auth.py` 的 `refresh` 函数（约第 119-145 行）：

```python
@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="刷新 Token",
)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    from app.utils.security import verify_refresh_token

    payload = await verify_refresh_token(body.refresh_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # 将旧 refresh token 加入黑名单（轮换）
    old_jti = payload.get("jti")
    if old_jti:
        old_exp = payload.get("exp")
        if old_exp:
            remaining = max(1, int(old_exp - datetime.now(UTC).timestamp()))
        else:
            remaining = settings.refresh_token_expire_days * 86400
        cache = AsyncRedisCache()
        await cache.revoke_token(old_jti, ttl=remaining)

    account_id = payload["sub"]
    # ... 后续代码不变（查询 account、创建新 token）
```

- [ ] **Step 4: 修改 /logout 端点撤销 refresh token**

修改 `backend/app/api/v1/auth.py` 的 `logout` 函数。在撤销 access token 之后（约第 218 行），添加 refresh token 撤销：

```python
    cache = AsyncRedisCache()
    await cache.revoke_token(jti, ttl=remaining)

    # 同时撤销关联的 refresh token（从 localStorage cookie 读取）
    refresh_token_str = request.cookies.get("refresh_token") or ""
    if refresh_token_str:
        try:
            from app.utils.security import decode_token
            refresh_payload = decode_token(refresh_token_str)
            refresh_jti = refresh_payload.get("jti")
            if refresh_jti:
                refresh_exp = refresh_payload.get("exp")
                refresh_remaining = max(1, int(refresh_exp - datetime.now(UTC).timestamp())) if refresh_exp else settings.refresh_token_expire_days * 86400
                await cache.revoke_token(refresh_jti, ttl=refresh_remaining)
        except Exception:
            pass  # refresh token 无效或格式错误，忽略

    return {"status": "ok"}
```

- [ ] **Step 5: 运行测试**

Run: `cd backend && python -m pytest tests/unit/test_auth_security.py -v`
预期: 全部通过

- [ ] **Step 6: Commit**

```bash
git add backend/app/utils/security.py backend/app/api/v1/auth.py backend/tests/unit/test_auth_security.py
git commit -m "fix(auth): refresh token rotation and revoke refresh on logout (C2, M9)"
```

---

### Task 3: Roles API 权限守卫 + Agency 撤销归属校验 [C5, C6]

**Files:**
- Modify: `backend/app/api/v1/roles.py`
- Modify: `backend/app/api/v1/agency_auth.py`
- Modify: `backend/app/services/agency_auth.py`

- [ ] **Step 1: 给 Roles API 添加 admin 权限守卫**

修改 `backend/app/api/v1/roles.py`，给所有写操作路由添加 `require_role("admin")` 依赖：

```python
from app.utils.rbac import require_role

# 修改以下路由签名：

@router.post("", status_code=201)
async def create_role(
    body: RoleCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),  # 新增
):

@router.post("/permissions", status_code=201)
async def create_permission(
    body: PermissionCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),  # 新增
):

@router.post("/{role_id}/permissions/{permission_id}")
async def assign_permission(
    role_id: uuid.UUID,
    permission_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),  # 新增
):

@router.delete("/{role_id}")
async def delete_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),  # 新增
):
```

读取路由（`list_roles`, `list_permissions`）保持不变（所有已认证用户可查看）。

- [ ] **Step 2: Agency 撤销添加归属校验**

修改 `backend/app/api/v1/agency_auth.py` 的 `revoke_auth` 函数（约第 86-98 行）：

```python
@router.delete("/{auth_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_auth(
    auth_id: uuid.UUID,
    tenant_type: str = Depends(get_current_tenant_type),
    tenant_id: uuid.UUID = Depends(get_current_tenant),  # 新增
    db: AsyncSession = Depends(get_db),
):
    """Brand 撤销 agency 授权"""
    _require_brand(tenant_type)

    auth = await revoke_authorization(db, auth_id, client_tenant_id=tenant_id)  # 传入 tenant_id
    if not auth:
        raise HTTPException(status_code=404, detail="授权记录不存在")
    await db.flush()
```

修改 `backend/app/services/agency_auth.py` 的 `revoke_authorization` 函数：

```python
async def revoke_authorization(
    db: AsyncSession, auth_id: uuid.UUID, client_tenant_id: uuid.UUID | None = None
) -> AgencyAuthorization | None:
    """撤销授权。若提供 client_tenant_id，则同时验证归属。"""
    result = await db.execute(select(AgencyAuthorization).where(AgencyAuthorization.id == auth_id))
    auth = result.scalar_one_or_none()
    if not auth:
        return None
    if auth.status != AgencyAuthStatus.active:
        return None
    # 归属校验：确保只能撤销属于自己的授权
    if client_tenant_id and auth.client_tenant_id != client_tenant_id:
        return None
    auth.status = AgencyAuthStatus.revoked
    auth.revoked_at = datetime.now(UTC)
    await db.flush()
    return auth
```

- [ ] **Step 3: 运行测试验证**

Run: `cd backend && python -m pytest tests/ -k "role or agency" -v --timeout=30`
预期: 相关测试通过（如无现成测试则手动验证）

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/v1/roles.py backend/app/api/v1/agency_auth.py backend/app/services/agency_auth.py
git commit -m "fix(auth): add admin guard to roles API and agency revoke ownership check (C5, C6)"
```

---

### Task 4: 速率限制与暴力破解防护 [C3, H1, H2, H6]

**Files:**
- Modify: `backend/app/services/redis_cache.py`
- Modify: `backend/app/api/v1/auth.py`
- Modify: `backend/app/middleware/tenant.py`

- [ ] **Step 1: 在 RedisCache 中添加速率限制方法**

修改 `backend/app/services/redis_cache.py`，添加：

```python
async def rate_limit_check(self, key: str, max_attempts: int, window_seconds: int) -> tuple[bool, int]:
    """滑动窗口速率限制。返回 (allowed, remaining_attempts)。"""
    import time

    now = time.time()
    window_start = now - window_seconds

    pipe = self.redis.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zcard(key)
    pipe.zadd(key, {str(now): now})
    pipe.expire(key, window_seconds)
    results = await pipe.execute()

    current_count = results[1]
    remaining = max(0, max_attempts - current_count - 1)
    allowed = current_count < max_attempts
    return allowed, remaining
```

- [ ] **Step 2: 给 /login 添加 IP 速率限制**

修改 `backend/app/api/v1/auth.py` 的 `login` 函数，在函数开头添加：

```python
@router.post("/login", ...)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    # IP 速率限制：每 IP 每分钟最多 20 次登录尝试
    client_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown").split(",")[0].strip()
    cache = AsyncRedisCache()
    allowed, remaining = await cache.rate_limit_check(
        f"login_rate:{client_ip}", max_attempts=20, window_seconds=60
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="登录尝试过于频繁，请稍后再试",
            headers={"Retry-After": "60"},
        )

    # ... 原有登录逻辑
```

注意：函数签名需要添加 `request: Request` 参数。

- [ ] **Step 3: 给 /confirm-reset-password 添加速率限制**

修改 `backend/app/api/v1/auth.py` 的 `confirm_reset_password` 函数，添加：

```python
@router.post("/confirm-reset-password", ...)
async def confirm_reset_password(
    body: ConfirmResetPasswordRequest,
    request: Request,  # 新增
    db: AsyncSession = Depends(get_db),
):
    # 速率限制：每 account_id 每分钟最多 5 次尝试
    cache = AsyncRedisCache()
    allowed, _ = await cache.rate_limit_check(
        f"reset_rate:{body.account_id}", max_attempts=5, window_seconds=60
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="重置尝试过于频繁，请稍后再试",
            headers={"Retry-After": "60"},
        )

    # ... 原有重置逻辑
```

- [ ] **Step 4: 给平台登录添加速率限制和审计**

修改 `backend/app/api/v1/platform.py` 的 `platform_login` 函数：

```python
@router.post("/auth/login", response_model=PlatformTokenResponse)
async def platform_login(body: PlatformLoginRequest, request: Request):
    """平台管理员独立认证路径"""
    # IP 速率限制
    client_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown").split(",")[0].strip()
    cache = AsyncRedisCache()
    allowed, _ = await cache.rate_limit_check(
        f"platform_login_rate:{client_ip}", max_attempts=10, window_seconds=300
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="尝试过于频繁", headers={"Retry-After": "300"})

    if not settings.platform_admin_password_hash:
        raise HTTPException(status_code=500, detail="Platform admin not configured")
    if (
        body.email != settings.platform_admin_email
        or not verify_password(body.password, settings.platform_admin_password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(
        tenant_id="platform",
        account_id="platform-admin",
        role="platform_admin",
    )

    # 审计日志（异步，不阻塞响应）
    try:
        from app.services.audit import write_audit_log
        from app.core.database import async_session_factory
        async with async_session_factory() as db:
            await write_audit_log(db, "platform-admin", "platform", "platform_login", f"ip:{client_ip}")
            await db.commit()
    except Exception:
        pass  # 审计失败不影响登录

    return PlatformTokenResponse(access_token=token)
```

- [ ] **Step 5: POST /tenants 添加邀请码机制**

修改 `backend/app/middleware/tenant.py` 的公开路由列表，移除 POST /tenants 的公开豁免：

```python
# 修改前（约第 38 行）:
        or (request.url.path == "/api/v1/tenants" and request.method == "POST")

# 修改后: 删除这一行
```

修改 `backend/app/api/v1/auth.py`，在注册端点（如果有）前添加邀请码验证。如果 POST /tenants 是平台管理创建租户，则不应公开，已被 platform.py 的 `create_tenant` 覆盖。如果是自助注册，需要添加邀请码：

```python
# 如果 /api/v1/tenants POST 路由存在且用于注册，添加邀请码验证
class RegisterRequest(BaseModel):
    name: str
    slug: str
    admin_email: EmailStr
    admin_name: str
    admin_password: str = Field(..., min_length=8)
    invite_code: str | None = Field(None, description="邀请码（如需要）")

@router.post("/register", ...)
async def register_tenant(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    if settings.registration_invite_code and body.invite_code != settings.registration_invite_code:
        raise HTTPException(status_code=400, detail="邀请码无效")
    # ... 创建租户逻辑
```

在 `config.py` 添加：

```python
registration_invite_code: str | None = None  # 可选的注册邀请码
```

- [ ] **Step 6: 运行测试**

Run: `cd backend && python -m pytest tests/ -k "login or reset or platform" -v --timeout=30`

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/redis_cache.py backend/app/api/v1/auth.py backend/app/api/v1/platform.py backend/app/middleware/tenant.py backend/app/core/config.py
git commit -m "fix(auth): rate limiting for login, password reset, platform login; remove public tenant creation (C3, H1, H2, H6)"
```

---

## Phase 2: RBAC 统一 + 平台加固

> 统一两套 RBAC 系统，修复平台配置持久化，Scan Token IP 验证。

### Task 5: 统一 RBAC 系统 [H4]

**Files:**
- Rename: `backend/app/utils/rbac.py` → `backend/app/utils/auth_rbac.py`
- Delete: `backend/app/core/permissions.py` (内容合并)
- Modify: `backend/app/middleware/auth.py`
- Modify: 所有引用 permissions.py 的文件

- [ ] **Step 1: 创建统一的 RBAC 模块**

将 `backend/app/core/permissions.py` 的 API Key 角色和 `backend/app/utils/rbac.py` 的 Web 角色合并到新文件 `backend/app/utils/auth_rbac.py`：

```python
"""统一 RBAC 系统 — Web 用户 + API Key 共享权限命名空间。

Web 用户角色（JWT）: admin, operator, platform_admin
API Key 角色: data_reader, coupon_operator, webhook_admin, full_access

权限码统一使用 resource:action 格式。
"""

from fastapi import Depends, HTTPException, Request

# ── Web 用户角色 → 权限映射 ──────────────────────────────────────

WEB_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin": [
        "tenant:manage", "account:manage", "role:manage",
        "product:create", "product:update", "product:delete",
        "code:generate", "code:export",
        "page:create", "page:publish",
        "campaign:create", "campaign:manage",
        "analytics:view", "export:run",
    ],
    "operator": [
        "product:create", "product:update",
        "code:generate", "code:export",
        "page:create", "page:publish",
        "campaign:create", "analytics:view",
    ],
    "platform_admin": [
        "platform:admin", "tenant:manage", "account:manage", "role:manage",
        "product:create", "product:update", "product:delete",
        "code:generate", "code:export",
        "page:create", "page:publish",
        "campaign:create", "campaign:manage",
        "analytics:view", "export:run",
    ],
}

# ── API Key 角色 → 权限映射 ──────────────────────────────────────

API_KEY_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "data_reader": [
        "scan:list", "scan:detail",
        "consumer:list", "consumer:detail",
        "claim:list", "claim:detail",
        "event:list", "event:detail",
    ],
    "coupon_operator": [
        "scan:list", "scan:detail",
        "consumer:list", "consumer:detail",
        "claim:list", "claim:detail",
        "event:list", "event:detail",
        "coupon:issue", "coupon:redeem", "coupon:detail",
    ],
    "webhook_admin": [
        "webhook:endpoint_create", "webhook:endpoint_list",
        "webhook:endpoint_update", "webhook:endpoint_delete",
        "webhook:delivery_list", "webhook:delivery_retry",
        "api_key:create", "api_key:list", "api_key:revoke",
    ],
    "full_access": [
        "scan:list", "scan:detail",
        "consumer:list", "consumer:detail",
        "claim:list", "claim:detail", "claim:create", "claim:update",
        "event:list", "event:detail",
        "coupon:issue", "coupon:redeem", "coupon:detail", "coupon:delete",
        "campaign:update", "campaign:status",
        "code:batch_create", "code:batch_update",
        "webhook:endpoint_create", "webhook:endpoint_list",
        "webhook:endpoint_update", "webhook:endpoint_delete",
        "webhook:delivery_list", "webhook:delivery_retry",
        "api_key:create", "api_key:list", "api_key:revoke",
    ],
}

# ── 合并 ────────────────────────────────────────────────────────

ALL_ROLE_PERMISSIONS = {**WEB_ROLE_PERMISSIONS, **API_KEY_ROLE_PERMISSIONS}
VALID_WEB_ROLES = list(WEB_ROLE_PERMISSIONS.keys())
VALID_API_KEY_ROLES = list(API_KEY_ROLE_PERMISSIONS.keys())


def get_permissions_for_role(role: str) -> list[str]:
    """根据角色名获取权限列表（Web 和 API Key 统一接口）。"""
    return ALL_ROLE_PERMISSIONS.get(role, [])


def role_has_permission(role: str, permission: str) -> bool:
    """检查角色是否包含指定权限。"""
    return permission in ALL_ROLE_PERMISSIONS.get(role, [])


async def _get_tenant_type_from_request(request: Request) -> str:
    """从 request.state 读取 tenant_type，默认 'brand'。"""
    return getattr(request.state, "tenant_type", "brand")


def require_tenant_type(*allowed_types: str):
    """FastAPI 依赖：限制只有特定 tenant_type 可访问。"""
    async def _check(tenant_type: str = Depends(_get_tenant_type_from_request)):
        if tenant_type not in allowed_types:
            raise HTTPException(
                status_code=403,
                detail=f"该操作需要 {', '.join(allowed_types)} 类型租户",
            )
        return tenant_type
    return _check


def require_role(*allowed_roles: str):
    """FastAPI 依赖注入：检查当前用户角色是否在允许列表中。"""
    async def _check(request: Request):
        role = getattr(request.state, "role", None)
        if role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return role
    return _check


def require_permission(permission: str):
    """FastAPI 依赖：检查当前请求的权限。支持 JWT 和 API Key。"""
    async def _check(request: Request) -> None:
        permissions: list[str] = getattr(request.state, "permissions", [])
        if permission not in permissions:
            raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")
    return _check
```

- [ ] **Step 2: 更新所有引用**

全局搜索替换：
- `from app.core.permissions import` → `from app.utils.auth_rbac import`
- `from app.utils.rbac import` → `from app.utils.auth_rbac import`

涉及的文件：
- `backend/app/api/v1/agency_auth.py`
- `backend/app/api/v1/platform.py`
- `backend/app/middleware/auth.py`（移除 `require_permission`，改为从 `auth_rbac` 导入）
- 所有使用 `require_role` / `require_tenant_type` / `require_permission` 的 API 文件

- [ ] **Step 3: 简化 auth.py 中间件**

修改 `backend/app/middleware/auth.py`，移除 `require_permission` 函数（已合并到 `auth_rbac.py`）。保留 `authenticate_api_key` 函数。

- [ ] **Step 4: 删除旧文件**

```bash
rm backend/app/core/permissions.py
rm backend/app/utils/rbac.py
```

- [ ] **Step 5: 运行全量测试**

Run: `cd backend && python -m pytest tests/ -v --timeout=60`

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(auth): unify RBAC system — merge permissions.py and rbac.py into auth_rbac.py (H4)"
```

---

### Task 6: 平台配置持久化 + Scan Token IP 验证 [C7, M8]

**Files:**
- Create: `backend/app/models/platform_config.py`
- Modify: `backend/app/api/v1/platform.py`
- Modify: `backend/app/services/scan_token.py`
- Create: `backend/alembic/versions/xxx_platform_config_table.py`

- [ ] **Step 1: 创建 PlatformConfig 模型**

Create `backend/app/models/platform_config.py`:

```python
"""平台级配置持久化模型"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class PlatformConfig(Base):
    __tablename__ = "platform_configs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
```

- [ ] **Step 2: 生成 Alembic 迁移**

Run: `cd backend && alembic revision --autogenerate -m "add platform_configs table"`

验证迁移：检查生成的迁移文件包含 `create_table('platform_configs')`。

- [ ] **Step 3: 修改 platform.py 使用数据库**

修改 `backend/app/api/v1/platform.py`，将 `_platform_config` dict 替换为数据库查询：

```python
# 删除旧的内存 dict:
# _platform_config: dict = { ... }

@router.get("/config", response_model=PlatformConfigRead)
async def get_platform_config(
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """读取平台级系统配置"""
    from app.models.platform_config import PlatformConfig
    result = await db.execute(select(PlatformConfig))
    configs = result.scalars().all()
    data = {c.key: c.value for c in configs}
    return PlatformConfigRead(
        feature_flags=data.get("feature_flags", {}),
        notification_settings=data.get("notification_settings", {}),
        compliance_defaults=data.get("compliance_defaults", {}),
    )


@router.patch("/config", response_model=PlatformConfigRead)
async def update_platform_config(
    body: PlatformConfigUpdate,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """更新系统配置"""
    from app.models.platform_config import PlatformConfig
    for key, value in body.model_dump(exclude_unset=True).items():
        existing = await db.execute(select(PlatformConfig).where(PlatformConfig.key == key))
        config = existing.scalar_one_or_none()
        if config:
            config.value = value
        else:
            db.add(PlatformConfig(key=key, value=value))
    await db.flush()

    await write_audit_log(db, "platform-admin", "platform", "update_config", "platform_config")
    await db.flush()

    # 返回最新配置
    return await get_platform_config(db=db, _role=_role)
```

- [ ] **Step 4: 修复 Scan Token IP 验证**

修改 `backend/app/services/scan_token.py` 的 `verify_scan_token` 函数：

```python
def verify_scan_token(token: str, expected_public_id: str, expected_ip_hash: str | None = None) -> dict | None:
    """验证 scan_token，可选验证 IP hash。"""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.exceptions.DecodeError:
        return None
    except jwt.exceptions.ExpiredSignatureError:
        return None

    if payload.get("type") != "scan_token":
        return None

    if payload.get("public_id") != expected_public_id:
        return None

    # IP hash 验证
    if expected_ip_hash and payload.get("ip_hash") != expected_ip_hash:
        return None

    return payload
```

- [ ] **Step 5: 更新 scan_token 调用点**

搜索所有调用 `verify_scan_token` 的位置，传入 `expected_ip_hash` 参数。在 `backend/app/api/v1/` 中的码解析路由中，需要从请求获取 IP 并计算 hash 后传入。

- [ ] **Step 6: 运行迁移和测试**

Run: `cd backend && alembic upgrade head`
Run: `cd backend && python -m pytest tests/ -v --timeout=60`

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "fix(auth): persist platform config to DB and verify scan token IP hash (C7, M8)"
```

---

## Phase 3: HttpOnly Cookie 迁移

> 将 Token 存储从 localStorage 迁移到 HttpOnly Cookie，同时修复前端相关问题。

### Task 7: 后端 HttpOnly Cookie 支持 [H3]

**Files:**
- Modify: `backend/app/api/v1/auth.py`
- Modify: `backend/app/api/v1/platform.py`
- Modify: `backend/app/core/config.py`

- [ ] **Step 1: 添加 cookie 安全配置**

修改 `backend/app/core/config.py`：

```python
# Cookie 安全配置
cookie_domain: str = ""
cookie_secure: bool = True  # 生产环境必须 HTTPS
cookie_samesite: str = "Lax"  # 或 "Strict"
```

- [ ] **Step 2: 创建 cookie 设置工具函数**

在 `backend/app/utils/security.py` 末尾添加：

```python
from starlette.responses import Response


def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str | None = None,
    max_age_access: int = 900,
    max_age_refresh: int = 30 * 86400,
) -> None:
    """设置 HttpOnly 认证 cookie。"""
    from app.core.config import settings

    domain = settings.cookie_domain or None
    secure = settings.cookie_secure
    samesite = settings.cookie_samesite

    response.set_cookie(
        "access_token",
        access_token,
        max_age=max_age_access,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
        path="/",
    )
    if refresh_token:
        response.set_cookie(
            "refresh_token",
            refresh_token,
            max_age=max_age_refresh,
            httponly=True,
            secure=secure,
            samesite=samesite,
            domain=domain,
            path="/api/v1/auth/refresh",  # 只在刷新时发送
        )


def clear_auth_cookies(response: Response) -> None:
    """清除认证 cookie。"""
    from app.core.config import settings
    domain = settings.cookie_domain or None
    response.delete_cookie("access_token", domain=domain, path="/")
    response.delete_cookie("refresh_token", domain=domain, path="/api/v1/auth/refresh")
```

- [ ] **Step 3: 修改 /login 返回 cookie**

修改 `backend/app/api/v1/auth.py` 的 `login` 函数：

```python
from fastapi.responses import JSONResponse
from app.utils.security import set_auth_cookies

@router.post("/login", ...)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    # ... 原有验证逻辑 ...

    access = create_access_token(...)
    refresh = create_refresh_token(str(account.id))

    response = JSONResponse(
        content={
            "access_token": access,  # 仍然返回，前端可作为过渡
            "refresh_token": refresh,
            "token_type": "bearer",
            "expires_in": settings.access_token_expire_minutes * 60,
        }
    )
    set_auth_cookies(response, access, refresh)
    return response
```

- [ ] **Step 4: 修改 /refresh 从 cookie 读取**

修改 `refresh` 函数，支持从 cookie 和 body 两种方式获取 refresh_token：

```python
class RefreshRequest(BaseModel):
    refresh_token: str | None = Field(None, description="刷新令牌")

@router.post("/refresh", ...)
async def refresh(
    body: RefreshRequest = None,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    # 优先从 cookie 获取，回退到 body
    refresh_token = None
    if request:
        refresh_token = request.cookies.get("refresh_token")
    if not refresh_token and body:
        refresh_token = body.refresh_token
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh token")

    # ... 原有验证和轮换逻辑 ...

    response = JSONResponse(content={...})
    set_auth_cookies(response, access, refresh)
    return response
```

- [ ] **Step 5: 修改 /logout 清除 cookie**

修改 `logout` 函数：

```python
@router.post("/logout")
async def logout(request: Request):
    from app.utils.security import clear_auth_cookies
    from fastapi.responses import JSONResponse

    # ... 原有 token 撤销逻辑 ...

    response = JSONResponse(content={"status": "ok"})
    clear_auth_cookies(response)
    return response
```

- [ ] **Step 6: 修改中间件优先从 cookie 读取 token**

修改 `backend/app/middleware/tenant.py` 的 `_authenticate_jwt`：

```python
async def _authenticate_jwt(self, request: Request, call_next):
    # 优先从 cookie 读取，回退到 Authorization header
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        return JSONResponse(status_code=401, content={"detail": "Missing or invalid token"})

    # ... 后续验证逻辑不变 ...
```

- [ ] **Step 7: 平台登录同样使用 cookie**

修改 `backend/app/api/v1/platform.py` 的 `platform_login`，使用独立的 cookie 键 `platform_access_token`：

```python
@router.post("/auth/login", ...)
async def platform_login(body: PlatformLoginRequest, request: Request):
    # ... 原有验证 ...

    response = JSONResponse(content={"access_token": token, "token_type": "bearer"})
    response.set_cookie(
        "platform_access_token",
        token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain or None,
        path="/",
    )
    return response
```

- [ ] **Step 8: 运行测试**

Run: `cd backend && python -m pytest tests/ -k "auth" -v --timeout=60`

- [ ] **Step 9: Commit**

```bash
git add backend/app/utils/security.py backend/app/api/v1/auth.py backend/app/api/v1/platform.py backend/app/middleware/tenant.py backend/app/core/config.py
git commit -m "feat(auth): HttpOnly cookie authentication with backward compatibility (H3)"
```

---

### Task 8: 前端 HttpOnly Cookie 适配 [H3, H8, M5, M6, M7, M10, L4]

**Files:**
- Modify: `frontend/apps/admin/src/lib/api.ts`
- Modify: `frontend/apps/admin/src/lib/auth.ts`
- Modify: `frontend/apps/admin/src/middleware.ts`
- Modify: `frontend/apps/platform/src/lib/platform-auth.ts`
- Modify: `frontend/apps/platform/src/lib/api.ts`
- Modify: `frontend/apps/platform/src/middleware.ts`

- [ ] **Step 1: Axios 使用 credentials: 'include'**

修改 `frontend/apps/admin/src/lib/api.ts`：

```typescript
const api = axios.create({
  baseURL: `${API_BASE}/api/v1`,
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
  withCredentials: true,  // 新增：发送 HttpOnly cookie
});
```

移除请求拦截器中的手动 Authorization header（cookie 自动携带），但保留作为回退：

```typescript
api.interceptors.request.use((config) => {
  // HttpOnly cookie 自动携带，不再需要手动设置
  // 但保留回退：如果没有 cookie，从 localStorage 读取
  if (typeof window !== "undefined") {
    const cookieToken = document.cookie.includes("access_token=");
    if (!cookieToken) {
      const token = localStorage.getItem("access_token");
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
    }
  }
  return config;
});
```

- [ ] **Step 2: 简化 auth store — 移除 localStorage token 操作**

修改 `frontend/apps/admin/src/lib/auth.ts`，逐步移除 localStorage token 存储：

```typescript
// _persistTokens 简化 — 只保留 localStorage 作为过渡期回退
function _persistTokens(accessToken: string, refreshToken: string, _expiresIn?: number) {
  // HttpOnly cookie 由后端 Set-Cookie 设置，前端不需要手动存储
  // 过渡期：保留 localStorage 供中间件读取
  localStorage.setItem("access_token", accessToken);
  localStorage.setItem("refresh_token", refreshToken);
}

// logout 只需清除 localStorage 和 Zustand 状态
// cookie 由后端 /logout 端点清除
logout: () => {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  localStorage.removeItem("auth_store");
  // cookie 由后端负责清除
  set({ user: null, token: null });
},
```

- [ ] **Step 3: 使用 /auth/me 获取用户信息替代 JWT 解码**

修改 `login` 函数，登录后从 API 获取用户信息而非解码 JWT：

```typescript
login: async (email, password, options) => {
  set({ loading: true });
  try {
    const { data } = await api.post("/auth/login", {
      email,
      password,
      ...(options?.tenantSlug ? { tenant_slug: options.tenantSlug } : {}),
    });
    // token 现在由 HttpOnly cookie 管理
    // 过渡期仍存储到 localStorage
    localStorage.setItem("access_token", data.access_token);
    localStorage.setItem("refresh_token", data.refresh_token);

    // 从 /auth/me 获取用户信息（更安全，无需解码 JWT）
    const { data: meData } = await api.get("/auth/me");
    const user: AuthUser = {
      account_id: meData.id,
      tenant_id: meData.tenant_id,
      role: meData.role,
      tenant_type: meData.tenant_type || "brand",
      email: meData.email,
      name: meData.name,
      acting_tenant_id: null,
      agency_scope: null,
    };
    localStorage.setItem("auth_store", JSON.stringify(user));
    set({ user, token: data.access_token, loading: false });
  } catch {
    set({ loading: false });
    throw new Error("登录失败，请检查邮箱和密码");
  }
},
```

- [ ] **Step 4: 修改 401 拦截器 — 使用 cookie 刷新**

`api.ts` 中的 `silentRefresh` 调用改为依赖 cookie：

```typescript
// refresh 请求使用 cookie 自动携带 refresh_token
const { data } = await api.post("/auth/refresh", {});  // body 为空，从 cookie 读取
```

- [ ] **Step 5: Platform auth 适配**

修改 `frontend/apps/platform/src/lib/platform-auth.ts`：

```typescript
login: async (email, password) => {
  set({ loading: true });
  try {
    const { data } = await api.post("/platform/auth/login", { email, password });
    // HttpOnly cookie 由后端设置
    // 过渡期保留 localStorage
    localStorage.setItem("platform_access_token", data.access_token);
    set({ token: data.access_token, loading: false });
  } catch {
    set({ loading: false });
    throw new Error("登录失败，请检查邮箱和密码");
  }
},
```

修改 `frontend/apps/platform/src/lib/api.ts`：

```typescript
const api = axios.create({
  baseURL: `${API_BASE}/api/v1`,
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
  withCredentials: true,  // 新增
});
```

- [ ] **Step 6: Platform 中间件增强**

修改 `frontend/apps/platform/src/middleware.ts`，添加 token 基本验证：

```typescript
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  const token = request.cookies.get("platform_access_token")?.value;

  if (!token) {
    // 回退到 localStorage 设置的 cookie（过渡期）
    const legacyToken = request.cookies.get("access_token")?.value;
    if (!legacyToken) {
      return NextResponse.redirect(new URL("/login", request.url));
    }
  }

  // 基本验证：检查 token 格式（JWT 有 3 段）
  if (token) {
    const parts = token.split(".");
    if (parts.length !== 3) {
      return NextResponse.redirect(new URL("/login", request.url));
    }
    // 检查过期（不验证签名，middleware 无法做 HMAC）
    try {
      const payload = JSON.parse(atob(parts[1]));
      if (payload.exp && payload.exp * 1000 < Date.now()) {
        return NextResponse.redirect(new URL("/login", request.url));
      }
    } catch {
      return NextResponse.redirect(new URL("/login", request.url));
    }
  }

  return NextResponse.next();
}
```

- [ ] **Step 7: Admin middleware 同样增强**

修改 `frontend/apps/admin/src/middleware.ts`，添加过期检查：

```typescript
// 在 JWT 解码后添加过期检查
try {
  const payload = JSON.parse(atob(token.split(".")[1]));
  // 过期检查
  if (payload.exp && payload.exp * 1000 < Date.now()) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  // ... 原有路由逻辑 ...
}
```

- [ ] **Step 8: 验证 Agency context URL**

检查 `frontend/apps/admin/src/lib/auth.ts:83` 中的 URL `/ops/authorizations/switch-context` 是否与后端路由一致。

后端路由在 `agency_auth.py:105` 定义为 `_switch_router = APIRouter(prefix="/api/v1/agency")`，路径为 `/switch-context`。

所以前端应调用 `/agency/switch-context` 而非 `/ops/authorizations/switch-context`。

修复 `auth.ts`：

```typescript
switchAgencyContext: async (clientTenantId: string) => {
  const { data } = await api.post("/agency/switch-context", { client_tenant_id: clientTenantId });
  // ... 后续逻辑不变
},

exitAgencyContext: async () => {
  const { data } = await api.post("/agency/exit-context");
  // ... 后续逻辑不变
},
```

- [ ] **Step 9: 运行前端构建验证**

Run: `cd frontend && pnpm build:shared && pnpm build:admin && pnpm build:platform`

- [ ] **Step 10: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): adapt to HttpOnly cookie auth, fix agency context URL, add token expiry check (H3, H8, M5, M7, M10)"
```

---

## Phase 4: 数据模型修复

### Task 9: 外键约束 + scope 类型 + email 唯一约束 [H5, H7, L2]

**Files:**
- Modify: `backend/app/models/tenant.py`
- Create: `backend/alembic/versions/xxx_auth_model_constraints.py`
- Create: `backend/scripts/verify_data_integrity.py`

- [ ] **Step 1: 创建数据完整性验证脚本**

Create `backend/scripts/verify_data_integrity.py`:

```python
"""验证数据完整性，用于在添加外键约束前检查。"""
import asyncio
import sys

from sqlalchemy import select, text
from app.core.database import async_session_factory
from app.models.tenant import Account, Role, Permission, Tenant


async def verify():
    async with async_session_factory() as db:
    issues = []

    # 检查 Account.tenant_id 是否都有对应 Tenant
    result = await db.execute(text("""
        SELECT a.id, a.tenant_id
        FROM accounts a
        LEFT JOIN tenants t ON a.tenant_id = t.id
        WHERE t.id IS NULL
    """))
    orphans = result.fetchall()
    if orphans:
        issues.append(f"发现 {len(orphans)} 个 Account 引用不存在的 Tenant")
        for row in orphans:
            issues.append(f"  Account {row[0]} → Tenant {row[1]}")

    # 检查 Role.tenant_id
    result = await db.execute(text("""
        SELECT r.id, r.tenant_id
        FROM roles r
        LEFT JOIN tenants t ON r.tenant_id = t.id
        WHERE t.id IS NULL
    """))
    orphans = result.fetchall()
    if orphans:
        issues.append(f"发现 {len(orphans)} 个 Role 引用不存在的 Tenant")

    # 检查 Permission.tenant_id
    result = await db.execute(text("""
        SELECT p.id, p.tenant_id
        FROM permissions p
        LEFT JOIN tenants t ON p.tenant_id = t.id
        WHERE t.id IS NULL
    """))
    orphans = result.fetchall()
    if orphans:
        issues.append(f"发现 {len(orphans)} 个 Permission 引用不存在的 Tenant")

    # 检查重复 email（同 tenant 内）
    result = await db.execute(text("""
        SELECT tenant_id, email, COUNT(*) as cnt
        FROM accounts
        GROUP BY tenant_id, email
        HAVING cnt > 1
    """))
    dupes = result.fetchall()
    if dupes:
        issues.append(f"发现 {len(dupes)} 组同租户内重复邮箱")
        for row in dupes:
            issues.append(f"  Tenant {row[0]}: {row[1]} ({row[2]} 次)")

    if issues:
        print("❌ 数据完整性问题：")
        for issue in issues:
            print(f"  {issue}")
        return 1
    else:
        print("✅ 数据完整性验证通过")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(verify()))
```

- [ ] **Step 2: 运行数据验证**

Run: `cd backend && python scripts/verify_data_integrity.py`
预期: 输出 "✅ 数据完整性验证通过" 或列出具体问题

- [ ] **Step 3: 修改模型添加外键和约束**

修改 `backend/app/models/tenant.py`：

```python
# Account: 添加 ForeignKey + email 唯一约束
class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        # 同租户内邮箱唯一
        {"extend_existing": True},
    )
    # 如果能通过 UniqueConstraint 更好：
    # from sqlalchemy import UniqueConstraint
    # __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_account_tenant_email"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True  # 添加 ForeignKey
    )
    # ... 其余字段不变 ...

# Role: 添加 ForeignKey
class Role(Base):
    __tablename__ = "roles"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True  # 添加 ForeignKey
    )
    # ... 其余字段不变 ...

# Permission: 添加 ForeignKey
class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True  # 添加 ForeignKey
    )
    # ... 其余字段不变 ...

# AgencyAuthorization: 修复 scope 类型
class AgencyAuthorization(Base):
    __tablename__ = "agency_authorizations"
    # ...
    scope: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # dict → list
    # ... 其余字段不变 ...
```

在文件顶部确保 `ForeignKey` 已从 sqlalchemy 导入（已有）。

- [ ] **Step 4: 生成 Alembic 迁移**

Run: `cd backend && alembic revision --autogenerate -m "add FK constraints, email unique, scope type fix"`

检查生成的迁移文件包含：
- `ALTER TABLE accounts ADD FOREIGN KEY (tenant_id) REFERENCES tenants(id)`
- `ALTER TABLE roles ADD FOREIGN KEY (tenant_id) REFERENCES tenants(id)`
- `ALTER TABLE permissions ADD FOREIGN KEY (tenant_id) REFERENCES tenants(id)`
- `CREATE UNIQUE INDEX uq_account_tenant_email ON accounts (tenant_id, email)`
- `ALTER TABLE agency_authorizations ALTER COLUMN scope TYPE ...`（如有类型变更）

- [ ] **Step 5: 运行迁移**

Run: `cd backend && alembic upgrade head`

- [ ] **Step 6: 运行全量测试**

Run: `cd backend && python -m pytest tests/ -v --timeout=60`

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/tenant.py backend/alembic/versions/ backend/scripts/verify_data_integrity.py
git commit -m "fix(models): add FK constraints, email unique index, fix scope type (H5, H7, L2)"
```

---

## Phase 5: 代码质量改进

### Task 10: 安全加固 — 时序攻击、密码重置令牌哈希、角色 fallback [M1, M2, M3, L3, L5]

**Files:**
- Modify: `backend/app/api/v1/auth.py`
- Modify: `backend/app/utils/rbac.py`（如已合并则为 `auth_rbac.py`）

- [ ] **Step 1: 修复登录时序攻击**

修改 `backend/app/api/v1/auth.py` 的 `login` 函数。核心改动：无论账户是否存在，都执行 `verify_password`：

```python
@router.post("/login", ...)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    # IP 速率限制 ... (已在 Task 4 添加)

    query = select(Account).options(selectinload(Account.roles)).where(Account.email == body.email)
    if body.tenant_slug:
        query = query.join(Tenant, Tenant.id == Account.tenant_id).where(Tenant.slug == body.tenant_slug)
    result = await db.execute(query)
    account = result.scalars().first()

    now = utcnow()

    # 时序攻击修复：无论账户是否存在都执行 bcrypt 验证
    if not account:
        # 用一个假 hash 保持相同时间
        verify_password("timing-resistant-dummy", "$2b$12$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(body.password, account.hashed_password):
        account.failed_login_attempts += 1
        if account.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
            account.locked_until = now + timedelta(minutes=LOCK_DURATION_MINUTES)
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if account.locked_until and account.locked_until > now:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # ... 成功登录逻辑不变 ...
```

- [ ] **Step 2: 密码重置令牌哈希存储**

修改 `generate_reset_token` 和 `confirm_reset_password`：

```python
import hashlib

def _hash_reset_token(token: str) -> str:
    """密码重置令牌的 SHA-256 哈希"""
    return hashlib.sha256(token.encode()).hexdigest()


# generate_reset_token 中：
    token = secrets.token_urlsafe(32)
    cache = AsyncRedisCache()
    await cache.set(
        f"{RESET_TOKEN_KEY_PREFIX}:{account_uuid}",
        {"token_hash": _hash_reset_token(token), "account_id": str(account_uuid)},
        ttl=RESET_TOKEN_TTL,
    )


# confirm_reset_password 中验证：
    record = await cache.get(f"{RESET_TOKEN_KEY_PREFIX}:{account_uuid}")
    if not record:
        raise HTTPException(status_code=400, detail="重置链接已过期或不存在")
    if record.get("token_hash") != _hash_reset_token(body.token):
        raise HTTPException(status_code=400, detail="重置令牌无效")
```

- [ ] **Step 3: 修复 _resolve_account_role fallback**

```python
def _resolve_account_role(account: Account) -> str:
    role_names = {role.name for role in account.roles}
    for role in ("platform_admin", "admin", "operator"):
        if role in role_names:
            return role
    # 安全 fallback：返回最低权限角色
    return "operator" if "operator" in role_names else "viewer"
```

- [ ] **Step 4: 添加密码重置审计日志**

在 `confirm_reset_password` 成功后添加：

```python
    await db.commit()

    # 审计日志
    try:
        from app.services.audit import write_audit_log
        await write_audit_log(
            db,
            operator_id=str(account_uuid),
            target_tenant_id=str(account.tenant_id) if account else "",
            action="password_reset",
            resource=f"account:{account_uuid}",
        )
        await db.commit()
    except Exception:
        pass
```

- [ ] **Step 5: require_role 模块级导入**

确保 `require_role` 函数（现在在 `auth_rbac.py` 中）使用模块级导入而非懒导入。

- [ ] **Step 6: 运行测试**

Run: `cd backend && python -m pytest tests/unit/test_auth_security.py -v`

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/v1/auth.py backend/app/utils/auth_rbac.py
git commit -m "fix(auth): timing attack mitigation, reset token hashing, safe role fallback, audit log (M1, M2, M3, L3, L5)"
```

---

### Task 11: 事务管理 + 错误消息统一 [M4, L1]

**Files:**
- Modify: `backend/app/api/v1/agency_auth.py`
- Modify: `backend/app/api/v1/roles.py`
- Modify: `backend/app/api/v1/auth.py`

- [ ] **Step 1: 统一事务管理模式**

决策：所有 API 路由使用 `db.flush()` + 依赖级 `db.commit()` 模式（推荐），不显式 `db.commit()`。

需要确认 `get_db` 依赖是否有自动 commit。检查 `backend/app/core/database.py` 的 `get_db` 函数，如果已经有 `finally: await db.commit()`，则移除路由中的显式 `db.commit()` 调用。

移除 `auth.py` 中的 `await db.commit()` 调用（约 3 处），改为 `await db.flush()`。
移除 `roles.py` 中的 `await db.commit()` 调用（约 3 处），改为 `await db.flush()`。
`agency_auth.py` 已经使用 `db.flush()`，无需改动。

- [ ] **Step 2: 统一错误消息为中文**

在 `backend/app/api/v1/auth.py` 中：
- `"Invalid credentials"` → `"邮箱或密码不正确"`
- `"Account not found"` → `"账户不存在"`
- `"Invalid refresh token"` → `"刷新令牌无效"`
- `"Missing permission: {permission}"` → `"缺少权限: {permission}"`

在 `backend/app/api/v1/roles.py` 中：
- `"Role name already exists"` → `"角色名称已存在"`
- `"Permission code already exists"` → `"权限代码已存在"`
- `"Role not found"` → `"角色不存在"`
- `"Permission not found"` → `"权限不存在"`
- `"Permission already assigned to this role"` → `"该角色已拥有此权限"`

- [ ] **Step 3: 运行测试**

Run: `cd backend && python -m pytest tests/ -v --timeout=60`

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/v1/auth.py backend/app/api/v1/roles.py backend/app/api/v1/agency_auth.py
git commit -m "refactor(auth): unify transaction management and error messages to Chinese (M4, L1)"
```

---

## 最终验证

### Task 12: 全量集成验证

- [ ] **Step 1: 后端全量测试**

Run: `cd backend && python -m pytest tests/ -v --timeout=120`

- [ ] **Step 2: 前端构建**

Run: `cd frontend && pnpm build:shared && pnpm build:admin && pnpm build:platform`

- [ ] **Step 3: 数据库迁移验证（空库）**

```bash
createdb yimatong_test_verify && psql -d yimatong_test_verify -c "GRANT ALL ON SCHEMA public TO yimatong"
DATABASE_URL="postgresql+asyncpg://yimatong:yimatong@localhost:5432/yimatong_test_verify" alembic upgrade head
dropdb yimatong_test_verify
```

- [ ] **Step 4: 手动冒烟测试**

1. 用现有账户登录 → 验证 HttpOnly cookie 设置
2. 等待 access_token 过期 → 验证自动刷新
3. 登出 → 验证 cookie 清除
4. 用 operator 角色尝试访问 /roles POST → 验证 403
5. 用 agency 账户切换上下文 → 验证正确路由
6. 平台管理员登录 → 验证速率限制和审计

- [ ] **Step 5: 更新文档**

更新 `docs/02_tech/ARCHITECTURE.md` 中认证架构部分，标注 `last_verified: 2026-06-07`。
更新 `CLAUDE.md` 中 `已知环境陷阱` 和 `前端认证流` 部分。

- [ ] **Step 6: Final Commit**

```bash
git add docs/ CLAUDE.md
git commit -m "docs: update auth architecture and CLAUDE.md after security hardening"
```

---

## 依赖关系

```
Phase 1 (Tasks 1-4)  ← 可并行，无依赖
    ↓
Phase 2 (Tasks 5-6)  ← 依赖 Phase 1 的权限加载
    ↓
Phase 3 (Tasks 7-8)  ← 依赖 Phase 2 的 RBAC 统一
    ↓
Phase 4 (Task 9)     ← 独立，可与 Phase 2 并行
    ↓
Phase 5 (Tasks 10-11) ← 依赖前面所有 Phase
    ↓
Task 12 (验证)        ← 最终验证
```
