# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

一码通（yimatong）是一款面向食品、农产品及消费品品牌方的**包装扫码增长 SaaS**。项目当前处于从产品设计转入开发的阶段，`docs/` 目录包含完整的产品资料包和开发策略设计。

一码通是独立产品，不依赖食链通、GTS 或任何既有溯源系统，可以独立销售、独立部署。

## 项目结构

```
docs/
├── 01_product/          产品定义：PRD、路线图、信息架构、用户旅程、竞品分析
├── 02_tech/             技术规格：架构、数据模型、API 草案、任务拆解
├── 03_delivery/         交付实施：实施手册、代运营手册、上线清单
├── 04_marketing_sales/  销售物料：PPT 脚本、话术、FAQ、报价模板
├── 05_samples/          配置示例：页面 DSL、租户配置、API 骨架、事件 schema
└── superpowers/specs/   开发策略设计文档（最新版为 v1.2）
```

计划中的代码结构：

```
backend/                  FastAPI 后端（四层：models → schemas → services → api）
frontend/                 前端 pnpm workspace monorepo
  apps/admin/             管理后台（Next.js App Router + Ant Design）
  apps/h5/                消费者扫码页（Next.js App Router + Tailwind + Headless UI）
  packages/shared/        共享 TypeScript 类型
docker-compose.dev.yml    本地开发环境（postgres + redis + minio + mock 服务）
```

## 技术栈（已确定）

| 层 | 选型 |
|---|---|
| 后端 API | FastAPI（Python 3.12+，uv 管理） |
| 数据库 | PostgreSQL 16（SQLAlchemy 2.0 async，Alembic 迁移） |
| 缓存/队列 | Redis 7（redis[hiredis]，arq 异步任务） |
| 对象存储 | MinIO（boto3 兼容，本地）/ S3 兼容（生产） |
| 管理后台 | Next.js App Router + Ant Design |
| 消费者 H5 | Next.js App Router + Tailwind + Headless UI |
| 前端管理 | pnpm workspace monorepo |
| 测试 | pytest（后端）+ Playwright（前端 E2E） |

## 核心架构要点

- **多租户 SaaS**：共享数据库 + `tenant_id` 强隔离（应用层过滤 + PostgreSQL RLS 双保险），所有业务表必须包含 `tenant_id`（即使可从父表推导也必须保留）
- **RLS 规范**：`SET LOCAL app.tenant_id` 事务级设置，`current_tenant_id()` 辅助函数，4 场景必测
- **前后端分离**：消费者 H5 与管理后台分离，pnpm workspace 管理
- **码解析服务独立**：`/c/{public_id}` 公开路由，不走 `/api/v1/` 前缀和 JWT 鉴权，独立限流
- **页面引擎模块化**：模板配置可版本化，支持草稿/预览/发布/下线/回滚
- **事件驱动埋点**：事件表追加写入（scan_events 按月分区），不可频繁更新
- **可插拔连接器**：权益、风控、外部集成采用连接器设计
- **双层幂等控制**：Redis 幂等缓存（体验优化）+ 数据库唯一约束（最终一致）
- **scan_token 防伪**：resolver 颁发短期 JWT，H5 事件/领取请求需携带
- **认证方案**：阶段一 Bearer Token，阶段二评估迁移到 HttpOnly Cookie + CSRF

## 数据模型核心实体

Tenant → Organization → Account → Role → Permission（多租户 RBAC）
Tenant → Brand → Product → SKU → ProductionBatch（产品体系）
Tenant → CodeBatch → CodeItem（码管理，public_id 10 位 Base62 + Luhn）
Tenant → PageTemplate → PageVersion（页面引擎，config_json 为 DSL）
Tenant → Campaign → Benefit → BenefitClaim（活动与权益）
ConsumerProfile + ScanEvent + RiskAlert（消费者与风控）

## 开发阶段

三阶段研发路线，当前从阶段一 Alpha 开始：

- **阶段一 Alpha**（最小可验证闭环，Wave A1-A7）：工程底座 → 租户认证 → 产品资料 → 码生成 → 固定模板页面 → 码解析扫码事件 → 基础统计。用 CLI seed 配数据，无需管理后台
- **阶段一 Beta**（功能补齐，Wave B1-B5）：极简 Admin Shell → 页面 DSL 编辑 → 活动权益 → 看板导出 → 代运营工作台
- **阶段二**（增强闭环）：双码验真、防窜货、会员积分、活动风控、渠道风控看板、区域品牌
- **阶段三**（规模化）：AI 辅助、外部权益连接器 L2-L4、Webhook/Open API、CRM/ERP 集成

开发策略设计详见 `docs/superpowers/specs/2026-05-27-development-strategy-design.md`。

## 关键设计约束

- 码的 `public_id`：10 位 Base62（CSPRNG）+ Luhn 校验位，不可自增暴露
- 短链格式：`https://qr.yimatong.cn/c/{public_id}`，永久可解析
- 消费者个人信息存最小必要字段，手机号 AES-GCM 加密 + HMAC-SHA256 哈希索引
- 码、页面、活动、权益必须支持版本化或历史记录
- 所有导出行为记录 `export_log`
- 外部系统数据保留 `external_id` 和 `source_system`
- 主键使用 UUID v7（时间排序），使用 uuid6 库

## 关键参考文件

| 场景 | 文件 |
|---|---|
| 开发策略设计（最新） | `docs/superpowers/specs/2026-05-27-development-strategy-design.md` |
| 实施任务 | `docs/02_tech/TASKS.md` |
| 技术架构 | `docs/02_tech/ARCHITECTURE.md` |
| 数据模型 | `docs/02_tech/DATA_MODEL.md` |
| API 设计 | `docs/02_tech/API_DRAFT.md` |
| 页面配置 DSL | `docs/05_samples/page_config_example.yaml` |
| 租户配置 | `docs/05_samples/sample_tenant_config.yaml` |
| 完整 PRD | `docs/01_product/PRD.md` |
| 路线图 | `docs/01_product/ROADMAP.md` |
