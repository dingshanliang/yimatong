# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

一码通（yimatong）是一款面向食品、农产品及消费品品牌方的**包装扫码增长 SaaS**。项目当前处于产品设计/规划阶段，`docs/` 目录包含完整的产品资料包，尚无源代码。

一码通是独立产品，不依赖食链通、GTS 或任何既有溯源系统，可以独立销售、独立部署。

## 项目结构

```
docs/
├── 01_product/          产品定义：PRD、路线图、信息架构、用户旅程、竞品分析
├── 02_tech/             技术规格：架构、数据模型、API 草案、任务拆解
├── 03_delivery/         交付实施：实施手册、代运营手册、上线清单
├── 04_marketing_sales/  销售物料：PPT 脚本、话术、FAQ、报价模板
└── 05_samples/          配置示例：页面 DSL、租户配置、API 骨架、事件 schema
```

## 技术栈（规划中）

| 层 | 推荐 |
|---|---|
| 管理后台 | React / Next.js |
| 消费者 H5 | Next.js / React SSR |
| 后端 API | FastAPI / Node.js NestJS |
| 数据库 | PostgreSQL |
| 缓存 | Redis |
| 队列 | Redis Queue / RabbitMQ / Kafka |
| 对象存储 | S3 兼容 / 阿里云 OSS / 腾讯云 COS |

## 核心架构要点

- **多租户 SaaS**：共享数据库 + `tenant_id` 强隔离，所有业务表必须包含 `tenant_id`
- **前后端分离**：消费者 H5 与管理后台分离
- **码解析服务独立**：保证高可用和低延迟，静态资源走 CDN
- **页面引擎模块化**：模板配置可版本化，支持草稿/预览/发布/下线/回滚
- **事件驱动埋点**：事件表采用追加写入，不可频繁更新
- **可插拔连接器**：权益、风控、外部集成采用连接器设计
- **幂等控制**：权益领取必须有幂等键（`Idempotency-Key`）

## 数据模型核心实体

Tenant → Organization → Account → Role → Permission（多租户 RBAC）
Tenant → Brand → Product → SKU → ProductionBatch（产品体系）
Tenant → CodeBatch → CodeItem（码管理，public_id 不可枚举）
Tenant → PageTemplate → PageVersion（页面引擎，config_json 为 DSL）
Tenant → Campaign → Benefit → BenefitClaim（活动与权益）
ConsumerProfile + ScanEvent + RiskAlert（消费者与风控）

## 开发阶段

三阶段研发路线，当前从阶段一开始：

- **阶段一**（核心闭环）：多租户认证、产品资料库、码管理、码解析服务、页面引擎、活动权益、埋点看板、代运营工作台
- **阶段二**（增强闭环）：双码验真、防窜货、会员积分、活动风控、渠道风控看板、区域品牌
- **阶段三**（规模化）：AI 辅助、外部权益连接器 L2-L4、Webhook/Open API、CRM/ERP 集成

阶段一对应 EPIC-01 到 EPIC-08，详见 `docs/02_tech/TASKS.md`。

## 关键设计约束

- 码的 `public_id` 不能自增暴露，必须支持签名或校验位
- 短链格式：`https://qr.yimatong.cn/c/{public_id}`，永久可解析
- 消费者个人信息存最小必要字段，手机号加密或哈希索引
- 码、页面、活动、权益必须支持版本化或历史记录
- 所有导出行为记录 `export_log`
- 外部系统数据保留 `external_id` 和 `source_system`

## 关键参考文件

| 场景 | 文件 |
|---|---|
| 实施任务 | `docs/02_tech/TASKS.md` |
| 技术架构 | `docs/02_tech/ARCHITECTURE.md` |
| 数据模型 | `docs/02_tech/DATA_MODEL.md` |
| API 设计 | `docs/02_tech/API_DRAFT.md` |
| 页面配置 DSL | `docs/05_samples/page_config_example.yaml` |
| 租户配置 | `docs/05_samples/sample_tenant_config.yaml` |
| 完整 PRD | `docs/01_product/PRD.md` |
| 路线图 | `docs/01_product/ROADMAP.md` |
