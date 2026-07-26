# 一码通 (Yimatong) 项目指南

> 本文件面向 AI 编程助手。修改前请先阅读全文，确保理解项目结构、技术栈、安全模型和开发流程。

## 1. 项目概述

一码通是一款面向食品、农产品及消费品品牌方的**包装扫码增长 SaaS**。核心能力包括：

- 为产品包装生成一物一码 / 内外码，并管理码批次、状态与生命周期。
- 消费者扫码后进入可配置 H5 页面，展示溯源、品牌、活动、权益等内容。
- 后台支持营销活动配置、权益发放、渠道归因、风控预警、数据看板、会员积分、企微/微信集成。
- 多租户架构：品牌方租户共享平台实例，数据通过应用层过滤 + PostgreSQL RLS 双重隔离。
- 平台管理后台独立运行，用于全局租户、套餐、配额、审计、健康监控。

仓库为前后端分离的全栈 monorepo：

- `backend/`：Python 3.12 + FastAPI 后端 API、异步任务、数据库迁移。
- `frontend/`：pnpm workspace，包含 Admin 管理后台、H5 消费者端、Platform 平台后台、共享包与 E2E 测试。
- `docs/`：产品、技术、交付、营销资料（中文）。

## 2. 技术栈

### 后端

| 层级 | 技术 |
|------|------|
| 语言 / 包管理 | Python 3.12+，uv |
| Web 框架 | FastAPI 0.115+ |
| ORM / 迁移 | SQLAlchemy 2.0 (async)，Alembic |
| 数据库 | PostgreSQL 16 |
| 缓存 / 队列 | Redis 7，`redis[hiredis]`，`arq`（异步任务） |
| 对象存储 | MinIO（本地开发），S3 兼容（生产） |
| 认证 | JWT (python-jose) + bcrypt，独立平台管理员认证 |
| 加密 | AES-256-GCM（手机号等敏感字段），HMAC-SHA256 pepper |
| 测试 | pytest、pytest-asyncio、testcontainers、aiosqlite |
| 代码检查 | Ruff |

### 前端

| 层级 | 技术 |
|------|------|
| 包管理 | pnpm 10 workspace |
| 框架 | Next.js 16 App Router，React 19 |
| Admin / Platform UI | Ant Design 6，Ant Design Icons |
| H5 UI | Tailwind CSS 4，Headless UI |
| 状态管理 | Zustand |
| 数据请求 | axios + SWR（Admin/Platform），直接 fetch / axios（H5） |
| 共享包 | `@yimatong/shared`（类型、JWT、API client、hooks） |
| 测试 | Vitest（Admin 组件/单元），Playwright（E2E） |
| 代码检查 | ESLint（`eslint-config-next`） |

### 基础设施与部署

- 本地开发：Docker Compose（PostgreSQL、Redis、MinIO、mock SMS、mock WeChat）。
- 完整本地栈：`docker-compose.dev.yml` 额外包含 backend、worker、migration、seed、admin、h5 服务。
- 部署目标：Docker Compose（当前），后续可扩展至 Kubernetes。

## 3. 仓库结构

```text
.
├── backend/
│   ├── app/
│   │   ├── api/v1/          # FastAPI 路由（按领域拆分）
│   │   ├── core/            # 配置(config.py)、数据库(database.py)、依赖(dependencies.py)、上下文(context.py)
│   │   ├── models/          # SQLAlchemy ORM 模型（约 30 个模型文件）
│   │   ├── schemas/         # Pydantic 请求/响应模型
│   │   ├── services/        # 业务逻辑层
│   │   ├── tasks/           # arq / 后台 worker（webhook 投递等）
│   │   ├── middleware/      # TenantScopeMiddleware、限流、RequestID、日志
│   │   ├── utils/           # 安全、加密、限流、RBAC、工具函数
│   │   └── main.py          # FastAPI 入口
│   ├── alembic/             # Alembic 迁移脚本
│   ├── tests/               # pytest 测试（按 test_api / test_services / test_models / test_isolation 等组织）
│   ├── cli/                 # 种子数据与运维 CLI（app.cli.seed）
│   ├── pyproject.toml       # 依赖、Ruff、pytest 配置
│   ├── uv.lock              # uv 锁定文件
│   ├── Dockerfile           # 后端镜像
│   └── .env.example         # 本地环境变量模板
├── frontend/
│   ├── apps/
│   │   ├── admin/           # 品牌方 Admin 后台，默认端口 3000
│   │   ├── h5/              # 消费者 H5，默认端口 3001（开发）/ 3003（Playwright）
│   │   └── platform/        # 平台管理后台，默认端口 3002
│   ├── packages/shared/     # 共享类型与工具
│   ├── e2e/                 # Playwright 端到端测试
│   ├── package.json         # workspace scripts
│   ├── pnpm-workspace.yaml  # workspace 配置
│   └── playwright.config.ts # E2E 配置
├── docs/                    # 产品、技术、交付文档（中文）
├── docker-compose.dev.yml   # 完整本地开发栈
├── docker-compose.infra.yml # 仅基础设施
├── scripts/dev-stack.sh     # `pnpm dev:stack` 实际执行：启动基础设施
└── AGENTS.md                # 本文件
```

