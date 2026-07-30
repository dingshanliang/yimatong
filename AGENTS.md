# 一码通（Yimatong）仓库指令

## 适用范围

- 本文件适用于整个仓库；进入 `frontend/` 后还必须遵守 `frontend/AGENTS.md`。
- 以当前代码、manifest、Compose 和 CI 为准。产品与架构细节按需查阅 `docs/README.md`、`docs/MANIFEST.md` 和 `docs/02_tech/`，不要把本文件当完整架构文档。

## 产品与架构边界

一码通是面向品牌方的包装扫码增长 SaaS：管理一物一码、H5 扫码体验、营销权益、渠道归因、风控和数据分析。系统为多租户架构，平台管理后台与品牌方 Admin 使用独立认证边界。

```text
backend/
  app/
    api/v1/       FastAPI 路由
    core/         配置、数据库、依赖和请求上下文
    models/       SQLAlchemy 模型
    schemas/      Pydantic 请求/响应
    services/     业务逻辑
    tasks/        arq 后台任务
    middleware/   租户、限流、请求日志
    cli/          seed 与运维 CLI
  alembic/        数据库迁移
  tests/          unit、API、service、integration、isolation、acceptance
frontend/
  apps/admin/     品牌方后台，端口 3000
  apps/h5/        消费者 H5
  apps/platform/  平台后台，端口 3002
  packages/shared/
  packages/design-tokens/
  e2e/            Playwright
docs/             产品、技术、交付与营销资料
```

## 必须遵守的安全规则

- 业务表默认包含 `tenant_id`；新增或修改查询时同时保留应用层租户过滤与 PostgreSQL RLS，不得只依赖前端或请求参数隔离。
- 普通租户请求使用 `get_db()`；只有平台管理、受控后台任务等明确跨租户场景才使用 `get_db_with_bypass()`。消费者 scan-token 路径沿用 `get_db_for_consumer()`。
- `TenantScopeMiddleware` 将 JWT/API Key 上下文写入 request state 和 RLS context。新增公开路由、scan-token 路由或中间件绕过条件时，必须补认证边界与跨租户测试。
- Admin 认证使用 `/api/v1/auth/login` 与 `access_token`；后端收到 Bearer header 时优先于 cookie。Platform 使用 `/api/v1/platform/auth/login` 与 `platform_access_token`，不得混用租户 Admin 权限。
- 手机号等 PII 沿用现有加密、哈希和 consent 记录机制；不得把密钥、生产凭证或真实个人数据写入代码、文档、测试 fixture 或日志。
- 数据导出必须有权限控制和审计；含个人信息时保留导出原因。
- 模型变更必须附 Alembic 迁移。迁移应可回滚；涉及数据删除、重写或 RLS 策略时先明确备份与回滚路径。

## 本地环境

- 使用 Node.js 22+ 与仓库 `packageManager` 锁定的 pnpm 10；执行前端命令前确认 `pnpm --version` 为 10.x，版本不符时从仓库根目录启用 Corepack，避免 pnpm 11 重建现有 `node_modules`。
- 本机不运行原生 PostgreSQL/Redis；使用本仓库 Docker 容器，不要启动 Homebrew PostgreSQL 占用 5432。
- 日常宿主机开发使用 `docker-compose.infra.yml`：PostgreSQL 5433、Redis 6380、MinIO 9000/9001、mock SMS 3099、mock WeChat 3098。
- `docker-compose.dev.yml` 是完整容器栈：PostgreSQL 5432、Admin 3000、H5 3001、backend 8000。不要混用两套 Compose 的数据库端口假设。
- `pnpm dev:stack` 会执行 `up -d --build --force-recreate`；依赖和镜像未变时可用 `docker compose -f docker-compose.infra.yml start`。

## 常用命令

### 基础设施

```bash
pnpm dev:stack
```

### 后端

```bash
cd backend
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run python -m app.cli all
uv run uvicorn app.main:app --reload --port 8000
```

