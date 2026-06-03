# 一码通完整开发推进策略设计

> **Status:** ⏸ Partial — strategy partially followed; Alpha/Beta waves mostly done, some Phase 2 items incomplete

> **版本**：v1.2（审查修订版）
> **修订说明**：
> - v1.0：初版
> - v1.1：基于 4 维度并行审查（产品/安全/架构/可行性）整合修正项
> - v1.2：基于 Codex gpt-5.5 深度审查，采纳关键修订：(1) 阶段一拆分 Alpha/Beta 双阶段；(2) 修正依赖矛盾与验收标准；(3) 固化安全与多租户落地规则；(4) 补齐交付链路与 E2E 黄金链路

## 1. 背景与约束

- **开发模式**：1 人 + Claude Code（AI 辅助开发），无固定交付期限
- **技术栈**：FastAPI（后端）+ Next.js App Router（前端）+ PostgreSQL + Redis
- **部署**：先本地开发（docker-compose），后续决定云平台
- **策略**：最小闭环优先——先跑通后端 API + 消费者 H5，管理后台后补
- **阶段一策略**：Alpha 只跑通"租户→产品→码→固定模板 H5→扫码事件→基础统计"，用 CLI seed 配数据；Beta 补齐管理后台、活动权益、留资私域、看板导出
- **方法**：Epic 线性推进，TDD 驱动
- **原则**：三阶段全覆盖，渐进明细——粗线条全局规划已定，每个 Epic 启动时细化

## 2. 项目工程结构

```
yimatong/
├── backend/                    # FastAPI 后端
│   ├── alembic/                # 数据库迁移
│   │   ├── versions/
│   │   └── env.py              # async 模板（预配置）
│   ├── app/
│   │   ├── main.py             # FastAPI 入口
│   │   ├── config.py           # 配置管理
│   │   ├── database.py         # SQLAlchemy 引擎 + async session
│   │   ├── deps.py             # 依赖注入（get_db, get_current_user 等）
│   │   ├── models/             # SQLAlchemy ORM 模型
│   │   │   ├── tenant.py
│   │   │   ├── product.py
│   │   │   ├── code.py
│   │   │   ├── page.py
│   │   │   ├── campaign.py
│   │   │   ├── member.py       # 阶段二：会员与积分
│   │   │   ├── risk.py         # 阶段二：风控
│   │   │   ├── channel.py      # 阶段二：渠道与经销商
│   │   │   ├── regional.py     # 阶段二：区域品牌
│   │   │   ├── connector.py    # 阶段三：外部连接器
│   │   │   ├── webhook.py      # 阶段三：Webhook/Open API
│   │   │   ├── ai.py           # 阶段三：AI 辅助
│   │   │   └── event.py
│   │   ├── schemas/            # Pydantic V2 请求/响应模型
│   │   │   └── ...（与 models 一一对应）
│   │   ├── api/v1/             # API 路由
│   │   │   ├── auth.py
│   │   │   ├── tenants.py
│   │   │   ├── products.py
│   │   │   ├── codes.py
│   │   │   ├── pages.py
│   │   │   ├── campaigns.py
│   │   │   ├── resolver.py     # 码解析（无鉴权，高性能）
│   │   │   ├── analytics.py
│   │   │   ├── members.py      # 阶段二
│   │   │   ├── risk.py         # 阶段二
│   │   │   ├── channels.py     # 阶段二
│   │   │   ├── regional.py     # 阶段二
│   │   │   ├── connectors.py   # 阶段三
│   │   │   ├── webhooks.py     # 阶段三
│   │   │   └── ai.py           # 阶段三
│   │   ├── services/           # 业务逻辑层
│   │   ├── tasks/              # 异步任务
│   │   │   ├── code_export.py  # 码包导出
│   │   │   ├── event_writer.py # 事件异步写入（Redis Stream）
│   │   │   ├── report.py       # 报表聚合计算
│   │   │   ├── aggregation.py  # 事件汇总到统计表
│   │   │   └── ai_extract.py   # AI 识别（阶段三）
│   │   ├── middleware/         # tenant scope、日志、限流、安全头
│   │   │   ├── tenant.py       # tenant scope 中间件
│   │   │   ├── rate_limit.py   # 限流中间件（IP/码/全局维度）
│   │   │   └── security.py     # CORS、安全头（CSP/X-Frame-Options）
│   │   ├── connectors/         # 阶段三：外部权益连接器实现
│   │   │   ├── base.py         # 连接器基类（validate_config/test_connection/send_benefit/check_status/handle_callback）
│   │   │   ├── l1_link.py      # L1 外部链接
│   │   │   ├── l2_code_pool.py # L2 券码池
│   │   │   ├── l3_api_coupon.py# L3 API 发券
│   │   │   └── l4_order.py     # L4 订单回流
│   │   └── utils/
│   │       ├── security.py     # JWT、密码哈希、API Key
│   │       ├── id_generator.py # public_id（Base62+Luhn）、UUID v7
│   │       └── crypto.py       # AES-GCM 加密、HMAC-SHA256 哈希索引
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── unit/
│   │   └── integration/
│   ├── workers.py              # 任务队列 worker 启动（arq）
│   ├── pyproject.toml
│   └── Dockerfile
│
├── frontend/                   # 前端 monorepo（pnpm workspace）
│   ├── apps/
│   │   ├── admin/              # 管理后台（Next.js App Router + Ant Design）
│   │   │   ├── app/
│   │   │   ├── package.json
│   │   │   └── ...
│   │   └── h5/                 # 消费者扫码页（Next.js App Router + Tailwind + Headless UI）
│   │       ├── app/
│   │       ├── package.json
│   │       └── ...
│   ├── packages/
│   │   └── shared/             # 共享 TypeScript 类型
│   │       ├── types/
│   │       ├── package.json
│   │       └── ...
│   ├── pnpm-workspace.yaml
│   └── package.json
│
├── docs/                       # 产品文档
├── CLAUDE.md
└── docker-compose.yml
```

### 关键设计决策

- **后端四层分离**：models → schemas → services → api，AI 开发时每次聚焦一层
- **异步任务层**：`tasks/` 目录承载码导出、事件写入、报表计算等异步任务，阶段一用 arq（基于 Redis）
- **码解析路由独立**：不走 JWT 鉴权中间件，独立限流（IP/码/全局维度）和性能优化
- **连接器可插拔**：base.py 定义统一接口（validate_config/test_connection/send_benefit/check_status/handle_callback），新增平台只需新增连接器
- **前端 pnpm workspace monorepo**：admin 和 h5 作为 `apps/` 下独立应用，共享类型通过 `packages/shared`，避免 npm link 依赖漂移
- **安全工具层**：`utils/crypto.py` 统一处理 AES-GCM 加密和 HMAC-SHA256 哈希索引
- **docker-compose 本地开发**：PostgreSQL + Redis + MinIO 一键启动

