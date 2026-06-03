---
status: active
last_verified: 2026-06-03
accuracy: high
---

# 一码通产品资料包

版本：v2.0 独立闭环版
最后验证：2026-06-03
格式：Markdown / YAML / JSON / PPTX
定位：独立可销售、独立部署、独立闭环的包装扫码增长 SaaS

## 如何阅读本文档

> **文档可信度看板**：参见 [`MANIFEST.md`](MANIFEST.md)——每个文档的治理状态、最后验证日期和准确度一览。

每个文档头部包含元数据块：
```yaml
---
status: active | stale | archived    # 文档生命周期状态
last_verified: YYYY-MM-DD            # 上次与代码比对日期
accuracy: high | medium | low        # 内容准确度评估
---
```

**阅读原则**：优先看 `status: active` 且 `accuracy: high` 的文档；`archived` 的文档仅作历史参考。

## 一句话定义

一码通是一款面向食品、农产品及消费品品牌方的**包装扫码增长 SaaS**，帮助企业把产品包装上的二维码升级为集轻量溯源、品牌展示、优惠券权益、私域连接、复购转化、渠道归因、轻量验真和防窜预警于一体的数字化入口。

## 核心前提

- 一码通不是 GTS 的附属模块。
- 一码通不以食链通、GTS 或任何既有溯源系统作为使用前提。
- 一码通可以独立销售、独立开通、独立生成二维码、独立承接扫码页面、独立完成营销转化和数据分析闭环。
- 在客户已有食链通 / GTS / ERP / 进销存 / 商城 / CRM 时，一码通可以作为独立产品进行数据联动。

## 资料包目录

```text
01_product/
  PRODUCT_BRIEF.md                产品一页纸
  PRD.md                          完整 PRD
  ROADMAP.md                      研发阶段与产品路线图
  INFORMATION_ARCHITECTURE.md     信息架构
  USER_JOURNEYS.md                用户旅程与核心流程
  BUSINESS_MODEL_PRICING.md       商业模式与计费
  COMPETITIVE_POSITIONING.md      市场定位与竞品分析

02_tech/
  ARCHITECTURE.md                 技术架构（已更新 2026-06-03）
  DATA_MODEL.md                   数据模型（已重写，覆盖 69 个模型）
  API_DRAFT.md                    API 参考（已重写，覆盖 250+ 路由）
  PAGE_LIST.md                    页面清单
  PERMISSION_MATRIX.md            权限矩阵
  EVENT_TRACKING.md               埋点事件字典
  SECURITY_COMPLIANCE.md          安全、合规、风控
  TASKS.md                        ⚠️ 已归档（阶段一/二任务已完成）
  INTEGRATION_PLAYBOOK.md         外部系统与电商权益集成方案
  EPIC-23_IMPLEMENTATION_PLAN.md  现金红包 Epic 实施计划

03_delivery/
  IMPLEMENTATION_PLAYBOOK.md      交付实施手册
  OPERATION_PLAYBOOK.md           代运营与客户成功手册
  GO_LIVE_CHECKLIST.md            上线验收清单
  enterprise-wechat-setup.md      企微配置指南

04_marketing_sales/
  SOLUTION_DECK_SCRIPT.md         PPT 脚本与讲稿
  SALES_PLAYBOOK.md               销售拜访话术
  CUSTOMER_FAQ.md                 客户常见问题
  PRICING_TEMPLATE.md             报价方案模板
  ONE_PAGER.md                    面向客户的一页纸
  WEBSITE_COPY.md                 官网/落地页文案草案
  yimatong_solution_deck.pptx     可编辑营销 PPT

05_samples/
  page_config_example.yaml        扫码页配置 DSL 示例
  api_openapi_skeleton.yaml       OpenAPI 骨架
  sample_event_schema.json        埋点事件 schema 示例
  sample_tenant_config.yaml       租户配置示例
  sample_rights_connector.yaml    外部权益连接器示例

superpowers/                      开发工作产物（Claude Code 生成）
  specs/                          设计规格（4 个）
  plans/                          实施计划（9 个，已完成 5 个）

REFERENCES.md                     外部资料与事实依据
MANIFEST.md                       📋 文档治理索引（状态看板）
```

## 使用建议

- 给产品团队：优先阅读 `01_product/PRD.md`、`01_product/ROADMAP.md`。
- 给研发团队 / Claude Code：优先阅读 `02_tech/ARCHITECTURE.md`、`02_tech/DATA_MODEL.md`、`02_tech/API_DRAFT.md`。~~`TASKS.md` 已归档~~。
- 给销售 / BD：优先阅读 `04_marketing_sales/yimatong_solution_deck.pptx`、`04_marketing_sales/SALES_PLAYBOOK.md`、`04_marketing_sales/CUSTOMER_FAQ.md`。
- 给交付 / 代运营：优先阅读 `03_delivery/IMPLEMENTATION_PLAYBOOK.md`、`03_delivery/OPERATION_PLAYBOOK.md`、`03_delivery/GO_LIVE_CHECKLIST.md`。

## 文档治理规则

- 每个文档头部含治理元数据（status / last_verified / accuracy）
- 修改涉及 API/数据模型/架构时，顺手更新对应文档的 `last_verified`
- 完整治理索引见 [`MANIFEST.md`](MANIFEST.md)
