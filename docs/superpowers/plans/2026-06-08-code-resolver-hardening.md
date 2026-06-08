# 码解析模块安全加固 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复码解析模块 13 项安全/质量问题，使 scan_token 签发、验证、缓存、限流达到生产级标准。

**Architecture:** 按依赖关系分层修复：先统一底层工具（client_ip、scan_token），再修路由层（resolver、consumers 等），最后补缓存失效和测试。每个 Task 产出可独立验证的变更。

**Tech Stack:** Python 3.12, FastAPI, PyJWT, SQLAlchemy 2.0 async, Redis, pytest-anyio

---

## 已做决策

| 决策 | 选择 | 理由 |
|------|------|------|
| scan_token TTL | 30 分钟（保持代码不变） | 给微信 OAuth 回调留时间，修正注释 |
| IP 获取策略 | 信任 X-Real-IP | Nginx 层设置 real_ip，最简单安全 |
| expired 码处理 | 返回提示页 + 不颁发 token | 过期码不应授权后续操作 |
| scan_token 含 tenant_id | 是 | 减少消费端 DB 查询 |

---

## 文件结构

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `backend/app/services/scan_token.py` | TTL 注释修正 + payload 增加 tenant_id |
| 修改 | `backend/app/utils/client_ip.py` | X-Real-IP 优先策略 |
| 修改 | `backend/app/api/v1/resolver.py` | expired 拦截 + IP hash 统一 + 传 tenant_id |
| 修改 | `backend/app/services/page_templates.py` | 新增 EXPIRED_PAGE 模板 |
| 修改 | `backend/app/api/v1/consumers.py` | 统一 verify_scan_token + get_client_ip |
| 修改 | `backend/app/api/v1/benefit_claims.py` | 统一 verify_scan_token + 修复限流 |
| 修改 | `backend/app/api/v1/consents.py` | 修复 tenant_id bug + 统一 get_client_ip |
| 修改 | `backend/app/services/code.py` | 状态变更后清除缓存 |
| 修改 | `backend/app/services/resolver.py` | data dict 含 production_batch_id |
| 修改 | `backend/app/services/resolver_response.py` | 移除重复 CodeBatch 查询 + import 提到顶部 |
| 修改 | `backend/tests/test_services/test_scan_token.py` | 增加测试覆盖 |
| 新建 | `backend/tests/test_api/test_resolver_edge_cases.py` | 限流/缓存/过期码测试 |

---

## Task 1: 修复 scan_token 服务（TTL 注释 + tenant_id）

**Files:**
- Modify: `backend/app/services/scan_token.py`
- Modify: `backend/tests/test_services/test_scan_token.py`

- [ ] **Step 1: 写失败测试 — tenant_id 在 payload 中**

在 `backend/tests/test_services/test_scan_token.py` 的 `TestScanToken` 类中添加：

```python
def test_create_with_tenant_id(self):
    token = create_scan_token(
        public_id="ABC123",
        ip_hash="abc123hash",
        tenant_id="tenant-001",
    )
    result = verify_scan_token(token, "ABC123")
    assert result is not None
    assert result["tenant_id"] == "tenant-001"

def test_verify_with_expected_tenant_id(self):
    token = create_scan_token(
        public_id="ABC123",
        ip_hash="abc123hash",
        tenant_id="tenant-001",
    )
    # 匹配
    result = verify_scan_token(token, "ABC123", expected_tenant_id="tenant-001")
    assert result is not None
    # 不匹配
    result = verify_scan_token(token, "ABC123", expected_tenant_id="tenant-999")
    assert result is None

def test_default_ttl_is_1800(self):
    token = create_scan_token(public_id="ABC123", ip_hash="hash")
    result = verify_scan_token(token, "ABC123")
    assert result["exp"] - int(time.time()) <= 1800
    assert result["exp"] - int(time.time()) > 1700
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_services/test_scan_token.py::TestScanToken::test_create_with_tenant_id -v`

Expected: FAIL — `create_scan_token() got an unexpected keyword argument 'tenant_id'`

- [ ] **Step 3: 修改 scan_token.py — 修正注释 + 添加 tenant_id**

修改 `backend/app/services/scan_token.py`，完整替换为：

