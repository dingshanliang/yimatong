# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

一码通（yimatong）是一款面向食品、农产品及消费品品牌方的**包装扫码增长 SaaS**。独立产品，不依赖食链通、GTS 或任何既有溯源系统，可以独立销售、独立部署。项目已完成阶段一 Alpha 和 Beta 以及阶段二的大部分功能开发，当前处于功能完善和测试阶段。

## 开发命令

### 后端（backend/）

```bash
cd backend && source .venv/bin/activate   # 激活虚拟环境（uv 管理）
uv run uvicorn app.main:app --reload      # 启动开发服务器（端口 8000）
alembic upgrade head                       # 运行数据库迁移
alembic revision --autogenerate -m "desc"  # 生成新迁移
python -m pytest                           # 运行全部测试
python -m pytest tests/unit/               # 只跑单元测试
python -m pytest tests/integration/        # 只跑集成测试
python -m pytest tests/path/test_file.py::test_name  # 跑单个测试
ruff check .                               # Lint
ruff format .                              # 格式化
```

### 前端（frontend/）

```bash
cd frontend
pnpm dev:admin      # 启动 Admin 开发服务器（端口 3000）
pnpm dev:h5         # 启动 H5 开发服务器（端口 3001）
pnpm build:admin    # 只构建 Admin
pnpm build:h5       # 只构建 H5
pnpm build          # 构建全部
```

### Docker 本地环境

```bash
docker compose -f docker-compose.dev.yml up -d   # 启动全部服务
docker compose -f docker-compose.dev.yml logs backend --tail 50  # 查看后端日志
docker compose -f docker-compose.dev.yml exec backend /app/.venv/bin/python -c "..."  # 容器内执行 Python
```

**注意**：如果本地已有 PostgreSQL 占用 5432 端口，`localhost:5432` 连接的是本地 PG 而非 Docker PG。Docker 容器内的后端通过 `postgres:5432`（Docker 内部网络）连接。

## 项目结构

```
backend/
  app/
    api/v1/          API 路由层（每个资源一个模块文件）
    cli/             CLI 工具（seed 数据等）
    core/            配置、数据库连接、依赖注入
    middleware/      中间件（tenant scope、CORS）
    models/          SQLAlchemy 2.0 模型（按领域分文件）
    schemas/         Pydantic V2 schema
    services/        业务逻辑层
    tasks/           Arq 异步任务
    templates/       Jinja2 页面模板
    utils/           工具函数（security、crypto、i18n）
  tests/
    unit/            单元测试
    integration/     集成测试
    test_middleware/  中间件测试
  alembic/           数据库迁移（0001-0009）
  docker-compose.dev.yml  完整本地环境

frontend/
  apps/admin/        管理后台（Next.js App Router + Ant Design 6）
    src/app/
      (auth)/login/  登录页
      (dashboard)/   所有认证后页面（layout.tsx 含侧边栏+头部）
    src/lib/
      api.ts         Axios 实例（自动带 Bearer token）
      auth.ts        Zustand auth store（localStorage + cookie 双写）
      theme.ts       Ant Design 主题配置
    src/middleware.ts Next.js 中间件（cookie 检查 → 未认证重定向 /login）
  apps/h5/           消费者扫码页（Next.js + Tailwind + Headless UI）
  packages/shared/   共享 TypeScript 类型（@yimatong/shared）

docs/
  01_product/        PRD、路线图、信息架构
  02_tech/           技术架构、数据模型、API 设计、任务拆解
  05_samples/        DSL 配置示例、租户配置示例
```

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 API | FastAPI（Python 3.12+，uv 管理） |
| 数据库 | PostgreSQL 16（SQLAlchemy 2.0 async，Alembic 迁移） |
| 缓存/队列 | Redis 7（redis[hiredis]，arq 异步任务） |
| 对象存储 | MinIO（boto3 兼容，本地）/ S3 兼容（生产） |
| 管理后台 | Next.js App Router + Ant Design 6 + Zustand + Axios |
| 消费者 H5 | Next.js App Router + Tailwind CSS 4 + Headless UI |
| 前端管理 | pnpm workspace monorepo |
| 测试 | pytest（后端）+ Playwright（前端 E2E） |