---

## 3. 全局路线图——三阶段 Epic 总览

### 阶段一：核心闭环（EPIC-01 ~ EPIC-08）

**目标**：完成"开通客户 → 配资料 → 生成/接管码 → 配页面 → 扫码 → 领券/留资/私域跳转 → 数据统计"的闭环。

**策略调整**：阶段一拆分为 Alpha（最小可验证闭环）和 Beta（功能补齐），避免 8 波次同时背负全量复杂度。

#### Alpha：最小可验证闭环（Wave A1 ~ A7）

**Alpha 目标**：内部可演示"一个租户的一款产品生成一个码，消费者扫码看到 H5，系统记录扫码事件"。用 CLI seed 工具配数据，无需管理后台。

| 波次 | Epic 子集 | 范围 | 最小演示目标 |
|------|----------|------|-------------|
| A1 | EPIC-01 子集 | 项目骨架、docker-compose（含所有基础设施+mock 服务）、Alembic 迁移、多租户模型（含 RLS）、JWT 双 token（Bearer Token）、最小 RBAC | 启动 docker-compose，API healthcheck 正常，迁移成功，测试通过 |
| A2 | EPIC-01 子集 | 租户 CRUD、账号登录、tenant scope 中间件、RLS 隔离验证 | 创建租户、登录鉴权、tenant A 无法读取 tenant B 数据（含 RLS 验证） |
| A3 | EPIC-02 子集 | Brand/Product/SKU/Batch 最小 CRUD、文件上传（MinIO） | CLI seed 创建品牌、产品、SKU、批次，API 可读取产品信息 |
| A4 | EPIC-03 子集 | CodeBatch/CodeItem、public_id 生成（10 位 Base62 + Luhn）、码状态机、码包导出 CSV | 生成 1000 个 public_id，激活/作废码，导出码包，码状态流转测试通过 |
| A5 | EPIC-05a 子集 | PageTemplate/PageVersion 最小模型、固定模板 H5（不做完整编辑器）、published 状态 | API 可创建页面模板，固定模板可渲染品牌+产品+批次信息 |
| A6 | EPIC-04 子集 | 码解析 `/c/{public_id}`、首扫/重扫、scan_event 异步写入、限流、Redis 缓存 | 手机浏览器访问短链打开 H5 页面，重复扫码次数累计 |
| A7 | EPIC-07 子集 | 扫码次数、UV、首扫/重扫的最小统计 API（走汇总表） | API 返回扫码量、UV、首扫数、重扫数、码状态统计 |

**明确不进 Alpha**：完整管理后台、活动权益领取、页面 DSL 编辑器、代运营工作台、外部集成、风控、留资私域。

#### Beta：功能补齐（Wave B1 ~ B5）

**Beta 目标**：补齐管理后台、活动权益、留资私域、看板导出，形成可交付给试点客户的最小产品。

| 波次 | Epic | 范围 | 最小演示目标 |
|------|------|------|-------------|
| B1 | EPIC-08 子集 | 管理后台前端基础框架（登录/布局/路由）、极简 Admin Shell（产品列表/码批次/扫码统计） | 登录后台，查看产品、码批次、扫码统计 |
| B2 | EPIC-05a 补齐 | 页面 DSL 编辑（JSON Schema 校验 + dsl_version）、草稿/预览/发布/下线/回滚状态机、行业模板（2-3 个） | 后台编辑页面配置并发布，扫码看到新内容 |
| B3 | EPIC-06 | 活动 CRUD、权益 CRUD、领取接口+幂等控制（Redis + 数据库唯一约束）、基础风控（IP/设备/频率）、留资表单、私域跳转、活动法律声明、隐私政策页 | 扫码领取权益（重复领取拦截）、提交留资表单、点击私域入口 |
| B4 | EPIC-07 补齐 | 经营看板 API、活动看板 API、数据导出（含 export_log） | 测试活动数据在看板可见并能导出 |
| B5 | EPIC-08 补齐 | 代运营工作台 API、核心管理页面完善（活动中心/数据导出/操作日志）、上线检查清单 | 代运营人员可通过管理后台查看客户进度 |

### 阶段二：增强闭环（EPIC-09 ~ EPIC-16）

**目标**：增强"轻量验真、防窜货、会员积分、活动风控、数据归因"。

| 波次 | Epic | 范围 | 前置依赖 | 验收标准 |
|------|------|------|----------|----------|
| Wave 9 | EPIC-09 外码/内码双码 | 双码配对模型、外码引流页、内码验真/领奖 | EPIC-03 | 外码扫码看引流页，内码扫码验真+领奖 |
| Wave 10 | EPIC-10 轻量验真与异常预警 | 首扫验真提示、多地扫码预警、疑似复制码预警、风险冻结 | EPIC-03, 04, 07 | 异常扫码自动产生预警，后台可查 |
| Wave 11 | EPIC-11 渠道流向绑定 | 经销商/区域/门店主数据、码段分配、出库流向、跨区比对（IP→城市：GeoLite2） | EPIC-03, 04 | 码段分配给经销商，跨区扫码产生窜货线索 |
| Wave 12 | EPIC-12 轻量会员与积分 | 会员身份、积分账户、积分规则、积分兑换权益、H5 积分页 | EPIC-04, 06 | 消费者扫码积累积分，可兑换权益 |
| Wave 13 | EPIC-13 活动风控规则引擎 | 用户/手机号/设备/IP/地区/预算/时间/库存多维规则、风控拦截记录 | EPIC-06 | 风控规则触发时自动拦截，后台可查拦截记录 |
| Wave 14 | EPIC-14 渠道风控看板 | 重复扫码/跨区扫码/复制码/窜货可视化看板 | EPIC-10, 11 | 风控数据在看板可视化展示 |
| Wave 15 | EPIC-15 区域品牌/协会基础版 | 上级组织与成员企业关系、统一模板、授权产品、汇总看板 | EPIC-01, 02, 05 | 区域品牌可管理成员企业，查看汇总数据 |
| Wave 16 | EPIC-16 外部成交导入与 GMV 归因 | 手动导入外部订单（Excel）、匹配规则、GMV 归因看板 | EPIC-04, 06, 07 | 导入外部订单后可看到 GMV 归因数据 |