```python
"""scan_token 防伪机制"""

import time

import jwt

from app.core.config import settings


def create_scan_token(
    public_id: str,
    ip_hash: str | None,
    tenant_id: str = "",
    expires_in: int = 1800,
) -> str:
    """颁发 scan_token（短期 JWT，默认 30 分钟）。

    Args:
        public_id: 码的公开标识
        ip_hash: 客户端 IP 的 SHA256 哈希，None 表示无法获取
        tenant_id: 租户 ID（减少消费端查询）
        expires_in: 有效期秒数，默认 1800（30 分钟）
    """
    payload = {
        "public_id": public_id,
        "ip_hash": ip_hash,
        "tenant_id": tenant_id,
        "exp": int(time.time()) + expires_in,
        "type": "scan_token",
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def verify_scan_token(
    token: str,
    expected_public_id: str | None = None,
    expected_ip_hash: str | None = None,
    expected_tenant_id: str | None = None,
) -> dict | None:
    """验证 scan_token。

    Args:
        token: JWT 字符串
        expected_public_id: 可选，验证码标识匹配
        expected_ip_hash: 可选，验证 IP 哈希匹配（None 跳过验证）
        expected_tenant_id: 可选，验证租户 ID 匹配
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.exceptions.DecodeError:
        return None
    except jwt.exceptions.ExpiredSignatureError:
        return None

    if payload.get("type") != "scan_token":
        return None

    if expected_public_id and payload.get("public_id") != expected_public_id:
        return None

    if expected_ip_hash is not None and payload.get("ip_hash") != expected_ip_hash:
        return None

    if expected_tenant_id is not None and payload.get("tenant_id") != expected_tenant_id:
        return None

    return payload
```

关键变更：
- 注释从"默认 5 分钟"改为"默认 30 分钟"
- `create_scan_token` 新增 `tenant_id` 参数
- `ip_hash` 类型从 `str` 改为 `str | None`
- `verify_scan_token` 新增 `expected_tenant_id` 参数
- IP hash 验证改为 `if expected_ip_hash is not None`（不再依赖 falsy 跳过）

- [ ] **Step 4: 运行全部 scan_token 测试确认通过**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_services/test_scan_token.py -v`

Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scan_token.py backend/tests/test_services/test_scan_token.py
git commit -m "fix(scan-token): correct TTL comment, add tenant_id to payload, fix IP hash None handling"
```

---

## Task 2: 修复 client_ip 工具（X-Real-IP 优先）

**Files:**
- Modify: `backend/app/utils/client_ip.py`
- Modify: `backend/tests/test_services/test_public_id.py`（无需改，验证现有测试通过）

- [ ] **Step 1: 修改 client_ip.py**

修改 `backend/app/utils/client_ip.py`，完整替换为：

```python
"""客户端 IP 提取工具（支持反向代理）

优先级：X-Real-IP > X-Forwarded-For 首个 > request.client.host

注意：生产环境需在 Nginx 配置 real_ip 模块：
  set_real_ip_from 10.0.0.0/8;
  real_ip_header X-Forwarded-For;
"""

from fastapi import Request


def get_client_ip(request: Request) -> str:
    """获取真实客户端 IP。

    优先使用 Nginx 设置的 X-Real-IP（不可被客户端伪造），
    回退到 X-Forwarded-For 第一个 IP，最后回退到直连 IP。
    """
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()

    return request.client.host if request.client else "unknown"
```

- [ ] **Step 2: 运行 resolver 相关测试确认无回归**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_code_resolve_public.py tests/test_api/test_resolver_json_response.py -v`

Expected: 全部 PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/utils/client_ip.py
git commit -m "fix(client-ip): prefer X-Real-IP over X-Forwarded-For to prevent spoofing"
```

---

## Task 3: 修复 resolver 路由（expired 拦截 + IP hash 统一 + tenant_id 传递）

**Files:**
- Modify: `backend/app/api/v1/resolver.py`
- Modify: `backend/app/services/page_templates.py`

- [ ] **Step 1: 新增 EXPIRED_PAGE 模板**

在 `backend/app/services/page_templates.py` 的 `RISK_FROZEN_PAGE` 之后添加：

```python
EXPIRED_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>码已过期</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px 16px;">
<h2>该二维码已过期</h2><p>产品保质期已过，如有疑问请联系客服</p>
</body></html>"""
```

- [ ] **Step 2: 修改 resolver.py**

修改 `backend/app/api/v1/resolver.py`，替换为：

