---
status: active
last_verified: 2026-07-30
accuracy: high
---

# 文档治理索引

> 本索引反映所有文档的治理状态。每个文档头部有元数据（status / last_verified / accuracy），此处汇总一览。
>
> **状态说明**：🟢 active = 内容准确 | 🟡 stale = 已知过时待更新 | 🔴 archived = 已归档历史参考
>
> **准确度说明**：⬆ high = 与代码一致 | ➡ medium = 部分偏差 | ⬇ low = 显著过时

## 01_product — 产品文档

| 文档                          | 状态      | 最后验证   | 准确度 | 备注                                                           |
| ----------------------------- | --------- | ---------- | ------ | -------------------------------------------------------------- |
| PRD.md                        | 🟢 active | 2026-06-03 | ⬆ high | 产品需求文档，核心内容稳定                                     |
| ROADMAP.md                    | 🟢 active | 2026-06-03 | ⬆ high | 三阶段路线图                                                   |
| PRODUCT_BRIEF.md              | 🟢 active | 2026-06-03 | ⬆ high | 产品简介                                                       |
| INFORMATION_ARCHITECTURE.md   | 🟢 active | 2026-06-03 | ⬆ high | 信息架构                                                       |
| USER_JOURNEYS.md              | 🟢 active | 2026-06-03 | ⬆ high | 用户旅程                                                       |
| BUSINESS_MODEL_PRICING.md     | 🟢 active | 2026-06-03 | ⬆ high | 商业模式与定价                                                 |
| COMPETITIVE_POSITIONING.md    | 🟢 active | 2026-06-03 | ⬆ high | 竞品分析                                                       |
| THREE_LINE_PRODUCT_SPEC.md    | 🟢 active | 2026-07-26 | ⬆ high | **新增**：基准客户场景三线产品闭环规格（yimatong-zgb1 父规格） |
| FIRST_SPEC_PACKAGE.md         | 🟢 active | 2026-07-26 | ⬆ high | **新增**：首个规格包，定义四纵向切片与执行顺序                 |
| BASELINE_ACCEPTANCE_MATRIX.md | 🟢 active | 2026-07-26 | ⬆ high | **新增**：基准数据与三线独立验收矩阵                           |
| VERIFICATION_STATE_MODEL.md   | 🟢 active | 2026-07-26 | ⬆ high | **新增**：轻量验真状态模型（首次/重复验证、异常信号）          |
| GROWTH_CONVERSION_MODEL.md    | 🟢 active | 2026-07-26 | ⬆ high | **新增**：增长转化事件与七层漏斗模型                           |
| DIVERSION_CLUE_MODEL.md       | 🟢 active | 2026-07-26 | ⬆ high | **新增**：窜货线索证据与处置模型                               |

## 02_tech — 技术文档

| 文档                           | 状态        | 最后验证   | 准确度   | 备注                                               |
| ------------------------------ | ----------- | ---------- | -------- | -------------------------------------------------- |
| API_DRAFT.md                   | 🟢 active   | 2026-06-03 | ⬆ high   | **已全面重写**，覆盖 250+ 路由                     |
| DATA_MODEL.md                  | 🟢 active   | 2026-06-03 | ⬆ high   | **已全面重写**，覆盖 69 个模型                     |
| ARCHITECTURE.md                | 🟢 active   | 2026-06-03 | ⬆ high   | **已更新**，补充平台管理、代理授权等模块           |
| TASKS.md                       | 🔴 archived | 2026-06-03 | ⬇ low    | **已归档**，阶段一/二任务已完成                    |
| EPIC-23_IMPLEMENTATION_PLAN.md | 🟢 active   | 2026-06-03 | ⬆ high   | 现金红包 Epic 实施计划                             |
| EVENT_TRACKING.md              | 🟢 active   | 2026-06-03 | ⬆ high   | 事件埋点设计                                       |
| INTEGRATION_PLAYBOOK.md        | 🟢 active   | 2026-06-03 | ⬆ high   | 集成手册                                           |
| PAGE_LIST.md                   | 🟡 stale    | 2026-06-03 | ➡ medium | 页面列表待与前端对齐                               |
| PERMISSION_MATRIX.md           | 🟢 active   | 2026-06-03 | ⬆ high   | 权限矩阵                                           |
| SECURITY_COMPLIANCE.md         | 🟢 active   | 2026-06-03 | ⬆ high   | 安全合规                                           |
| design-system/                 | 🟢 active   | 2026-07-30 | ⬆ high   | **新增**，设计体系规范（tokens/组件/H5 槽位/a11y） |

## 03_delivery — 交付文档

| 文档                       | 状态      | 最后验证   | 准确度 | 备注         |
| -------------------------- | --------- | ---------- | ------ | ------------ |
| GO_LIVE_CHECKLIST.md       | 🟢 active | 2026-06-03 | ⬆ high | 上线检查清单 |
| IMPLEMENTATION_PLAYBOOK.md | 🟢 active | 2026-06-03 | ⬆ high | 实施手册     |
| OPERATION_PLAYBOOK.md      | 🟢 active | 2026-06-03 | ⬆ high | 运营手册     |
| enterprise-wechat-setup.md | 🟢 active | 2026-06-03 | ⬆ high | 企微配置指南 |

## 04_marketing_sales — 营销销售文档