### 阶段三：规模化能力（EPIC-17 ~ EPIC-23）

**目标**：形成可规模化销售、服务商交付、行业模板复制和大客户集成能力。

| 波次 | Epic | 范围 | 前置依赖 | 验收标准 |
|------|------|------|----------|----------|
| Wave 17 | EPIC-17 AI 资料识别与文案生成 | 文档解析、字段提取+人工确认、页面文案草稿、活动方案生成 | EPIC-02, 05, 06 | 上传资料自动提取字段，AI 生成页面文案草稿 |
| Wave 18 | EPIC-18 外部权益连接器 L2-L4 | L2 券码池、L3 API 发券、L4 订单回流、连接器配置管理 | EPIC-06 | 外部券码导入后可扫码领取，API 发券成功 |
| Wave 19 | EPIC-19 Webhook / Open API | 事件推送订阅、签名校验、API Key 认证、重试机制、API 文档 | EPIC-07 | 外部系统可订阅 Webhook 事件 |
| Wave 20 | EPIC-20 CRM/ERP/商城集成 | 批量导入增强、ERP 同步、CRM 同步、GTS/食链通联动 | EPIC-02, 03, 11, 19 | 外部系统可通过 API 同步数据 |
| Wave 21 | EPIC-21 区域品牌高级能力与白标 | 统一码规则、高级汇总看板、自有域名+HTTPS、白标 H5 | EPIC-15 | 区域品牌可配置白标和自有域名 |
| Wave 22 | EPIC-22 高级多语言与自动切换 | 多语言模板管理、浏览器语言检测、翻译工作流 | EPIC-05 | H5 页面根据浏览器语言自动切换 |
| Wave 23 | EPIC-23 现金红包插件 | 红包规则、合规审核、第三方支付通道（持牌）、KYC、AML、限额、风控加强。**前置条件：完成合规咨询** | EPIC-13 | 现金红包发放合规、风控拦截有效 |

---

## 4. 阶段一详细设计——波次与交付物

### Alpha：最小可验证闭环（Wave A1 ~ A7）

#### A1：工程底座（EPIC-01 子集）

范围：
- FastAPI 项目骨架 + Alembic 迁移体系（async env.py 模板）
- **完整 docker-compose.dev.yml**：postgres、redis、minio、minio-init、backend、worker、migration、mock-sms、mock-wechat
- 多租户数据模型：Tenant、Organization、Account、Role、Permission
- **PostgreSQL RLS**：所有业务表启用 Row Level Security，`SET LOCAL app.tenant_id` 事务级设置（详见第 7 节 RLS 规范）
- JWT 双 token：access_token（15min，HS256，**Bearer Token 模式**）+ refresh_token（7d，Redis 存储 + 吊销机制）
- Tenant scope 中间件 + service 层强制 tenant_id 过滤（双保险）
- 最小 RBAC（admin / operator 角色）
- 套餐额度字段

交付物：启动 docker-compose，API healthcheck 正常，迁移成功，测试通过

#### A2：租户与认证（EPIC-01 子集）

范围：
- 租户 CRUD + 账号登录
- tenant scope 注入：中间件从 JWT 提取 `tenant_id`，自动过滤所有查询
- **RLS 隔离验证**：未设 tenant 查不到数据、设 tenant A 查不到 tenant B、插入 tenant B 数据被 WITH CHECK 拦截
- 平台管理员跨租户操作走独立审计路径

交付物：创建租户、登录鉴权、验证数据隔离（应用层 + RLS）

#### A3：产品资料最小集（EPIC-02 子集）

范围：
- Brand、Product、SKU、ProductionBatch 最小 CRUD
- 文件上传对接对象存储（MinIO，boto3 兼容）
- CLI seed 工具：`ymt seed tenant/product/code` 快速创建测试数据

交付物：CLI seed 创建品牌、产品、SKU、批次，API 可读取产品信息

#### A4：码生成与状态（EPIC-03 子集）

范围：
- 码批次创建
- **public_id 生成**：10 位 CSPRNG（Python secrets 模块）+ Base62 + Luhn 校验位 + 碰撞检测重试（详见第 7 节码生成规范）
- 码状态机（created → activated → bound → expired/revoked）
- 码包导出 CSV（异步任务 arq）
- **预埋 resolver stub**：`GET /api/v1/code-items/{public_id}` 返回码状态（真正的扫码解析在 A6）

交付物：生成 1000 个 public_id，激活/作废码，导出码包，码状态流转测试通过

#### A5：固定模板页面（EPIC-05a 子集）

范围：
- PageTemplate、PageVersion 最小模型
- 固定模板 H5：不做完整 DSL 编辑器，只支持预设模板渲染品牌+产品+批次信息
- published 状态即可用

交付物：API 可创建页面模板，固定模板可渲染品牌+产品+批次信息

#### A6：码解析与扫码事件（EPIC-04 子集 + EPIC-05b 子集）

范围：
- 码解析 `/c/{public_id}`：**公开路由，不走 JWT 鉴权，不走 `/api/v1/` 前缀**（详见 API 规范）
- 对接页面引擎返回页面内容
- **限流策略**：按 IP（100次/分钟）+ public_id（10次/分钟/码）+ 全局（10000次/秒）
- **Redis 缓存**：热门码解析结果 + 页面版本缓存，TTL 5 分钟
- scan_event **异步写入**（Redis Stream → arq worker）
- 首扫/重扫判断 + 环境解析（微信/浏览器/支付宝）
- **scan_token 防伪**：resolver 颁发短期 JWT，H5 事件写入需携带（详见第 7 节安全规范）
- 熔断降级：码解析异常时返回通用降级页面
- 消费者 H5 前端模块化渲染

交付物：手机浏览器/微信访问短链打开 H5 页面，重复扫码次数累计

#### A7：基础统计（EPIC-07 子集）

范围：
- 事件表追加写入（scan_events **先按月分区**，暂不加 tenant_id 子分区）
- 扫码次数、UV、首扫/重扫的最小统计 API（走汇总表）
- 定时聚合任务（arq）

交付物：API 返回扫码量、UV、首扫数、重扫数、码状态统计

### Beta：功能补齐（Wave B1 ~ B5）

#### B1：极简 Admin Shell（EPIC-08 子集）

范围：
- 管理后台前端基础框架（Next.js App Router + Ant Design）：登录、布局、路由
- 极简页面：产品列表、码批次列表、扫码统计
- 登录使用 JWT Bearer Token（阶段二再评估迁移到 HttpOnly Cookie + CSRF）

交付物：登录后台，查看产品、码批次、扫码统计