```python
"""码解析公开路由"""

import hashlib
import logging
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.middleware.rate_limit import rate_limiter
from app.models.code import CodeItemStatus, CodeType
from app.models.scan import ScanEvent
from app.services.page_render import render_page
from app.services.page_templates import (
    EXPIRED_PAGE,
    INNER_VERIFY_PAGE,
    NOT_ACTIVE_PAGE,
    NOT_FOUND_PAGE,
    OUTER_LANDING_PAGE,
    REVOKED_PAGE,
    RISK_FROZEN_PAGE,
    build_code_page,
)
from app.services.public_id import validate_public_id
from app.services.resolve_cache import resolve_cache
from app.services.resolver import resolve_public_code
from app.services.resolver_response import build_json_response
from app.services.scan_event import parse_environment, record_scan_event
from app.services.scan_token import create_scan_token
from app.utils.client_ip import get_client_ip

logger = logging.getLogger(__name__)

resolver_router = APIRouter(tags=["resolver"])

# 不允许扫码的状态（返回提示页，不颁发 token）
_BLOCKED_STATUSES = frozenset({
    CodeItemStatus.revoked,
    CodeItemStatus.frozen,
    CodeItemStatus.created,
    CodeItemStatus.expired,
})


@resolver_router.get("/c/{public_id}", summary="解析码")
async def resolve_code_endpoint(
    public_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    accept = request.headers.get("accept", "")
    want_json = "application/json" in accept

    # 1. 限流 + 格式校验
    client_ip = get_client_ip(request)
    rate_result = await rate_limiter.check_resolver(client_ip, public_id)
    if not rate_result.allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests"},
            headers={"Retry-After": str(rate_result.retry_after)},
        )

    if not validate_public_id(public_id):
        return _not_found(want_json)

    # 2. 缓存查询 -> DB 回退
    cached = await resolve_cache.get(f"resolve:{public_id}")
    data = cached or await resolve_public_code(db, public_id)
    if data and not cached:
        await resolve_cache.set(f"resolve:{public_id}", data)

    if not data:
        return _not_found(want_json)

    status = data["status"]

    # 3. 阻断状态（revoked/frozen/created/expired）
    if status in _BLOCKED_STATUSES:
        return _error_status(status, public_id, want_json)

    # 4. 计算 IP hash（一次，复用）
    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest() if client_ip != "unknown" else None

    # 5. 记录扫码事件
    user_agent = request.headers.get("user-agent", "")
    scan_info = await _record_scan(db, data, public_id, ip_hash, user_agent, status)

    # 6. 生成 scan_token（含 tenant_id）
    scan_token = create_scan_token(
        public_id=public_id,
        ip_hash=ip_hash,
        tenant_id=data["tenant_id"],
    )

    # 7. JSON 模式
    if want_json:
        resp = await build_json_response(db, data, scan_token, scan_info)
        return JSONResponse(content=resp)

    # 8. HTML 模式
    return await _html_response(db, data, public_id)


# -- 内部辅助函数 --


def _not_found(want_json: bool):
    if want_json:
        return JSONResponse(status_code=404, content={"detail": "not_found"})
    return HTMLResponse(content=NOT_FOUND_PAGE, status_code=404)


def _error_status(status: str, public_id: str, want_json: bool):
    if status == CodeItemStatus.revoked:
        if want_json:
            return JSONResponse(status_code=410, content={"code_data": {"status": "revoked", "public_id": public_id}})
        return HTMLResponse(content=REVOKED_PAGE, status_code=410)

    if status == CodeItemStatus.frozen:
        if want_json:
            return JSONResponse(status_code=403, content={"code_data": {"status": "frozen", "public_id": public_id}})
        return HTMLResponse(content=RISK_FROZEN_PAGE, status_code=403)

    if status == CodeItemStatus.expired:
        if want_json:
            return JSONResponse(status_code=410, content={"code_data": {"status": "expired", "public_id": public_id}})
        return HTMLResponse(content=EXPIRED_PAGE, status_code=410)

    # CodeItemStatus.created
    if want_json:
        return JSONResponse(content={"code_data": {"status": "not_active", "public_id": public_id}})
    return HTMLResponse(content=NOT_ACTIVE_PAGE, status_code=200)


async def _record_scan(
    db: AsyncSession,
    data: dict,
    public_id: str,
    ip_hash: str | None,
    user_agent: str,
    status: str,
) -> dict:
    scan_info: dict = {"is_first_scan": False, "scan_count": 0}
    if status != CodeItemStatus.activated:
        return scan_info

    try:
        count_before = await db.execute(
            select(func.count()).select_from(ScanEvent).where(ScanEvent.public_id == public_id)
        )
        scan_info["scan_count"] = count_before.scalar() or 0
        event = await record_scan_event(
            db=db,
            tenant_id=uuid.UUID(data["tenant_id"]),
            public_id=public_id,
            ip_hash=ip_hash,
            user_agent=user_agent,
            environment=parse_environment(user_agent),
        )
        scan_info["is_first_scan"] = event.is_first_scan
    except Exception:
        logger.exception("Failed to record scan event for public_id=%s", public_id)
    return scan_info


async def _html_response(db: AsyncSession, data: dict, public_id: str):
    code_type = data.get("code_type", CodeType.single)

    if code_type == CodeType.outer:
        return HTMLResponse(content=OUTER_LANDING_PAGE.format(public_id=public_id))

    if code_type == CodeType.inner:
        template_id = data.get("template_id")
        tenant_id = data.get("tenant_id")
        if template_id and tenant_id:
            html = await render_page(db, uuid.UUID(tenant_id), uuid.UUID(template_id))
            if html:
                return HTMLResponse(content=html)
        return HTMLResponse(content=INNER_VERIFY_PAGE.format(public_id=public_id))

    # single 类型：尝试渲染页面模板，否则降级
    template_id = data.get("template_id")
    tenant_id = data.get("tenant_id")
    if template_id and tenant_id:
        html = await render_page(db, uuid.UUID(tenant_id), uuid.UUID(template_id))
        if html:
            return HTMLResponse(content=html)

    return HTMLResponse(content=build_code_page(data))
```