## 4. 本地开发环境搭建

### 前置条件

- Python 3.12+
- uv (`pip install uv` 或官方安装脚本)
- Node.js 22+ 与 pnpm 10（启用 corepack 后可自动准备）
- Docker & Docker Compose

### 方式 A：仅启动基础设施，前后端在宿主机运行（推荐日常开发）

```bash
# 启动 Postgres(5433)、Redis(6380)、MinIO(9000/9001)、mock 服务
# 注意：该命令会 --build --force-recreate，首次/镜像变更后使用
pnpm dev:stack

# 日常重启（依赖未变时）
docker compose -f docker-compose.infra.yml start
```

注意：

- `docker-compose.infra.yml` 将 PostgreSQL 映射到宿主机的 **5433** 端口，避免与本机可能存在的 5432 原生 PG 冲突。
- Redis 映射到 **6380**。
- MinIO API 在 **9000**，Console 在 **9001**。
- Mock SMS 在 **3099**，Mock WeChat 在 **3098**。

然后分别在两个终端启动前后端：

```bash
# 后端（在 backend/ 目录）
cd backend
cp .env.example .env
# 编辑 .env：database_url 使用 localhost:5433（若用 infra）或 localhost:5432（若用 dev.yml 完整栈）
uv sync
uv run alembic upgrade head           # 首次或迁移变更后
uv run python -m app.cli all          # 可选：生成种子数据
uv run uvicorn app.main:app --reload --port 8000

# 前端（在 frontend/ 目录）
cd frontend
pnpm install
pnpm dev:admin              # http://localhost:3000
pnpm dev:h5 --port 3001     # http://localhost:3001（H5 dev 脚本未固定端口）
pnpm dev:platform           # http://localhost:3002
```

### 方式 B：使用完整 Docker Compose 栈

```bash
# 启动所有服务：postgres(5432)、redis、minio、backend、worker、migration、seed、admin、h5、mocks
docker-compose -f docker-compose.dev.yml up -d
```

- 该方式下 PostgreSQL 映射到 **5432**。
- 容器内会自动执行 Alembic 迁移和种子数据（seed service）。
- 日常调试若依赖/镜像未变化，优先 `docker-compose -f docker-compose.dev.yml start` 复用已有容器，避免反复 `--build --force-recreate`。

### 环境变量要点

后端 `.env` 关键项：

- `database_url`：PostgreSQL 连接串。
- `redis_url`：Redis 连接串。
- `secret_key`：JWT 签名密钥，生产必须替换。
- `AES_MASTER_KEY_V1`、`HMAC_PEPPER`：敏感字段加密与密码 pepper，生产必须生成并保密。
- `platform_admin_email`、`platform_admin_password_hash`：平台管理员账号（bcrypt hash）。
- `minio_endpoint` / `minio_access_key` / `minio_secret_key`：对象存储。
- `deepseek_api_keys`：AI 功能需要的 DeepSeek API key。

示例生成命令：

```bash
# JWT secret
python -c "import secrets; print(secrets.token_urlsafe(32))"
# AES/HMAC keys (64 字符 hex)
python -c "import secrets; print(secrets.token_hex(32))"
# bcrypt hash
python -c "import bcrypt; print(bcrypt.hashpw(b'YourPassword', bcrypt.gensalt()).decode())"
```

## 5. 常用命令

### 后端