#### B2：页面模板与发布（EPIC-05a 补齐）

范围：
- config_json DSL：**JSON Schema 校验 + dsl_version 字段**
- 草稿、预览、发布、下线、回滚状态机
- **回滚策略**：创建新版本号，内容复制自目标版本
- **活动期切换**：页面模板支持 campaign_status 条件（预热/活动中/结束展示不同内容）
- EPIC-05c 行业模板（2-3 个：食品、农产品、礼盒）

交付物：后台编辑页面配置并发布，扫码看到新内容

#### B3：活动与权益（EPIC-06，含基础风控）

范围：
- 活动 CRUD + 权益 CRUD（4 种类型）
- 权益领取接口 + **双层幂等控制**：
  - 第一层：Redis 缓存（Idempotency-Key + TTL 24h）做体验优化
  - 第二层：数据库唯一约束做最终一致（详见第 7 节幂等规范）
- **基础风控**（阶段一最低限度）：
  - IP 维度限流
  - 设备频率限制（anonymous_id 维度）
  - 每人每活动领取上限
- 留资表单 + 私域跳转记录
- **活动法律声明**：rules_json 包含必填字段（参与条件/领取限制/有效期/免责说明/未成年人提示/客服联系方式），发布前自动校验
- **隐私政策页** + 授权管理

交付物：扫码 → 领券/留资/私域跳转，风控拦截有效，后台可查记录

#### B4：看板与导出（EPIC-07 补齐）

范围：
- 经营看板 API（走汇总表）+ 活动看板 API
- 数据导出（含 export_log：操作人/时间/范围/数量/IP/文件有效期）
- **归档策略**：超 6 个月原始事件归档到冷存储

交付物：测试活动数据在看板可见并能导出

#### B5：代运营工作台 + 管理后台完善（EPIC-08 补齐）

范围：
- 代运营工作台 API：客户列表、开通状态、待办任务、初始化进度
- 核心管理页面完善：活动中心、数据导出、操作日志、权限设置、合规设置
- 上线检查清单

交付物：代运营人员可通过管理后台查看客户进度

---

## 5. 阶段二详细设计——波次与交付物

### Wave 9：外码/内码双码（EPIC-09）

范围：
- CodeItem 扩展：outer/inner 配对模型
- 外码：扫码展示引流页（品牌、产品概览、引导刮开内码）
- 内码：验真 + 领奖 + 积分
- 配对码批量生成

交付物：外码扫码看引流页，内码扫码验真+领奖

### Wave 10：轻量验真与异常预警（EPIC-10）

范围：
- 首扫验真提示文案配置
- 同码多地扫码检测（**IP → 城市解析：MaxMind GeoLite2 离线库**）+ RiskAlert 生成
- 疑似复制码预警规则
- 风险冻结流程
- 消费者端异常提示页

交付物：异常扫码自动产生预警，后台可查

### Wave 11：渠道流向绑定（EPIC-11）

范围：
- Distributor、Region、Store 主数据 CRUD
- 码批次分配给经销商/区域
- 扫码 IP → 城市解析，与预期区域比对
- 跨区扫码线索记录

交付物：码段分配给经销商，跨区扫码产生窜货线索

### Wave 12：轻量会员与积分（EPIC-12）

范围：
- ConsumerProfile 增强：会员等级、标签
- PointAccount + PointTransaction 模型
- 积分规则：扫码、首扫、复购导入、活动行为
- 积分兑换权益
- 消费者 H5 积分页

交付物：消费者扫码积累积分，可兑换权益

### Wave 13：活动风控规则引擎（EPIC-13）

范围：
- RiskRule 模型：用户/手机号/设备/IP/地区/预算/时间/库存维度
- 规则评估服务：实时拦截 vs 预警
- 拦截记录与审计
- 后台风控规则配置

交付物：风控规则触发时自动拦截，后台可查拦截记录

### Wave 14：渠道风控看板（EPIC-14）

范围：
- 重复扫码热力图
- 跨区扫码地图
- 疑似窜货线索看板
- 风控数据导出

交付物：风控数据在看板可视化展示

### Wave 15：区域品牌/协会基础版（EPIC-15）

范围：
- 上级组织（RegionalOrg）与成员企业关系
- 统一页面模板（上级定义，成员使用）
- 授权产品管理
- 区域品牌汇总看板

交付物：区域品牌可管理成员企业，查看汇总数据

### Wave 16：外部成交导入与 GMV 归因（EPIC-16）

范围：
- 外部订单手动导入（CSV/Excel）
- 匹配规则：手机号/unionid/券码/code_id/tracking_id
- GMV 归因看板
- 外部成交事件记录

交付物：导入外部订单后可看到 GMV 归因数据

---

## 6. 阶段三详细设计——波次与交付物

### Wave 17：AI 资料识别与文案生成（EPIC-17）

范围：
- 文档解析服务（PDF/图片 → 结构化数据）
- 产品资料字段提取 + 人工确认工作流
- 页面文案生成（品牌故事、产品卖点）+ 草稿模式
- 页面结构建议（模块推荐）
- 活动方案生成

交付物：上传资料自动提取字段，AI 生成页面文案草稿

### Wave 18：外部权益连接器 L2-L4（EPIC-18）

范围：
- L2 券码池：外部券码批量导入、按规则一人一码发放
- L3 API 发券：有赞、微盟、微信支付商家券、自建商城连接器
- L4 订单回流：API/Webhook/联盟回调接收，匹配归因
- 连接器配置管理界面
- **连接器接口协议**：
  ```
  BaseConnector:
    - validate_config(config) -> bool
    - test_connection() -> bool
    - send_benefit(claim) -> ConnectorResult
    - check_status(external_id) -> ConnectorResult
    - handle_callback(payload) -> ConnectorResult
  ```

交付物：外部券码导入后可扫码领取，API 发券成功

### Wave 19：Webhook / Open API（EPIC-19）

范围：
- Webhook 端点管理：订阅事件类型、签名校验（HMAC-SHA256）、重试机制（指数退避）
- 事件推送：scan、claim、lead、risk_alert 等 10+ 事件类型
- **API Key 认证**：独立于 JWT，用于外部系统调用
- 外部 API Key 管理（创建/吊销/权限范围）
- OpenAPI 文档自动生成

交付物：外部系统可订阅 Webhook 事件，通过 API Key 调用 Open API

### Wave 20：CRM/ERP/商城集成（EPIC-20）

范围：
- 批量导入增强：产品/批次/经销商/码段分配
- ERP 进销存同步 API（出库流向）
- CRM 客户同步 API
- GTS/食链通追溯数据联动
- 会员系统积分同步