| 文档                    | 状态      | 最后验证   | 准确度 | 备注         |
| ----------------------- | --------- | ---------- | ------ | ------------ |
| CUSTOMER_FAQ.md         | 🟢 active | 2026-06-03 | ⬆ high | 客户 FAQ     |
| ONE_PAGER.md            | 🟢 active | 2026-06-03 | ⬆ high | 产品一页纸   |
| PRICING_TEMPLATE.md     | 🟢 active | 2026-06-03 | ⬆ high | 报价模板     |
| SALES_PLAYBOOK.md       | 🟢 active | 2026-06-03 | ⬆ high | 销售手册     |
| SOLUTION_DECK_SCRIPT.md | 🟢 active | 2026-06-03 | ⬆ high | 方案演示脚本 |
| WEBSITE_COPY.md         | 🟢 active | 2026-06-03 | ⬆ high | 网站文案     |

## 05_samples — 配置示例

| 文档                         | 状态      | 最后验证   | 准确度 | 备注              |
| ---------------------------- | --------- | ---------- | ------ | ----------------- |
| api_openapi_skeleton.yaml    | 🟢 active | 2026-06-03 | ⬆ high | OpenAPI 骨架      |
| page_config_example.yaml     | 🟢 active | 2026-06-03 | ⬆ high | 页面配置 DSL 示例 |
| sample_rights_connector.yaml | 🟢 active | 2026-06-03 | ⬆ high | 权益连接器示例    |
| sample_tenant_config.yaml    | 🟢 active | 2026-06-03 | ⬆ high | 租户配置示例      |

## superpowers — 开发工作产物

### specs/ 设计规格

| 文档                                        | 状态         | 完成度 | 备注               |
| ------------------------------------------- | ------------ | ------ | ------------------ |
| 2026-05-27-development-strategy-design.md   | ⏸ Partial    | —      | 开发策略，部分执行 |
| 2026-05-28-aes-gcm-encryption-design.md     | ✅ Completed | 已实现 | AES-GCM 加密       |
| 2026-05-30-page-dsl-visual-editor-design.md | ✅ Completed | 已实现 | 页面 DSL 编辑器    |
| 2026-06-02-demo-seed-data-design.md         | ✅ Completed | 已实现 | 演示数据           |

### plans/ 实施计划

| 文档                                        | 状态         | 完成度 | 备注                                                            |
| ------------------------------------------- | ------------ | ------ | --------------------------------------------------------------- |
| 2026-05-28-aes-gcm-encryption.md            | ✅ Completed | 已实现 | AES-GCM 加密                                                    |
| 2026-05-30-page-dsl-visual-editor.md        | ✅ Completed | 已实现 | 页面 DSL 编辑器                                                 |
| 2026-06-01-channel-region-closed-loop.md    | ✅ Completed | 已实现 | 渠道闭环（store toggle + diversion notifications）              |
| 2026-06-02-agency-ux-improvements.md        | ✅ Completed | 已实现 | 代运营 UX 改进                                                  |
| 2026-06-02-agency-workbench-optimization.md | ✅ Completed | 已实现 | 代运营工作台优化                                                |
| 2026-06-02-dashboard-ux-improvements.md     | ✅ Completed | 已实现 | 工作台 UX 改进（refresh + comparison + row click + SSE banner） |
| 2026-06-02-demo-seed-data.md                | ✅ Completed | 已实现 | 演示数据增强                                                    |
| 2026-06-02-org-account-ux-improvements.md   | ✅ Completed | 已实现 | 组织账户 UX 改进                                                |
| 2026-06-02-scan-stats-ux-improvements.md    | ✅ Completed | 已实现 | 扫码统计 UX 改进（layout + loading + SSE tests）                |

## agents — 工程 Skill 配置

| 文档             | 状态      | 最后验证   | 准确度 | 备注                            |
| ---------------- | --------- | ---------- | ------ | ------------------------------- |
| issue-tracker.md | 🟢 active | 2026-07-26 | ⬆ high | Beads issue tracker 操作约定    |
| triage-labels.md | 🟢 active | 2026-07-26 | ⬆ high | 工程 skill triage 标签映射      |
| domain.md        | 🟢 active | 2026-07-26 | ⬆ high | single-context 领域文档消费规则 |

## evolution — 产品进化扫描

| 文档                             | 状态      | 最后验证   | 准确度 | 备注                                       |
| -------------------------------- | --------- | ---------- | ------ | ------------------------------------------ |
| improvement-report-2026-07-26.md | 🟢 active | 2026-07-26 | ⬆ high | **新增**：EV-20260726-01 改进报告          |
| radar.md                         | 🟢 active | 2026-07-26 | ⬆ high | **新增**：产品进化雷达（9 项主动改进方向） |

## research — 产品研究笔记

| 文档                                 | 状态      | 最后验证   | 准确度 | 备注                                       |
| ------------------------------------ | --------- | ---------- | ------ | ------------------------------------------ |
| current-three-line-coverage.md       | 🟢 active | 2026-07-26 | ⬆ high | **新增**：三线能力现状核验                 |
| qr-verification-diversion-signals.md | 🟢 active | 2026-07-26 | ⬆ high | **新增**：轻量验真与跨区判定的可靠信号边界 |
| wecom-confirmed-conversion.md        | 🟢 active | 2026-07-26 | ⬆ high | **新增**：企业微信确认加企微的官方能力边界 |

## 根级文件

| 文档          | 状态      | 最后验证   | 准确度 | 备注                                     |
| ------------- | --------- | ---------- | ------ | ---------------------------------------- |
| README.md     | 🟢 active | 2026-06-03 | ⬆ high | 产品资料包入口                           |
| REFERENCES.md | 🟢 active | 2026-06-03 | ⬆ high | 外部参考资料                             |
| CONTEXT.md    | 🟢 active | 2026-07-26 | ⬆ high | **新增**：一码通领域语言（统一业务术语） |
| MANIFEST.md   | 🟢 active | 2026-07-26 | ⬆ high | 本文件                                   |