```bash
cd backend

# 安装/同步依赖
uv sync

# 运行服务
uv run uvicorn app.main:app --reload --port 8000

# 数据库迁移
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "describe change"

# 种子数据
uv run python -m app.cli all           # 生成完整种子（含 demo 租户）
uv run python -m app.cli demo --clean  # 仅重建 demo 租户

# 测试
uv run pytest
uv run pytest tests/test_api/test_auth_login.py

# 代码检查与格式化
uv run ruff check .
uv run ruff format --check .
uv run ruff format .
```

### 前端

```bash
cd frontend

# 安装依赖
pnpm install

# 开发服务器
pnpm dev:admin              # http://localhost:3000
pnpm dev:h5 --port 3001     # http://localhost:3001（H5 dev 脚本未固定端口）
pnpm dev:platform           # http://localhost:3002

# 构建（shared 必须先构建）
pnpm build:shared
pnpm build:admin
pnpm build:h5
pnpm build:platform
pnpm build            # 构建全部

# 代码检查
pnpm lint:admin
pnpm lint:h5
pnpm lint:platform
pnpm --filter @yimatong/shared typecheck

# 测试
pnpm test:e2e
pnpm test:e2e:ui
pnpm test:e2e:debug

# Admin 单元/组件测试
cd frontend/apps/admin
pnpm exec vitest run
```

### 根目录

```bash
# 仅启动基础设施（不启动 backend / frontend 容器）
pnpm dev:stack
```

## 6. 核心模块说明

### 后端领域模块

- **认证与权限** (`api/v1/auth.py`, `services/auth.py`, `utils/security.py`)：JWT 登录、刷新、登出、密码强度、token 撤销。
- **租户与组织** (`api/v1/tenants.py`, `api/v1/organizations.py`, `models/tenant.py`)：租户创建、套餐、组织树、角色。
- **产品资料** (`api/v1/products.py`, `models/product.py`)：品牌、产品、SKU、生产批次、检测报告与资质证书。
- **码管理** (`api/v1/code_batches.py`, `services/code.py`, `models/code.py`)：码批次生成、激活、状态机、导出、一物一码/内外码。
- **二维码解析** (`api/v1/resolver.py`, `services/resolver.py`)：公开短链 `/c/{public_id}`，低延迟解析码状态、风控、返回页面或 JSON。
- **页面引擎** (`api/v1/page_templates.py`, `services/page*.py`, `models/page.py`)：H5 页面模板、组件配置、版本发布/回滚/预览。
- **活动与权益** (`api/v1/campaigns.py`, `api/v1/benefits.py`, `api/v1/benefit_claims.py`)：营销活动、优惠券、积分、红包、权益领取与核销。
- **渠道与区域** (`api/v1/channels.py`, `services/channel.py`)：经销商、区域、门店、码段分配、防窜预警。
- **风控** (`api/v1/risk*.py`, `services/risk*.py`)：首扫/重扫、跨区扫码、IP/设备/预算/频次限制、自动处置。
- **数据分析** (`api/v1/analytics*.py`, `services/analytics*.py`)：扫码统计、活动效果、GMV 归因、渠道看板。
- **集成中心** (`api/v1/connectors.py`, `api/v1/integration.py`, `services/integration.py`)：外部权益连接器、Webhook、企微集成、开放 API。
- **平台管理** (`api/v1/platform.py`)：平台管理员专属接口，绕过 RLS。
- **后台任务** (`tasks/worker.py`)：Webhook 投递、重试、清理。

### 前端应用

- **Admin (`apps/admin`)**：品牌方运营后台。使用 Ant Design 6、Tailwind CSS 4、`@ant-design/charts`。核心页面在 `(dashboard)/` 路由组。
- **H5 (`apps/h5`)**：消费者扫码页面。轻量移动端，核心入口 `/c/[publicId]`。
- **Platform (`apps/platform`)**：平台运营后台，独立认证与主题。
- **Shared (`packages/shared`)**：JWT 解析、`api-client`、通用 hooks。

## 7. 多租户隔离与安全模型

这是本项目的核心约束，修改前必须理解：

### 双重隔离