交付物：外部系统可通过 API 同步数据

### Wave 21：区域品牌高级能力与白标（EPIC-21）

范围：
- 统一码规则（区域品牌级码规则模板）
- 高级汇总看板（成员企业对比、区域分布）
- 客户自有域名配置 + HTTPS 证书管理
- 白标 H5（隐藏一码通品牌标识）

交付物：区域品牌可配置白标和自有域名

### Wave 22：高级多语言与自动切换（EPIC-22）

范围：
- 多语言模板管理（中/英/扩展）
- 浏览器语言自动检测与切换
- 翻译工作流（人工翻译 + AI 辅助）
- 多语言页面版本管理

交付物：H5 页面根据浏览器语言自动切换

### Wave 23：现金红包插件（EPIC-23）

范围：
- 红包活动规则（金额、预算、频率限制）
- **合规要求**：
  - 必须通过持牌第三方支付通道
  - KYC 实名认证
  - 单笔/单日限额
  - 交易记录保留 5 年以上
  - 大额交易报告机制
  - **前置条件：启动前完成合规咨询**
- 红包风控加强（身份验证、设备指纹、提现限制）

交付物：现金红包发放合规、风控拦截有效

---

## 7. 技术实现规范

### 数据库规范

- ORM：SQLAlchemy 2.0 声明式模型 + async session
- 迁移：Alembic（async env.py），每个 Epic 的模型变更独立迁移文件
- **主键**：UUID v7（时间排序，对 B-tree 友好），使用 uuid6 库
- 所有业务表必须包含：`id` (UUID v7)、`tenant_id` (UUID)、`created_at`、`updated_at`。**即使 tenant_id 可从父表推导也必须保留**（保证 RLS、索引、跨租户排查一致性）
- 软删除：用 `status` 字段而非 `deleted_at`
- 事件表：追加写入，不允许 UPDATE，用 `event_time`
- **强制参数化查询**：禁止原生 SQL 拼接，必须使用 ORM 参数化绑定
- 事件表分区策略：阶段一先按月分区（不加 tenant_id 子分区），等真实扫码量验证后再考虑子分区
- **迁移规范**：所有 migration 必须包含 downgrade；生产发布先兼容性 migration 再发代码再清理旧字段；大表变更避免锁表（先加 nullable → backfill → 再加约束/索引）
- **备份策略**：PostgreSQL 每日全量 + WAL/PITR；日备 14 天、周备 8 周、月备 12 月；每月至少一次恢复演练

### PostgreSQL RLS 规范

所有租户相关表启用 Row Level Security，采用事务级 `SET LOCAL` 模式：

```sql
-- 辅助函数：获取当前租户 ID
CREATE FUNCTION current_tenant_id() RETURNS uuid AS $$
  SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid;
$$ LANGUAGE sql STABLE;

-- 示例：为 products 表启用 RLS
ALTER TABLE products ENABLE ROW LEVEL SECURITY;
ALTER TABLE products FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_products
ON products
USING (tenant_id = current_tenant_id())
WITH CHECK (tenant_id = current_tenant_id());
```

```python
# Python 侧：事务级 tenant 设置，事务结束自动失效
@asynccontextmanager
async def tenant_session(tenant_id: UUID):
    async with async_sessionmaker() as session:
        async with session.begin():
            await session.execute(
                text("SET LOCAL app.tenant_id = :tenant_id"),
                {"tenant_id": str(tenant_id)},
            )
            yield session
```

RLS 测试必须覆盖 4 个场景：
1. 未设置 tenant：查不到任何业务数据
2. 设置 tenant A：查不到 tenant B 的数据
3. 插入 tenant B 数据：被 `WITH CHECK` 拦截
4. 平台管理员跨租户操作：走独立审计路径，不默认绕过 RLS

### API 规范

- 版本化：管理端 API 挂载在 `/api/v1/` 下
- **公开路由例外**：码解析 `/c/{public_id}` 不走 `/api/v1/` 前缀，不走 JWT 鉴权，独立限流
- 响应格式统一：
  ```json
  { "data": {}, "meta": { "total": 100, "page": 1 } }
  { "error": { "code": "TENANT_NOT_FOUND", "message": "..." } }
  ```
- 鉴权：管理端 API 用 JWT Bearer Token（阶段一），阶段二评估迁移到 HttpOnly Cookie + CSRF Token，阶段三增加 API Key 认证
- 幂等：权益领取等写操作要求 `Idempotency-Key` 请求头（详见幂等规范）
- 分页：`?page=1&page_size=20`，默认 20，最大 100
- **安全头**：所有响应包含 CSP、X-Frame-Options、X-Content-Type-Options、Strict-Transport-Security
- CORS：从登录/API 调试阶段就需配置，不只是 EPIC-04 起

### 认证与多租户

- JWT 双 token：access_token（15min）+ refresh_token（7d）
  - 签名算法：HS256（密钥 >= 256 位）
  - 密钥通过环境变量注入，支持 `kid` 标识和轮换（新 key 签发，旧 key 同时验签 7 天）
  - **refresh_token 存储在 Redis**，支持主动吊销
  - access_token 黑名单机制（至少用于管理员强踢场景）
  - **阶段一**：Bearer Token 模式（`Authorization: Bearer <token>`）
  - **阶段二评估**：迁移到 HttpOnly Secure Cookie + CSRF Token（`SameSite=Lax`，CSRF 通过 `X-CSRF-Token` header + double-submit cookie 校验）
- Tenant scope 注入：中间件从 JWT 提取 `tenant_id`，自动过滤所有查询
- 跨租户防护：service 层强制 `filter(Model.tenant_id == tenant_id)` + PostgreSQL RLS（双保险）
- 平台管理员：跨租户写操作需二次确认，独立审计日志（MFA 留到阶段二）
- 阶段二增强：区域品牌管理员可跨成员企业只读查看
- 阶段三增强：API Key 认证（Open API 调用场景）

### 个人信息保护

- **手机号**：AES-GCM 加密存储 + HMAC-SHA256 加盐哈希索引（服务端 pepper，非每用户独立盐——独立盐会阻碍按手机号查重/匹配）
- **微信 openid/unionid**：加密存储 + 哈希索引
- **密钥管理**：阶段一用环境变量注入；预留 KMS envelope encryption 接口（`kid` 标识每个密钥版本），阶段三上云时接入
- **consent_records 增强**：增加 `policy_version`（隐私政策版本）、`terms_version`（用户协议版本）字段，evidence_json 定义必填 schema
- **字段分级**：在 schema 层标注字段敏感级别（普通/个人信息/敏感），据此自动应用不同的加密和脱敏策略
- **导出审批**：包含个人信息的导出需审批流程，文件加水印，设置有效期自动清理
- **文件上传安全**：
  - 走后端签名 URL 上传，前端不直传任意路径
  - MIME 白名单校验 + 文件大小限制（20MB）
  - 图片重新编码去 EXIF、PDF 做页数/大小限制
  - 下载走短期 signed URL，不暴露永久公开地址