## 核心架构要点

### 多租户隔离

- **双重隔离**：应用层 `tenant_id` 过滤 + PostgreSQL RLS（`SET LOCAL app.tenant_id`）
- **RLS 辅助函数**：`current_tenant_id()` 读取 `current_setting('app.tenant_id', true)`，返回 NULL 时放行所有行
- **中间件**：`TenantScopeMiddleware` 从 JWT 提取 `tenant_id`，写入 context var → `get_db()` 在事务中 `SET LOCAL`
- **公开路由跳过认证**：`/api/v1/auth/login`、`/c/{public_id}`、`/health` 等
- **所有业务表必须包含 `tenant_id`**（即使可从父表推导）

### 前端认证流

1. **登录**：`/api/v1/auth/login` → JWT access_token + refresh_token
2. **Token 存储**：同时写入 localStorage（axios 拦截器读取）和 cookie（Next.js middleware 读取）
3. **路由守卫**：`middleware.ts` 检查 cookie → 无 token 重定向 `/login`
4. **Dashboard layout**：`useState` lazy initializer 同步检查 localStorage + Zustand auto-hydrate
5. **API 请求**：axios 拦截器自动添加 `Authorization: Bearer {token}`
6. **401 处理**：axios 响应拦截器清除认证状态并跳转 `/login`

### 后端四层架构

```
api/v1/    → 路由定义、请求验证（Pydantic schema）、依赖注入
services/  → 业务逻辑、跨模型协调
models/    → SQLAlchemy 2.0 ORM 模型（mapped_column, Mapped）
schemas/   → Pydantic V2 请求/响应模型
```

### 码解析服务

- 公开路由 `/c/{public_id}`，不走 JWT 鉴权
- `public_id`：10 位 Base62（CSPRNG）+ Luhn 校验位
- resolver 颁发短期 scan_token JWT，H5 后续请求需携带

### 页面引擎

- `PageTemplate` → `PageVersion`（config_json 为 DSL）
- 支持草稿/预览/发布/下线/回滚版本控制

### 事件系统

- `scan_events` 按月分区表，追加写入，不可频繁更新
- 双层幂等：Redis 缓存（体验优化）+ 数据库唯一约束（最终一致）

## 关键设计约束

- 主键使用 UUID v7（时间排序），使用 uuid6 库
- 短链格式：`https://qr.yimatong.cn/c/{public_id}`，永久可解析
- 消费者手机号 AES-GCM 加密 + HMAC-SHA256 哈希索引
- 码、页面、活动、权益必须支持版本化或历史记录
- 所有导出行为记录 `export_log`
- 外部系统数据保留 `external_id` 和 `source_system`
- 认证：阶段一 Bearer Token，阶段二评估 HttpOnly Cookie + CSRF

## 关键参考文件

| 场景 | 文件 |
|---|---|
| 开发策略设计（最新） | `docs/superpowers/specs/2026-05-27-development-strategy-design.md` |
| 实施任务 | `docs/02_tech/TASKS.md` |
| 技术架构 | `docs/02_tech/ARCHITECTURE.md` |
| 数据模型 | `docs/02_tech/DATA_MODEL.md` |
| API 设计 | `docs/02_tech/API_DRAFT.md` |
| 页面配置 DSL | `docs/05_samples/page_config_example.yaml` |
| 完整 PRD | `docs/01_product/PRD.md` |
| 路线图 | `docs/01_product/ROADMAP.md` |

## 已知环境陷阱

- **端口 5432 冲突**：本地 PostgreSQL 可能占用 5432，导致 `localhost:5432` 连接到本地 PG 而非 Docker PG。Docker 容器通过内部网络 `postgres:5432` 连接。操作 Docker 内的数据库数据时需用 `docker exec` 而非本地 psql。
- **Redis 端口**：Docker Redis 映射到 `localhost:6380`（非默认 6379），但容器内部用 `redis:6379`。
- **Alembic stamp**：如果数据库表已存在但 `alembic_version` 为空，用 `alembic stamp head` 标记版本而非重新迁移。