1. **应用层**：`TenantScopeMiddleware` 从 JWT 或平台 token 提取 `tenant_id`，写入 `request.state`；API 与 service 层按 `tenant_id` 过滤。
2. **数据库层（PostgreSQL RLS）**：`get_db()` 在事务开始时执行 `SET LOCAL app.tenant_id = '...'`，RLS 策略根据该配置限制行可见性。

### 关键约定

- 所有业务表必须包含 `tenant_id`（平台级配置表除外）。
- 平台管理员 / 后台 worker 使用 `get_db_with_bypass()` 显式绕过 RLS。
- 公开路由（扫码 `/c/`、登录、部分消费者接口）跳过 `TenantScopeMiddleware` 的 JWT 校验。
- 文件存储按租户分目录，避免跨租户访问。

### 认证体系

- **Admin 租户账号**：`/api/v1/auth/login`，cookie `access_token`，请求头也可携带 `Authorization: Bearer <token>`。
- **Platform 管理员**：`/api/v1/platform/auth/login`，独立 cookie `platform_access_token`，不依赖 demo 租户。
- **开放 API**：`/open/v1/...` 使用 API Key 认证。
- **消费者**：扫码 token（`scan_token`）用于无登录场景，受 RLS 保护。

### 敏感数据处理

- 手机号使用 AES-256-GCM 加密存储。
- 密码使用 bcrypt + HMAC pepper。
- IP 哈希加盐，防止彩虹表。
- 消费者同意书（consent）必须记录授权场景、版本、时间、IP/UA、撤回时间。

### 风控与合规

- 活动必须配置领取限制、预算、库存、有效期、隐私授权、免责说明。
- 风控覆盖：首扫/重扫、同码多地、跨区扫码、IP/设备/手机号频次、预算耗尽、库存耗尽。
- 数据导出需权限控制并记录审计日志；含个人信息的导出需导出原因。

## 8. 代码风格与约定

### 后端

- Python 3.12 语法，120 字符行宽。
- Ruff 规则：`E`, `F`, `I`, `N`, `W`, `UP`；忽略 `N818`。
- 一级导入标记为 `app`（`known-first-party = ["app"]`）。
- 测试文件命名：`test_*.py`；测试类 `Test*`；测试函数 `test_*`。
- 异步优先：数据库操作使用 `AsyncSession`。
- 模型、schema、service、API 分层清晰，避免在路由中写业务逻辑。
- Alembic 迁移文件位于 `backend/alembic/versions/`，`alembic/env.py` 和 `main.py` 的 E402 忽略是预期的。

### 前端

- TypeScript 严格模式，React 19，Next.js 16 App Router。
- 组件 PascalCase，hooks `useSomething`，路由相关代码放在对应 `src/app/...` 目录。
- Admin/Platform 优先使用 Ant Design 组件；H5 保持轻量、移动优先。
- 共享代码放入 `frontend/packages/shared/src`。
- Admin 与 Platform 有独立的 theme provider 和 auth store。
- `next.config.ts` 中保留了 `turbopack.root` 配置（指向 workspace root），不要随意删除。

### Git 提交

- 使用 Conventional Commits，例如：
  - `feat(backend): ...`
  - `fix(frontend): ...`
  - `refactor: ...`
  - `style: ...`
- 保持提交范围单一，避免同一提交混合后端、前端、基础设施无关变更。

## 9. 测试策略

### 后端

- **单元测试**：`backend/tests/unit/`，覆盖工具函数、加密、状态机、请求 ID 等。
- **服务层测试**：`backend/tests/test_services/`，覆盖业务逻辑与边界条件。
- **API 测试**：`backend/tests/test_api/`，覆盖接口行为与权限。
- **隔离测试**：`backend/tests/test_isolation/`，专门验证 RLS 与租户隔离。
- **集成测试**：`backend/tests/test_integration/`，例如完整扫码闭环。
- **迁移测试**：`backend/tests/test_migration_on_clean_db.py`，验证迁移可在干净数据库运行。
- 运行最小相关测试优先，再跑 `uv run pytest` 全量。

### 前端

- **Admin 组件/单元测试**：Vitest，放在 `__tests__/` 或功能目录旁。
- **E2E**：Playwright，配置在 `frontend/playwright.config.ts`。
  - Admin: `http://localhost:3000`
  - H5: `http://localhost:3003`（注意 Playwright 使用 3003，避免与 mock-sms 的 3001 冲突）
  - API: `http://localhost:8000`