### 码生成规范

- public_id：10 位字母数字，**CSPRNG（secrets 模块）** + Base62 + Luhn 校验位 + 碰撞检测重试（约 60 bit 熵，配合限流足够防枚举）
- code_url：`https://qr.yimatong.cn/c/{public_id}`，本地开发 `http://localhost:3000/c/{public_id}`
- 批量生成：数据库批量 INSERT + async，1000 码 < 1 秒
- **防枚举策略**：
  - 未命中码也返回统一降级页，不暴露"码不存在/已冻结"的细节差异
  - 限流维度：IP、public_id、连续 miss 比率
  - 阶段二可选：增加 HMAC 签名校验（`/c/{public_id}.{sig}`），增强防伪能力
- 阶段二增强：外码/内码配对生成

### 幂等规范（双层保障）

**第一层：Redis 幂等缓存（体验优化）**

请求头 `Idempotency-Key` 由客户端生成（UUID v4），服务端 Redis 存储 + TTL 24h。
相同 key 返回第一次成功结果，非幂等重试返回 409 Conflict。

**第二层：数据库唯一约束（最终一致）**

```sql
-- 请求幂等表：绑定 tenant + actor + operation，防止 key 被复用到其他请求
CREATE TABLE idempotency_keys (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL,
  actor_key text NOT NULL,       -- consumer_id / anonymous_id / mobile_hash
  operation text NOT NULL,       -- 'benefit.claim'
  idempotency_key text NOT NULL,
  request_hash text NOT NULL,    -- 请求体哈希，防 key 被绑定到不同参数
  response_json jsonb,
  status text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  UNIQUE (tenant_id, actor_key, operation, idempotency_key)
);

-- 每人每活动每权益限领一次
CREATE UNIQUE INDEX uq_claim_consumer_once
ON benefit_claims (tenant_id, campaign_id, benefit_id, consumer_id)
WHERE consumer_id IS NOT NULL AND status IN ('success', 'pending');

-- 每个码每个权益只能领取一次
CREATE UNIQUE INDEX uq_claim_code_once
ON benefit_claims (tenant_id, campaign_id, benefit_id, code_item_id)
WHERE code_item_id IS NOT NULL AND status IN ('success', 'pending');
```

领取流程：先查 Redis → 再 INSERT idempotency_keys → 同一事务创建 claim 并扣库存。

### scan_token 防伪规范

码解析 `/c/{public_id}` 是公开入口，需防止事件伪造：

**颁发流程**：
1. 用户访问 `/c/{public_id}`
2. resolver 校验码状态、页面版本、限流
3. 生成短期 `scan_token`（JWT，有效期 15-30 分钟），包含 `tenant_id`、`code_item_id`、`page_version_id`、`anonymous_id`、`scope`
4. H5 后续事件、领取、留资请求都带 `X-Scan-Token` header

**校验逻辑**：
- token 未过期
- event 的 `code_item_id`/`page_version_id` 与 token 一致
- event type 在 scope 内
- 高频事件按 `sid + event_type` 限流

### Webhook 签名规范（阶段三）

签名内容：`timestamp + "." + nonce + "." + raw_body`，HMAC-SHA256。
校验：5 分钟窗口 + nonce 去重（Redis，TTL 5 分钟）。
阶段三实现时详见 EPIC-19。

### 页面引擎 DSL

```yaml
dsl_version: "1.0"
campaign_status_rules:   # 活动期切换
  preheat:
    modules: [...]
  active:
    modules: [...]
  ended:
    modules: [...]
modules:
  - type: product_card
    enabled: true
    props:
      show_origin: true
      show_batch: true
  - type: certificate_list
    enabled: true
  - type: benefit_claim
    enabled: true
    props:
      benefit_id: "uuid"
      require_auth: false
  - type: private_domain_entry
    enabled: true
    props:
      entry_type: wecom
      target_url: "..."
```

- **JSON Schema 校验**：每个 dsl_version 对应一个 JSON Schema，API 层严格校验
- **活动期切换**：campaign_status_rules 支持预热/活动中/结束展示不同模块
- **场景动态展示**（阶段二增强）：按地区、渠道、用户状态、首扫/重扫条件渲染
- 阶段三新增模块：多语言切换、AI 推荐模块

### 测试规范

- 单元测试：pytest + httpx AsyncClient，每个 service 方法至少 1 正常 + 1 异常
- 集成测试：testcontainers 验证数据库交互（CI/nightly），本地默认用 docker-compose + 事务回滚测试库
- API 测试：每个端点测试鉴权、参数校验、正常响应
- **覆盖率目标（底线）**：service > 60%，API > 50%
- **高风险路径强制要求**：认证/授权/RLS/权益领取/数据导出必须有集成测试
- **黄金链路 E2E**（5 条，必须持续通过）：
  1. **租户隔离**：创建 tenant A/B，A 创建产品，B 请求返回 404，DB 验证 RLS
  2. **产品到扫码页**：创建产品→生成码→激活→访问 `/c/{id}`→H5 显示产品信息→scan_events 写入
  3. **首扫/重扫**：第一次 `is_first_scan=true`，第二次 `scan_count=2`，H5 显示重扫提示
  4. **权益领取幂等**：同一 Idempotency-Key 重复请求返回同一结果，换 key 同一用户同一权益被唯一约束拦截，并发 10 次库存只扣 1
  5. **导出与审计**：管理员导出→生成任务→下载 signed URL→export_log 记录操作人/IP/范围
- 阶段二增强：风控规则单元测试、跨区比对集成测试、并发测试（权益库存、幂等、码状态）
- 阶段三增强：Webhook 签名校验测试、连接器契约测试、Playwright 覆盖 H5 和管理后台
- **Playwright 前端 E2E**：从 B1 开始覆盖管理后台登录、配置发布；从 B3 覆盖 H5 扫码领取、留资

### 本地开发环境