关键变更：
- 新增 `EXPIRED_PAGE` import
- `_BLOCKED_STATUSES` 集合包含 `expired`
- IP hash 计算统一为一次（`_record_scan` 接收 `ip_hash` 而非 `client_ip`）
- `ip_hash` 用 `None`（非空字符串）表示无效 IP
- `create_scan_token` 传入 `tenant_id=data["tenant_id"]`
- `_error_status` 新增 `expired` 分支（返回 410）

- [ ] **Step 3: 运行 resolver 测试确认通过**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_code_resolve_public.py tests/test_api/test_resolver_json_response.py tests/test_integration/test_e2e_scan_flow.py tests/test_integration/test_core_scan_closed_loop.py -v`

Expected: 全部 PASS（无回归）

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/v1/resolver.py backend/app/services/page_templates.py
git commit -m "fix(resolver): block expired codes, unify IP hash to None, pass tenant_id to scan_token"
```

---

## Task 4: 统一 consumers.py 的 token 验证 + IP 获取

**Files:**
- Modify: `backend/app/api/v1/consumers.py`

- [ ] **Step 1: 修改 consumers.py**

核心变更：
1. 删除 `_resolve_scan_tenant` 中手动 JWT 解码，改用 `verify_scan_token`
2. `lead_capture` 中用 `get_client_ip` 替代手动 XFF 解析
3. `lead_capture` 用 `verify_scan_token` 替代手动验证

修改 `backend/app/api/v1/consumers.py` 的以下部分：

将 `_resolve_scan_tenant` 函数替换为：

```python
async def _resolve_scan_tenant(request: Request, db: AsyncSession) -> uuid.UUID:
    token = _extract_bearer_token(request)
    payload = verify_scan_token(token)
    if not payload or not payload.get("public_id"):
        raise HTTPException(status_code=401, detail="invalid token")

    # 优先从 token payload 获取 tenant_id（减少 DB 查询）
    tid = payload.get("tenant_id")
    if tid:
        return uuid.UUID(tid)

    from app.services.resolver import resolve_public_code

    code_data = await resolve_public_code(db, payload["public_id"])
    if not code_data:
        raise HTTPException(status_code=404, detail="code not found")
    return uuid.UUID(code_data["tenant_id"])
```

将 `lead_capture` 函数中的手动 IP 获取和验证替换（约 line 66-76）：

```python
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")

    token = auth_header[7:]
    client_ip = get_client_ip(request)
    import hashlib
    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest() if client_ip != "unknown" else None
    payload = verify_scan_token(token, body.public_id, expected_ip_hash=ip_hash)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid_token")
```

同时添加 import：

```python
from app.utils.client_ip import get_client_ip
```

- [ ] **Step 2: 运行相关测试**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_integration/test_core_scan_closed_loop.py -v -k "claim"`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/v1/consumers.py
git commit -m "refactor(consumers): use shared verify_scan_token and get_client_ip"
```

---

## Task 5: 统一 benefit_claims.py 的 token 验证 + 修复限流

**Files:**
- Modify: `backend/app/api/v1/benefit_claims.py`

- [ ] **Step 1: 修改 benefit_claims.py**

将 line 26-56 的手动 JWT 解码和限流替换为：

```python
    # 0. IP 级速率限制
    client_ip = get_client_ip(request)
    ip_rate, _ = await rate_limiter._cache.rate_limit_check(
        f"claim:{client_ip}", 20, 60,
    )
    if not ip_rate:
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后再试",
            headers={"Retry-After": "60"},
        )

    # 1. 验证 scan_token
    auth_header = request.headers.get("Authorization", "")
    token = body.scan_token
    if not token and auth_header.startswith("Bearer "):
        token = auth_header[7:]

    if not token:
        raise HTTPException(status_code=401, detail="scan_token required")

    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest() if client_ip != "unknown" else None
    payload = verify_scan_token(token, expected_ip_hash=ip_hash)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid token")
```

同时更新 imports：