最小相关验证优先：

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/test_api/test_auth_login.py
uv run pytest
```

普通 `pytest` 默认排除 acceptance。Acceptance 测试需要 infra PostgreSQL 5433，并会创建/删除专用 acceptance 数据库，只能显式运行：

```bash
cd backend
uv run pytest -m acceptance
```

### 前端

```bash
cd frontend
pnpm install
pnpm dev:admin
pnpm dev:h5 --port 3001
pnpm dev:platform

pnpm check
pnpm lint:admin
pnpm lint:h5
pnpm lint:platform
pnpm --filter @yimatong/admin exec tsc --noEmit

pnpm build
pnpm test:e2e
```

`pnpm build` 的顺序包含 shared、design tokens、Admin、H5、Platform。只构建单个应用前，先确保其 workspace 依赖已通过检查或构建。

Admin 组件测试：

```bash
cd frontend/apps/admin
pnpm exec vitest run
```

## 验证要求

- 先运行受影响模块的最小测试，再按风险扩大到 lint、类型检查、构建或全量测试。
- 后端安全、认证、租户/RLS 改动至少覆盖 API 行为和 `tests/test_isolation/` 中的相关隔离场景。
- 前端测试验证业务行为、权限、状态、payload 和可访问入口；普通说明文案、装饰性 copy 和大范围快照不作为稳定契约。
- E2E 配置使用 Admin 3000、H5 3003、backend 8000，且 `workers: 1`；不要把普通 H5 开发端口 3001 写进 Playwright 流程。
- 视觉改动在必要时用真实浏览器和截图验证，不能只以 typecheck 或组件测试代替。

## 实现约定

### 后端

- Python 3.12+、异步 SQLAlchemy 2.0；路由负责校验和编排，业务逻辑放 service 层。
- Ruff 行宽 120，规则与例外以 `backend/pyproject.toml` 为准；迁移版本目录被 Ruff 排除。
- 先复用现有模型、schema、service 和权限工具，不在路由里复制状态机或租户判断。

### 前端

- TypeScript strict、React 19、Next.js 16；Admin/Platform 优先复用 Ant Design，H5 保持移动优先。
- 跨应用能力放稳定的 workspace package interface；遵守 `frontend/packages/README.md` 的 deep-module 边界，禁止从其他 package 的私有目录 deep import。
- 保留各应用 `next.config.ts` 的显式 `turbopack.root`。Next dev server 启动但 `/login` 或 manifest 卡住时，先查该配置和代理目标。
- 更细规则见 `frontend/AGENTS.md`。

## 数据与演示

- 完整 seed：`cd backend && uv run python -m app.cli all`。
- 仅重建 demo 租户：`cd backend && uv run python -m app.cli demo --clean`。
- Demo 租户 slug 为 `demo`；开发账号以 seed 命令输出及 `backend/app/cli/seed.py`、`backend/scripts/seed_demo.py` 为准，不把 demo 密码当生产凭证。

## 协作与审查

- 使用 Conventional Commits，保持提交范围单一；未经用户要求不 push、merge 或 deploy。
- 用户要求并行审查时：Alembic/RLS 迁移路由给 `.codex/agents/migration-reviewer.toml`；认证、租户隔离、公开路由、加密和 PII 路由给 `.codex/agents/security-reviewer.toml`。
- 不把一次性事故、临时进度或可直接从代码读取的大段事实继续堆入本文件。

## 权威参考

- 文档索引：`docs/README.md`、`docs/MANIFEST.md`
- 技术架构：`docs/02_tech/ARCHITECTURE.md`
- 数据模型：`docs/02_tech/DATA_MODEL.md`
- API 草案：`docs/02_tech/API_DRAFT.md`
- 权限矩阵：`docs/02_tech/PERMISSION_MATRIX.md`
- 安全合规：`docs/02_tech/SECURITY_COMPLIANCE.md`