```yaml
# docker-compose.dev.yml —— 完整本地开发环境
# 仅用于本地开发，生产环境通过环境变量注入
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: yimatong
      POSTGRES_USER: yimatong
      POSTGRES_PASSWORD: yimatong_dev  # 仅限本地开发
    ports: ["5432:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U yimatong"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redisdata:/data

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minio
      MINIO_ROOT_PASSWORD: minio_dev_password
    ports: ["9000:9000", "9001:9001"]
    volumes:
      - miniodata:/data

  minio-init:
    image: minio/mc
    depends_on: [minio]
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 minio minio_dev_password &&
      mc mb -p local/yimatong-dev || true
      "

  migration:
    build: ./backend
    depends_on:
      postgres:
        condition: service_healthy
    command: ["alembic", "upgrade", "head"]
    env_file: .env.dev

  backend:
    build: ./backend
    depends_on: [postgres, redis, minio, migration]
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--reload"]
    ports: ["8000:8000"]
    env_file: .env.dev
    volumes:
      - ./backend:/app

  worker:
    build: ./backend
    depends_on: [backend, redis]
    command: ["python", "workers.py"]
    env_file: .env.dev
    volumes:
      - ./backend:/app

  admin:
    build: ./frontend/apps/admin
    command: ["pnpm", "dev", "--host", "0.0.0.0"]
    ports: ["3001:3000"]
    volumes:
      - ./frontend/apps/admin:/app

  h5:
    build: ./frontend/apps/h5
    command: ["pnpm", "dev", "--host", "0.0.0.0"]
    ports: ["3000:3000"]
    volumes:
      - ./frontend/apps/h5:/app

  mock-sms:
    image: node:22-alpine
    working_dir: /mock
    command: ["node", "sms-server.js"]
    ports: ["8101:8101"]
    volumes:
      - ./mocks:/mock

  mock-wechat:
    image: node:22-alpine
    working_dir: /mock
    command: ["node", "wechat-server.js"]
    ports: ["8102:8102"]
    volumes:
      - ./mocks:/mock

volumes:
  pgdata:
  redisdata:
  miniodata:
```

**Mock 服务说明**：
- `mock-sms`：`POST /sms/send` 返回固定验证码，`POST /sms/verify` 校验
- `mock-wechat`：OAuth 直接跳回 callback 返回 fake openid，JSAPI 签名返回固定值
- 通过环境变量切换：`SMS_PROVIDER=mock`、`WECHAT_PROVIDER=mock`

---

## 8. 管理后台前端完整页面规划

### 阶段一页面（Beta B1/B5 实现）

| 页面 | 路径 | 说明 |
|------|------|------|
| 登录 | /login | JWT 登录 |
| 首页经营看板 | /dashboard | 核心指标卡片 |
| 租户管理 | /platform/tenants | 平台管理员专用 |
| 品牌资料 | /brand/profile | 品牌 Logo、信息 |
| 产品列表/详情 | /products, /products/{id} | 产品/SKU/批次 CRUD |
| 码批次/导出 | /codes/batches | 码管理核心 |
| 扫码页编辑器 | /pages/{id}/edit | 页面 DSL 编辑 |
| 活动中心 | /campaigns | 活动/权益配置 |
| 数据导出 | /exports | 导出任务管理 |
| 操作日志 | /settings/audit-logs | 审计日志 |
| 权限设置 | /settings/roles | RBAC 管理 |
| 合规设置 | /settings/compliance | 隐私政策/授权管理 |

### 阶段二新增页面

| 页面 | 路径 | 说明 |
|------|------|------|
| 会员与积分 | /members | 会员管理、积分规则 |
| 渠道管理 | /channels | 经销商/区域/门店 |
| 风控看板 | /analytics/risk | 风控可视化 |
| 区域品牌看板 | /regional/dashboard | 区域品牌专用 |
| 成员企业 | /regional/members | 区域品牌成员管理 |
| 集成中心 | /integrations | 外部系统配置 |

### 阶段三新增页面

| 页面 | 路径 | 说明 |
|------|------|------|
| AI 助手 | /ai | AI 辅助配置入口 |
| Webhook 管理 | /settings/webhooks | Webhook 订阅配置 |
| Open API 文档 | /settings/api-docs | API Key 管理+文档 |
| 多语言管理 | /settings/i18n | 多语言模板配置 |

---

## 9. 消费者 H5 页面完整规划

### 阶段一 H5 页面

| 页面 | 说明 |
|------|------|
| 扫码解析页 | 校验码状态并跳转/渲染 |
| 产品信任页 | 品牌、产品、轻量溯源、权益入口 |
| 验真状态页 | 首扫、重扫、异常提示 |
| 检测报告页 | 报告图片、摘要、附件 |
| 权益领取页 | 优惠券、礼品、外部券 |
| 留资表单页 | 手机、地区、意向、隐私授权 |
| 私域跳转页 | 企微、公众号、小程序 |
| 电商跳转页 | 外部店铺链接 |
| 活动规则页 | 参与规则、限制、权益说明 |
| 隐私政策页 | 隐私政策、授权、撤回入口 |
| 异常客服页 | 查无此码、冻结、作废、疑似风险 |

### 阶段二 H5 新增

| 页面 | 说明 |
|------|------|
| 积分页 | 积分余额、兑换、记录 |
| 核销码页 | 线下核销凭证 |
| 双码引导页 | 外码引导刮开内码 |

### 阶段三 H5 新增

| 页面 | 说明 |
|------|------|
| 多语言自动切换 | 根据浏览器语言 |
| 现金红包页 | 红包领取、提现（仅 EPIC-23 客户） |

---

## 10. AI 协作开发工作流

### Git 分支策略

```
main                        # 稳定发布
└── dev                     # 主开发分支
    ├── epic/01-auth        # 阶段一 Alpha
    ├── epic/02-product
    ├── epic/03-code
    ├── epic/05a-page-backend
    ├── epic/04-resolver
    ├── epic/07-analytics
    ├── epic/08-admin       # 阶段一 Beta
    ├── epic/06-campaign
    ├── epic/09-dual-code   # 阶段二
    ...
```

- 每个 Epic 从 `dev` 创建短分支，完成后合并回 `dev` 并删除
- `main` 只从 `dev` 合入
- 每阶段完成时在 `main` 上打 tag（如 `v1.0.0`、`v2.0.0`、`v3.0.0`）
- 单人开发优先使用短分支 + 频繁合并，Epic 分支只保留里程碑意义

### 每个 Epic 的标准工作流

1. 设计细化：读取已有文档，补全 Epic 内的任务粒度
2. TDD 循环：先写测试用例，再实现代码
3. 增量验证：每完成一个 service/api 立即运行测试
4. Epic 验收：跑通该 Epic 的完整验收标准
5. 提交：清理临时文件，commit 到对应分支