```python
import hashlib

from app.middleware.rate_limit import rate_limiter
from app.services.scan_token import verify_scan_token
from app.utils.client_ip import get_client_ip
```

并删除 `import jwt` 和 `from app.core.config import settings`（不再直接使用）。

- [ ] **Step 2: 运行权益领取测试**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_integration/test_core_scan_closed_loop.py::TestBenefitClaimClosedLoop -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/v1/benefit_claims.py
git commit -m "refactor(benefit-claims): use verify_scan_token, fix rate limiter, use get_client_ip"
```

---

## Task 6: 修复 consents.py（tenant_id bug + 统一工具）

**Files:**
- Modify: `backend/app/api/v1/consents.py`

- [ ] **Step 1: 修改 consents.py**

核心修复：
1. `tenant_id = payload.get("tenant_id")` 之前永远返回 `None`（token 没有此字段），现在 Task 1 已添加
2. 将手动 XFF 解析替换为 `get_client_ip`

所有手动 IP 获取行（约 line 34 和 80）：
```python
# 旧
client_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown").split(",")[0].strip()
# 新
client_ip = get_client_ip(request)
```

添加 import：
```python
from app.utils.client_ip import get_client_ip
```

- [ ] **Step 2: 运行 consents 测试**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/ -k "consent" -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/v1/consents.py
git commit -m "fix(consents): tenant_id now available from scan_token, use shared get_client_ip"
```

---

## Task 7: 优化 resolver 数据流（消除重复查询 + import 顶部化）

**Files:**
- Modify: `backend/app/services/resolver.py`
- Modify: `backend/app/services/resolver_response.py`

- [ ] **Step 1: 修改 resolver.py — data dict 含 production_batch_id**

在 `backend/app/services/resolver.py` 的 `resolve_public_code` 函数中，batch 数据段末尾添加：

```python
    batch = item.code_batch
    if batch:
        data["product_id"] = str(batch.product_id)
        data["sku_id"] = str(batch.sku_id)
        data["production_batch_id"] = str(batch.production_batch_id) if batch.production_batch_id else None

        # 查找产品关联的页面模板
        tmpl_result = await db.execute(
            select(PageTemplate)
            .where(
                PageTemplate.tenant_id == item.tenant_id,
                PageTemplate.product_id == batch.product_id,
                PageTemplate.status == "active",
            )
            .limit(1)
        )
        template = tmpl_result.scalar_one_or_none()
        if template:
            data["template_id"] = str(template.id)
```

- [ ] **Step 2: 修改 resolver_response.py — 消除重复查询 + import 提到顶部**

将 `backend/app/services/resolver_response.py` 完整替换为：