- E2E 默认串行执行（`workers: 1`），因共享后端状态。

## 10. 数据库迁移

```bash
cd backend

# 生成迁移
uv run alembic revision --autogenerate -m "add X to Y"

# 升级
uv run alembic upgrade head

# 降级
uv run alembic downgrade -1

# 检查历史
uv run alembic history
```

- 模型变更必须同步生成并提交 Alembic 迁移。
- 迁移脚本需保持可回滚，避免在迁移中执行破坏性数据操作而不备份。
- 复杂迁移或涉及 RLS 策略变更，建议路由给 `.codex/agents/migration-reviewer.toml` 进行并行审查。

## 11. Demo 数据与测试账号

生成 demo 租户：

```bash
cd backend
uv run python -m app.cli all       # 首次生成全部种子
uv run python -m app.cli demo --clean  # 重建 demo 租户（slug=demo）
```

Demo 租户快速登录账号（`/api/v1/auth/login`，`tenant_slug: demo`）：

| 账号 | 密码 | 角色 |
|------|------|------|
| `admin@demo.com` | `Admin1234` | 品牌管理员 |
| `ops@demo.com` | `Ops123456` | 活动运营 |
| `agency@demo.com` | `Agency1234` | 代运营顾问 |
| `dist@demo.com` | `Dist123456` | 经销商入口 |
| `store@demo.com` | `Store123456` | 门店入口 |

平台管理员账号通过环境变量 `platform_admin_email` / `platform_admin_password_hash` 配置，登录端点 `/api/v1/platform/auth/login`。

## 12. 部署与运维

- 构建镜像：`backend/Dockerfile` 使用多阶段安装 uv 并同步生产依赖。
- 容器启动顺序：`postgres` → `migration`（`alembic upgrade head`）→ `seed`（可选）→ `backend` / `worker`。
- 生产环境必须替换所有默认密钥：`secret_key`、`AES_MASTER_KEY_V1/V2`、`HMAC_PEPPER`、`ip_hash_secret`。
- 生产必须启用 `cookie_secure=True`，并使用 HTTPS。
- 短信与微信 provider 可配置为真实服务商或 mock，通过 `sms_provider` / `wechat_provider` 环境变量切换。

## 13. 常见陷阱

- **CORS 报错先查后端 500**：浏览器看到 CORS 错误可能是后端异常未返回 CORS 头，先用 authenticated 请求验证真实 API 状态。
- **H5 `/login` 或 manifest 卡住**：Admin/H5 的 `next.config.ts` 中有 `turbopack.root` 指向 workspace root，排查前先检查配置，不要直接重写页面逻辑。
- **端口冲突**：
  - `pnpm dev:stack`（infra）使用 PG 5433、Redis 6380。
  - `docker-compose.dev.yml` 使用 PG 5432、H5 宿主机 3001、mock-sms 宿主机 3099（容器内 mock-sms 监听 3001，不冲突）。
  - Playwright E2E 使用 H5 3003。
- **shared 未构建导致类型错误**：执行 `pnpm build:shared` 或 `pnpm --filter @yimatong/shared typecheck`。
- **RLS 导致查不到数据**：确认 `get_db()` 正确设置了 `app.tenant_id`，平台/后台任务使用 `get_db_with_bypass()`。
- **JWT 过期或被撤销**：Token jti 会写入 Redis 撤销集合，登出后旧 token 不可用。

## 14. 并行审查路由

当用户要求子代理或并行审查时，按主题路由：

- **Alembic / 数据库变更**：`.codex/agents/migration-reviewer.toml`
- **认证、租户隔离、公开路由、加密、PII**：`.codex/agents/security-reviewer.toml`

审查完成后，在最终交接前总结审查发现。

## 15. 参考文档

- 产品/技术文档：`docs/README.md` 与 `docs/MANIFEST.md`
- 技术架构：`docs/02_tech/ARCHITECTURE.md`
- 数据模型：`docs/02_tech/DATA_MODEL.md`
- API 参考：`docs/02_tech/API_DRAFT.md`
- 权限矩阵：`docs/02_tech/PERMISSION_MATRIX.md`
- 安全合规：`docs/02_tech/SECURITY_COMPLIANCE.md`
- 前端专属规则：`frontend/AGENTS.md`