### Claude Code 使用模式

- **骨架搭建**（Epic 开始时）：一次性生成 model + schema + router + test 骨架
- **TDD 驱动**（功能开发时）：先给测试描述 → 生成测试 → 实现功能 → 验证
- **子代理并行**（独立任务时）：同一 Epic 内的独立任务用 Agent 并行
- **代码审查**（功能点完成后）：用 code-reviewer 代理审查

### 每个 Epic 的上下文加载

1. 本文档中该 Epic 对应的波次范围和验收标准
2. `DATA_MODEL.md` 中相关实体
3. `API_DRAFT.md` 中相关端点
4. 已有代码中的 models/ 和 deps.py
5. 前一个 Epic 的关键接口
6. 黄金链路 E2E 测试的最新状态

### 依赖管理

- Python：用 uv 管理，pyproject.toml 锁版本，锁定 Python 3.12+、PostgreSQL 16、Redis 7
- 前端：pnpm workspace monorepo（`frontend/`），共享类型通过 `packages/shared` 内部引用
- 原则：每波次开始时统一安装，波次内不再新增
- arq 作为阶段一异步任务队列，迁移触发条件：任务堆积、延迟、可靠性需求超出 Redis 能力时

---

## 11. 跨阶段依赖关系

```text
EPIC-01（多租户）
  ├── EPIC-02（产品）── EPIC-03（码管理）
  │                         ├── EPIC-05a（页面引擎后端）
  │                         │       └── EPIC-04（码解析）+ EPIC-05b（H5渲染）
  │                         │               └── EPIC-06（活动权益+基础风控）
  │                         │                       └── EPIC-07（埋点看板）
  │                         ├── EPIC-09（双码）
  │                         └── EPIC-11（渠道流向）
  ├── EPIC-08（代运营+管理后台+行业模板）
  ├── EPIC-10（验真预警）← EPIC-03, 04, 07
  ├── EPIC-12（会员积分）← EPIC-04, 06
  ├── EPIC-13（风控引擎）← EPIC-06
  ├── EPIC-14（风控看板）← EPIC-10, 11
  ├── EPIC-15（区域品牌）← EPIC-01, 02, 05
  ├── EPIC-16（GMV 归因）← EPIC-04, 06, 07
  ├── EPIC-17（AI 辅助）← EPIC-02, 05, 06
  ├── EPIC-18（连接器）← EPIC-06
  ├── EPIC-19（Webhook）← EPIC-07
  ├── EPIC-20（CRM/ERP）← EPIC-02, 03, 11, 19
  ├── EPIC-21（白标）← EPIC-15
  ├── EPIC-22（多语言）← EPIC-05
  └── EPIC-23（红包）← EPIC-13
```

---

## 12. 外部依赖清单

| 依赖项 | 需要 Epic | 说明 | 方案 |
|--------|----------|------|------|
| IP 地理位置解析 | EPIC-04, 10, 11 | IP → 省/市映射 | MaxMind GeoLite2 离线库（免费） |
| 短信服务 | EPIC-06, 12 | 手机验证码 | 阿里云/腾讯云短信 SDK |
| 微信 JS-SDK | EPIC-04, 05, 06 | 环境检测、openid、分享 | weixin-js-sdk + 后端签名接口 |
| 微信公众号/开放平台 | EPIC-06, 12 | openid 获取、私域跳转 | 需提前注册公众号 |
| 二维码图片生成 | EPIC-03 | 码包导出 | Python qrcode + Pillow |
| Excel 处理 | EPIC-02, 03, 16 | 批量导入导出 | openpyxl |
| 对象存储 SDK | EPIC-02, 03, 07 | 文件上传存储 | boto3（兼容 MinIO） |
| CORS 中间件 | EPIC-01 起 | 前后端分离（登录/API 调试即需） | FastAPI CORSMiddleware（内置） |
| 定时任务框架 | EPIC-07, 10, 13 | 数据聚合、过期清理 | arq（基于 Redis，与异步任务共用） |
| PDF 生成（可选） | EPIC-05 | 检测报告导出 | WeasyPrint |
| 图像处理 | EPIC-02 | 图片裁剪压缩 | Pillow |
| Redis 客户端 | EPIC-01 起 | 缓存、限流、队列 | redis[hiredis]（5.x 内置 async） |
| 域名与 HTTPS | EPIC-04 | 短链服务 | 本地 localhost，生产需域名+证书 |

---

## 附录 A：阶段一 Alpha/Beta 分界线

### Alpha 完成标准（内部可演示）

- 通过 CLI seed 创建租户、产品、码
- 手机扫码能打开真实 H5 页面
- 后台 API 能看到扫码记录和基础统计
- RLS 隔离测试全部通过
- 5 条黄金链路 E2E 全部通过（其中权益领取相关在 Beta 才有）

### Beta 完成标准（可交付试点客户）

- 管理后台可登录并完成基本配置
- 可通过后台创建活动、配置权益
- 消费者可扫码领取权益、提交留资、点击私域入口
- 经营看板和活动看板可见数据
- 数据可导出
- 代运营人员可通过管理后台查看客户进度

---

## 附录 B：resolver/H5 生产部署参考架构

```
用户扫码
  ↓
CDN / WAF / Rate Limit
  ↓
resolver API: qr.yimatong.cn/c/{public_id}
  ↓
Redis cache（热门码 + 页面快照）
  ↓ miss
PostgreSQL read replica
  ↓
返回 H5 URL 或 SSR/静态页面
  ↓
H5 静态资源走 CDN
  ↓
事件写入 Redis Stream / Queue
  ↓
worker 落 PostgreSQL
```

关键策略：
- H5 静态资源全部 CDN
- 页面发布后生成 `page_snapshot`，resolver 优先读 Redis snapshot
- resolver 超时目标：P95 < 200ms
- DB 异常时返回通用降级页，不影响消费者基础体验
- 事件写入失败不阻塞页面打开，先入队，队列失败记本地 fallback log
- 热门码缓存 key：`resolve:{public_id}`，TTL 5-15 分钟
- 页面版本缓存 key：`page:{page_version_id}`，发布/下线时主动失效

降级页规则：
- 码不存在：通用客服页
- 码冻结/作废：风险提示页
- DB/Redis 故障：品牌通用页 + 稍后重试
- 页面配置异常：默认产品信任页模板

早期生产形态：单机 Docker + 托管 PostgreSQL/Redis + 对象存储 + CDN。RPO 24h 内，RTO 4h 内。