```python
"""构建 H5 前端所需的 JSON 响应（从 resolver 模块提取）"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.code import CodeBatch
from app.models.campaign import Benefit, Campaign, CampaignStatus
from app.models.page import PageVersion, PageVersionStatus
from app.models.product import Brand, ProductionBatch, Product
from app.services.redis_cache import AsyncRedisCache

_product_cache = AsyncRedisCache(prefix="product", default_ttl=600)
_page_config_cache = AsyncRedisCache(prefix="pagecfg", default_ttl=600)


async def build_json_response(
    db: AsyncSession,
    data: dict,
    scan_token: str,
    scan_info: dict,
) -> dict:
    """构建 H5 前端所需的 JSON 响应

    查询品牌、页面配置、生产批次溯源、活动权益等信息。
    """
    uuid.UUID(data["tenant_id"])
    product_id = data.get("product_id")

    result: dict = {
        "scan_token": scan_token,
        "code_data": {
            "public_id": data["public_id"],
            "status": data["status"],
            "code_type": data.get("code_type", "single"),
        },
        "scan_info": scan_info,
    }

    # 查询品牌信息（缓存优先）
    if product_id:
        cached_pb = await _product_cache.get(f"pb:{product_id}")
        if cached_pb:
            result["code_data"]["product"] = cached_pb["product"]
            if cached_pb.get("brand"):
                result["brand"] = cached_pb["brand"]
                result["tenant_branding"] = cached_pb["brand"]
        else:
            prod_result = await db.execute(
                select(Product, Brand)
                .join(Brand, Product.brand_id == Brand.id)
                .where(Product.id == uuid.UUID(product_id))
            )
            row = prod_result.one_or_none()
            if row:
                product, brand = row
                product_data = {
                    "name": product.name,
                    "description": product.description,
                    "image_url": product.image_url or "",
                    "origin": product.origin or "",
                }
                brand_data = {"name": brand.name, "logo_url": brand.logo_url or ""}
                await _product_cache.set(f"pb:{product_id}", {
                    "product": product_data,
                    "brand": brand_data,
                })
                result["code_data"]["product"] = product_data
                result["brand"] = brand_data
                result["tenant_branding"] = brand_data

    # 查询页面配置（缓存优先）
    template_id = data.get("template_id")
    if template_id:
        cached_config = await _page_config_cache.get(f"pv:{template_id}")
        if cached_config:
            result["page_config"] = cached_config
        else:
            ver_result = await db.execute(
                select(PageVersion)
                .where(
                    PageVersion.page_template_id == uuid.UUID(template_id),
                    PageVersion.status == PageVersionStatus.published,
                )
                .limit(1)
            )
            version = ver_result.scalar_one_or_none()
            if version:
                await _page_config_cache.set(f"pv:{template_id}", version.config_json)
                result["page_config"] = version.config_json

    # 溯源信息：使用 resolver 已传递的 production_batch_id，避免重复查 CodeBatch
    production_batch_id = data.get("production_batch_id")
    if production_batch_id:
        pb_result = await db.execute(
            select(ProductionBatch).where(
                ProductionBatch.id == uuid.UUID(production_batch_id)
            )
        )
        prod_batch = pb_result.scalar_one_or_none()
        if prod_batch:
            result["batch"] = {
                "batch_code": prod_batch.batch_code,
                "production_date": str(prod_batch.production_date),
                "expiry_date": str(prod_batch.expiry_date),
                "origin": prod_batch.origin or "",
            }

    # 查询当前产品可用活动
    if product_id:
        campaign_result = await db.execute(
            select(Campaign)
            .where(
                Campaign.product_id == uuid.UUID(product_id),
                Campaign.status == CampaignStatus.ACTIVE,
            )
            .order_by(Campaign.id.desc())
            .limit(1)
        )
        campaign = campaign_result.scalar_one_or_none()
        if campaign:
            benefit_result = await db.execute(
                select(Benefit)
                .where(Benefit.campaign_id == campaign.id, Benefit.tenant_id == campaign.tenant_id)
                .order_by(Benefit.id.desc())
                .limit(1)
            )
            benefit = benefit_result.scalar_one_or_none()
            result["campaign"] = {
                "id": str(campaign.id),
                "name": campaign.name,
                "rules": campaign.rules_json,
                "benefit": {
                    "id": str(benefit.id),
                    "name": benefit.name,
                    "benefit_type": benefit.benefit_type,
                    "config_json": benefit.config_json,
                    "description": benefit.config_json.get("description") if isinstance(benefit.config_json, dict) else None,
                }
                if benefit
                else None,
            }

    return result
```

关键变更：
- 所有 import 移到模块顶部
- 溯源数据段直接用 `data["production_batch_id"]`，不再重复查 `CodeBatch`
- `benefit.config_json.get("description")` 加了 `isinstance` 防御

- [ ] **Step 3: 运行全部 resolver 相关测试**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_code_resolve_public.py tests/test_api/test_resolver_json_response.py tests/test_integration/test_core_scan_closed_loop.py::TestJsonModeResolution -v`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/resolver.py backend/app/services/resolver_response.py
git commit -m "refactor(resolver): eliminate duplicate CodeBatch query, move imports to top, add isinstance guard"
```

---

## Task 8: 添加解析缓存主动失效

**Files:**
- Modify: `backend/app/services/code.py`

- [ ] **Step 1: 在 revoke_code_item 中添加缓存失效**

在 `backend/app/services/code.py` 的 `revoke_code_item` 函数中，`await db.flush()` 之后添加：

```python
    # 清除解析缓存，确保下次扫码立即看到 revoked 状态
    from app.services.resolve_cache import resolve_cache
    await resolve_cache.invalidate(f"resolve:{item.public_id}")
```

- [ ] **Step 2: 在 freeze_batch 中添加批量缓存失效**

在 `freeze_batch` 函数中，`await db.flush()` 之后添加：

```python
    # 批量清除被冻结码的解析缓存
    from app.services.resolve_cache import resolve_cache
    affected = await db.execute(
        select(CodeItem.public_id)
        .where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id == batch_id,
            CodeItem.status == CodeItemStatus.frozen,
        )
    )
    for (pid,) in affected.all():
        await resolve_cache.invalidate(f"resolve:{pid}")
```

- [ ] **Step 3: 在 void_batch 中添加批量缓存失效**

在 `void_batch` 函数中，`await db.flush()` 之后添加同样逻辑：

```python
    # 批量清除被作废码的解析缓存
    from app.services.resolve_cache import resolve_cache
    affected = await db.execute(
        select(CodeItem.public_id)
        .where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id == batch_id,
            CodeItem.status == CodeItemStatus.revoked,
        )
    )
    for (pid,) in affected.all():
        await resolve_cache.invalidate(f"resolve:{pid}")
```

