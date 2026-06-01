---
name: project-conventions
description: Project-specific coding conventions and patterns for yimatong — architecture, naming, models, schemas, services, and API routes
user-invocable: false
---

# 一码通项目编码规范

Codex 在编写代码时应遵循以下规范。这些是项目已确立的模式，不是建议。

## 后端四层架构

```
api/v1/    → 路由定义 + 请求验证（Pydantic inline 或 schema 文件）
services/  → 业务逻辑，接收 db session + 参数，返回 dict 或 model
models/    → SQLAlchemy 2.0 ORM（Mapped + mapped_column）
schemas/   → Pydantic V2 响应模型（仅当 inline schema 过长时独立文件）
```

## Model 规范

```python
# models/xxx.py — 继承 TenantModel（自动包含 id, tenant_id, created_at, updated_at）
from app.models.base import Base, TenantModel

class Campaign(TenantModel, Base):
    __tablename__ = "campaigns"

    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    campaign_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    # ...
```

关键约束：
- 所有业务 model 继承 `TenantModel`（提供 `id`, `tenant_id`, `created_at`, `updated_at`）
- 主键 `id` 为 UUID v7（通过 `uuid6.uuid7` 生成）
- `tenant_id` 必须为 `nullable=False, index=True`
- 不要使用 `sa.Column()` 风格，统一使用 `Mapped + mapped_column`

## Schema 规范

```python
# schemas/xxx.py 或 api/v1/xxx.py 内 inline
from pydantic import BaseModel

class CampaignCreateRequest(BaseModel):
    name: str
    campaign_type: str
    # 所有用户输入必须有验证
```

关键约束：
- 简单 CRUD 的 request/response schema 可以 inline 在 API 文件中
- 复杂或被多处复用的 schema 独立放到 `schemas/` 目录
- 响应模型使用 `from_attributes = True` 以支持 ORM 转换
- 公共分页响应使用 `schemas.common.PaginatedResponse`

## Service 规范

```python
# services/xxx.py — 纯业务逻辑，不依赖 FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

async def create_campaign(db: AsyncSession, tenant_id: uuid.UUID, ...) -> dict:
    obj = Campaign(tenant_id=tenant_id, ...)
    db.add(obj)
    await db.flush()
    await db.refresh(obj)
    return _to_dict(obj)
```

关键约束：
- 所有 service 函数第一个参数为 `db: AsyncSession`，第二个为 `tenant_id: uuid.UUID`
- 使用 `flush()` + `refresh()` 而非 `commit()`（由 API 层统一提交）
- 返回 `dict` 或 model 实例，不返回 Response 对象
- 查询时始终包含 `.where(Model.tenant_id == tenant_id)`
- 新增记录排序第一（列表查询用 `ORDER BY created_at DESC`）

## API Route 规范

```python
# api/v1/xxx.py
from fastapi import APIRouter, Depends
from app.core.database import get_db
from app.core.dependencies import get_current_tenant

router = APIRouter(prefix="/api/v1/resource", tags=["resource"])

@router.post("")
async def create_resource(
    body: CreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    result = await service_func(db, tenant_id, ...)
    return result
```

关键约束：
- 使用 `get_current_tenant` dependency 获取 tenant_id（从 JWT 提取）
- CRUD 路由顺序：POST（创建）→ GET 列表 → GET 详情 → PUT → DELETE
- 路由前缀统一 `/api/v1/{resource_name}`
- 公开路由（不需要认证）不使用 `get_current_tenant` 依赖

## 前端规范

### Admin（管理后台）
- UI 框架：Ant Design 6 + `@ant-design/icons`
- 状态管理：Zustand store（`src/lib/` 下）
- API 请求：`src/lib/api.ts` 的 axios 实例（自动 Bearer token）
- 新增记录排在列表第一个位置

### H5（消费者扫码页）
- UI 框架：Tailwind CSS 4 + Headless UI
- 不使用 Ant Design
- 面向移动端，响应式设计

### 共享包
- `packages/shared/` 存放 TypeScript 类型定义
- 使用 `workspace:*` 协议引用
- 修改 shared 后需 `pnpm build:shared` 再使用

## 禁止事项

- 禁止在 service 层导入 FastAPI 组件（HTTPException, Request, Response 等）
- 禁止在 model 中写业务逻辑
- 禁止硬编码密码、API Key 等敏感信息
- 禁止跳过 `tenant_id` 过滤（即使"当前只有一个租户"）
- 禁止使用 `sa.Integer` 作为主键类型
- 禁止在 scan_events 分区表上做 ALTER（只允许追加）