- [ ] **Step 4: 运行全部测试确认无回归**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/ -v --timeout=60`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/code.py
git commit -m "fix(cache): invalidate resolve cache on code revoke/freeze/void"
```

---

## Task 9: 补全测试覆盖

**Files:**
- New: `backend/tests/test_api/test_resolver_edge_cases.py`
- Modify: `backend/tests/test_services/test_scan_token.py`

- [ ] **Step 1: 新建 edge case 测试文件**

创建 `backend/tests/test_api/test_resolver_edge_cases.py`：

```python
"""码解析模块边缘场景测试：限流、缓存失效、expired 码"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def expired_code(client: AsyncClient, db_session: AsyncSession):
    """创建一个 expired 状态的码"""
    from app.models.code import CodeItem, CodeItemStatus

    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "过期测试",
            "admin_email": "expired@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "过期品牌"}, headers=headers)
    prod = await client.post(
        "/api/v1/products",
        json={"brand_id": brand.json()["id"], "name": "过期产品"},
        headers=headers,
    )
    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": prod.json()["id"], "code": "EXP-SKU", "name": "EXP SKU"},
        headers=headers,
    )
    batch = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": prod.json()["id"],
            "sku_id": sku.json()["id"],
            "batch_code": "EXP-001",
            "quantity": 1,
        },
        headers=headers,
    )
    batch_id = batch.json()["id"]
    await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)

    items = await client.get(
        f"/api/v1/code-items?code_batch_id={batch_id}",
        headers=headers,
    )
    public_id = items.json()["items"][0]["public_id"]
    item_id = items.json()["items"][0]["id"]

    # 设置为 expired
    from sqlalchemy import update as db_update
    import uuid

    await db_session.execute(
        db_update(CodeItem)
        .where(CodeItem.id == uuid.UUID(item_id))
        .values(status=CodeItemStatus.expired)
    )
    await db_session.commit()

    return public_id


class TestExpiredCode:
    """expired 状态码不应颁发 scan_token"""

    @pytest.mark.anyio
    async def test_expired_code_json_returns_410(self, client: AsyncClient, expired_code: str):
        resp = await client.get(
            f"/c/{expired_code}",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 410, f"过期码 JSON 应返回 410，实际 {resp.status_code}"
        data = resp.json()
        assert data["code_data"]["status"] == "expired"

    @pytest.mark.anyio
    async def test_expired_code_html_returns_410(self, client: AsyncClient, expired_code: str):
        resp = await client.get(f"/c/{expired_code}")
        assert resp.status_code == 410, f"过期码 HTML 应返回 410，实际 {resp.status_code}"
        assert "过期" in resp.text

    @pytest.mark.anyio
    async def test_expired_code_no_scan_token(self, client: AsyncClient, expired_code: str):
        """过期码响应不应包含 scan_token"""
        resp = await client.get(
            f"/c/{expired_code}",
            headers={"Accept": "application/json"},
        )
        data = resp.json()
        assert "scan_token" not in data, "过期码不应颁发 scan_token"


class TestScanTokenTenantId:
    """验证 scan_token 包含 tenant_id"""

    @pytest.mark.anyio
    async def test_scan_token_contains_tenant_id(self, client: AsyncClient):
        """通过完整链路验证 scan_token 包含 tenant_id"""
        from app.services.scan_token import verify_scan_token

        # 创建完整链路并扫码
        resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "TenantID测试",
                "admin_email": "tid@test.com",
                "admin_name": "Admin",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        tid = resp.json()["id"]
        token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
        headers = {"Authorization": f"Bearer {token}"}

        brand = await client.post("/api/v1/brands", json={"name": "TID品牌"}, headers=headers)
        prod = await client.post(
            "/api/v1/products",
            json={"brand_id": brand.json()["id"], "name": "TID产品"},
            headers=headers,
        )
        sku = await client.post(
            "/api/v1/skus",
            json={"product_id": prod.json()["id"], "code": "TID-SKU", "name": "TID SKU"},
            headers=headers,
        )
        batch = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": prod.json()["id"],
                "sku_id": sku.json()["id"],
                "batch_code": "TID-001",
                "quantity": 1,
            },
            headers=headers,
        )
        await client.post(f"/api/v1/code-batches/{batch.json()['id']}/activate", headers=headers)
        items = await client.get(
            f"/api/v1/code-items?code_batch_id={batch.json()['id']}",
            headers=headers,
        )
        public_id = items.json()["items"][0]["public_id"]

        # 扫码获取 scan_token
        scan_resp = await client.get(
            f"/c/{public_id}",
            headers={"Accept": "application/json"},
        )
        assert scan_resp.status_code == 200
        scan_token = scan_resp.json()["scan_token"]

        # 验证 token 包含 tenant_id
        payload = verify_scan_token(scan_token, public_id)
        assert payload is not None
        assert payload["tenant_id"] == tid, f"tenant_id 应为 {tid}，实际 {payload.get('tenant_id')}"


class TestClientIpExtraction:
    """验证 IP 提取逻辑"""

    def test_x_real_ip_takes_priority(self):
        """X-Real-IP 应优先于 X-Forwarded-For"""
        from unittest.mock import MagicMock
        from app.utils.client_ip import get_client_ip

        request = MagicMock()
        request.headers = {"X-Real-IP": "1.2.3.4", "X-Forwarded-For": "5.6.7.8, 9.10.11.12"}
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        assert get_client_ip(request) == "1.2.3.4"

    def test_xff_fallback(self):
        """无 X-Real-IP 时回退到 XFF 首个"""
        from unittest.mock import MagicMock
        from app.utils.client_ip import get_client_ip

        request = MagicMock()
        request.headers = {"X-Forwarded-For": "5.6.7.8, 9.10.11.12"}
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        assert get_client_ip(request) == "5.6.7.8"

    def test_direct_connection_fallback(self):
        """无代理头时回退到直连 IP"""
        from unittest.mock import MagicMock
        from app.utils.client_ip import get_client_ip

        request = MagicMock()
        request.headers = {}
        request.client = MagicMock()
        request.client.host = "192.168.1.1"

        assert get_client_ip(request) == "192.168.1.1"

    def test_no_client_returns_unknown(self):
        """无 client 对象时返回 unknown"""
        from unittest.mock import MagicMock
        from app.utils.client_ip import get_client_ip

        request = MagicMock()
        request.headers = {}
        request.client = None

        assert get_client_ip(request) == "unknown"
```

- [ ] **Step 2: 运行新测试**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_resolver_edge_cases.py -v`

Expected: 全部 PASS

- [ ] **Step 3: 运行全量测试确认无回归**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/ -v --timeout=120`

Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_api/test_resolver_edge_cases.py
git commit -m "test(resolver): add expired code, tenant_id in token, and IP extraction tests"
```

---

## Task 10: 代码检查 + 交付

**Files:**
- 无新增/修改（仅运行检查）

- [ ] **Step 1: Ruff lint + format**

Run: `cd backend && source .venv/bin/activate && ruff check app/ tests/ && ruff format app/ tests/`

Expected: 无错误

- [ ] **Step 2: 全量测试最终确认**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/ -v --timeout=120`

Expected: 全部 PASS

- [ ] **Step 3: 清理临时文件**

Run: `git status` 检查是否有未跟踪的临时文件（`test_*.py`, `debug_*.py` 等），如有则删除。

- [ ] **Step 4: 更新 findings.md**

在 `findings.md` 的历史记录中归档码解析模块 review，更新 `task_plan.md` 和 `progress.md` 指向新计划。

- [ ] **Step 5: 最终提交**

```bash
git add -A
git commit -m "chore: code resolver security hardening — all 13 findings addressed"
```

---

## 自审检查

### 1. Spec 覆盖

| # | 发现 | 对应 Task |
|---|------|-----------|
| 1 | TTL 注释不一致 | Task 1 |
| 2 | XFF 伪造绕过限流 | Task 2 |
| 3 | IP hash 类型不一致 | Task 1 + Task 3 |
| 4 | token 验证碎片化 | Task 4 + Task 5 |
| 5 | 缓存无主动失效 | Task 8 |
| 6 | expired 码仍颁发 token | Task 3 |
| 7 | IP hash 重复计算 | Task 3 |
| 8 | CodeBatch 重复查询 | Task 7 |
| 9 | token 不含 tenant_id | Task 1 |
| 10 | XFF 解析重复代码 | Task 2 + Task 4 + Task 5 + Task 6 |
| 11 | 延迟 import | Task 7 |
| 12 | 限流同步/异步不一致 | Task 5 |
| 13 | consents tenant_id 永远 None | Task 6 |

### 2. Placeholder 扫描

无 TBD、TODO、fill in details 等占位符。所有步骤包含完整代码。

### 3. 类型一致性

- `create_scan_token(public_id, ip_hash, tenant_id, expires_in)` 在 Task 1 定义，Task 3/4/5 调用参数一致
- `verify_scan_token(token, expected_public_id, expected_ip_hash, expected_tenant_id)` 签名一致
- `get_client_ip(request)` 在 Task 2 定义，Task 3/4/5/6 使用
- `resolve_cache.invalidate(key)` 在 Task 8 使用，与 `redis_cache.py` 现有 API 一致
