---
status: active
last_verified: 2026-07-30
accuracy: high
---

# 三端旧页面盘点：token 采用度与硬编码现状清单

## 方法说明

- 扫描范围：`frontend/apps/admin`、`frontend/apps/platform`、`frontend/apps/h5` 三端所有 `page.tsx`。
- 统计粒度：每个页面自身及其同目录（含 `_components` 子目录）下的 `.tsx/.ts/.css` 文件。
- Token 采用判定：出现 `@yimatong/design-tokens` import、`theme.css` / `design-tokens/theme` 引入、或 `var(--ymt-` 即视为已/部分接入；无上述引用记为未接入。
- 硬编码计数：色值 `#RRGGBB[AA]`、`rgb(` / `rgba(`，以及 Tailwind 任意值 `[#...]`、`[NNpx]` 的命中次数合计。
- 核心业务流程：码/批次/活动/权益/会员/扫码 H5/红包/商品/风控/渠道/页面引擎等主链路判为是，其余为否。

## 汇总统计

- 总页面数：**63**

### 各端 token 采用度分布

| 端       | 已接入 | 部分 | 未接入 | 合计 |
| -------- | ------ | ---- | ------ | ---- |
| admin    | 0      | 1    | 47     | 48   |
| platform | 0      | 0    | 11     | 11   |
| h5       | 1      | 0    | 3      | 4    |

### 硬编码最严重的 10 个页面

| 排名 | 端       | 路由          | 硬编码数量 | 功能域        | 是否核心 |
| ---- | -------- | ------------- | ---------- | ------------- | -------- |
| 1    | admin    | /page-preview | 37         | 页面引擎      | 是       |
| 2    | admin    | /benefits     | 11         | 权益          | 是       |
| 3    | platform | /analytics    | 9          | 数据分析      | 否       |
| 4    | platform | /             | 9          | 首页          | 否       |
| 5    | platform | /health       | 7          | 客户健康度    | 否       |
| 6    | platform | /plans        | 7          | 套餐管理      | 否       |
| 7    | admin    | /risk-center  | 5          | 风控          | 是       |
| 8    | admin    | /agency       | 4          | 经销商/代理   | 否       |
| 9    | admin    | /imports      | 4          | 集成/导入导出 | 否       |
| 10   | platform | /login        | 4          | 认证          | 否       |

## 明细表

| 端       | 路由                 | 功能域          | 是否核心 | token 采用度 | design-tokens import | theme.css 引入 | var(--ymt-* | 硬编码数量 | 菜单层级            |
| -------- | -------------------- | --------------- | -------- | ------------ | -------------------- | -------------- | ----------- | ---------- | ------------------- |
| admin    | /login               | 认证            | 否       | 未接入       | 0                    | 0              | 0           | 1          | N/A                 |
| admin    | /reset-password      | 认证            | 否       | 未接入       | 0                    | 0              | 0           | 3          | N/A                 |
| admin    | /accounts            | 渠道            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（渠道）        |
| admin    | /agency              | 经销商/代理     | 否       | 未接入       | 0                    | 0              | 0           | 4          | 一级（经销商/代理） |
| admin    | /ai-assistant        | AI 助手         | 否       | 未接入       | 0                    | 0              | 0           | 3          | 一级（AI 助手）     |
| admin    | /analytics           | 数据分析        | 否       | 未接入       | 0                    | 0              | 0           | 0          | 一级（数据分析）    |
| admin    | /anti-diversion      | 风控            | 是       | 未接入       | 0                    | 0              | 0           | 1          | 一级（防窜货）      |
| admin    | /batches             | 批次            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（商品目录）    |
| admin    | /benefits            | 权益            | 是       | 未接入       | 0                    | 0              | 0           | 11         | 二级（增长运营）    |
| admin    | /brands/[id]         | 商品            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（商品目录）    |
| admin    | /brands              | 商品            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（商品目录）    |
| admin    | /campaign-analytics  | 活动            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（数据分析）    |
| admin    | /campaigns/[id]      | 活动            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（增长运营）    |
| admin    | /campaigns           | 活动            | 是       | 未接入       | 0                    | 0              | 0           | 2          | 二级（增长运营）    |
| admin    | /channel-portal      | 渠道            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 一级（渠道门户）    |
| admin    | /channels            | 渠道            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（渠道）        |
| admin    | /codes/[id]          | 码管理/页面引擎 | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（追溯码/页面） |
| admin    | /codes               | 码管理/页面引擎 | 是       | 未接入       | 0                    | 0              | 0           | 1          | 二级（追溯码/页面） |
| admin    | /connectors          | 集成/导入导出   | 否       | 未接入       | 0                    | 0              | 0           | 2          | 二级（集成）        |
| admin    | /crm-sync            | 集成/导入导出   | 否       | 未接入       | 0                    | 0              | 0           | 0          | 二级（集成）        |
| admin    | /exports             | 数据            | 否       | 未接入       | 0                    | 0              | 0           | 0          | 一级（数据导出）    |
| admin    | /gmv                 | 数据            | 否       | 未接入       | 0                    | 0              | 0           | 0          | 二级（数据分析）    |
| admin    | /i18n                | 设置            | 否       | 未接入       | 0                    | 0              | 0           | 0          | 二级（设置）        |
| admin    | /imports             | 集成/导入导出   | 否       | 未接入       | 0                    | 0              | 0           | 4          | 二级（集成）        |
| admin    | /integrations        | 集成/导入导出   | 否       | 未接入       | 0                    | 0              | 0           | 0          | 二级（集成）        |
| admin    | /launch-checklist    | 上线检查        | 否       | 未接入       | 0                    | 0              | 0           | 0          | 二级（治理）        |
| admin    | /members             | 会员            | 是       | 未接入       | 0                    | 0              | 0           | 2          | 二级（增长运营）    |
| admin    | /pages/[id]/edit     | 码管理/页面引擎 | 是       | 未接入       | 0                    | 0              | 0           | 1          | 二级（追溯码/页面） |
| admin    | /pages/[id]          | 码管理/页面引擎 | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（追溯码/页面） |
| admin    | /pages               | 码管理/页面引擎 | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（追溯码/页面） |
| admin    | /products/[id]       | 商品            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（商品目录）    |
| admin    | /products            | 商品            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（商品目录）    |
| admin    | /regional            | 渠道            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（渠道）        |
| admin    | /risk                | 风控            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（治理）        |
| admin    | /risk-center         | 风控            | 是       | 未接入       | 0                    | 0              | 0           | 5          | 一级（风控中心）    |
| admin    | /risk-dashboard      | 风控            | 是       | 未接入       | 0                    | 0              | 0           | 1          | 二级（风控中心）    |
| admin    | /settings/audit-logs | 设置            | 否       | 未接入       | 0                    | 0              | 0           | 0          | 二级（设置）        |
| admin    | /settings/branding   | 设置            | 否       | 未接入       | 0                    | 0              | 0           | 2          | 二级（设置）        |
| admin    | /settings/compliance | 设置            | 否       | 未接入       | 0                    | 0              | 0           | 1          | 二级（设置）        |
| admin    | /settings/crm        | 设置            | 否       | 未接入       | 0                    | 0              | 0           | 0          | 二级（设置）        |
| admin    | /settings/roles      | 设置            | 否       | 未接入       | 0                    | 0              | 0           | 0          | 二级（设置）        |
| admin    | /settings/tenant     | 设置            | 否       | 未接入       | 0                    | 0              | 0           | 0          | 二级（设置）        |
| admin    | /skus/[id]           | 商品            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（商品目录）    |
| admin    | /skus                | 商品            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 二级（商品目录）    |
| admin    | /stats               | 数据            | 否       | 未接入       | 0                    | 0              | 0           | 0          | 一级（统计）        |
| admin    | /store-portal        | 渠道            | 是       | 未接入       | 0                    | 0              | 0           | 0          | 一级（门店门户）    |
| admin    | /page-preview        | 页面引擎        | 是       | 未接入       | 0                    | 0              | 0           | 37         | N/A                 |
| admin    | /                    | 首页            | 否       | 部分         | 1                    | 1              | 13          | 2          | 一级（首页看板）    |
| h5       | /c/[publicId]        | 扫码 H5         | 是       | 未接入       | 0                    | 0              | 0           | 0          | N/A                 |
| h5       | /                    | 首页            | 否       | 已接入       | 1                    | 1              | 2           | 0          | N/A                 |
| h5       | /preview             | 页面引擎        | 是       | 未接入       | 0                    | 0              | 0           | 0          | N/A                 |
| h5       | /redpacket/result    | 红包            | 是       | 未接入       | 0                    | 0              | 0           | 0          | N/A                 |
| platform | /login               | 认证            | 否       | 未接入       | 0                    | 0              | 0           | 4          | N/A                 |
| platform | /analytics           | 数据分析        | 否       | 未接入       | 0                    | 0              | 0           | 9          | N/A                 |
| platform | /audit-logs          | 审计日志        | 否       | 未接入       | 0                    | 0              | 0           | 0          | N/A                 |
| platform | /health              | 客户健康度      | 否       | 未接入       | 0                    | 0              | 0           | 7          | N/A                 |
| platform | /                    | 首页            | 否       | 未接入       | 0                    | 0              | 0           | 9          | N/A                 |
| platform | /plans               | 套餐管理        | 否       | 未接入       | 0                    | 0              | 0           | 7          | N/A                 |
| platform | /providers           | 服务商管理      | 否       | 未接入       | 0                    | 0              | 0           | 0          | N/A                 |
| platform | /quota               | 额度监控        | 否       | 未接入       | 0                    | 0              | 0           | 0          | N/A                 |
| platform | /settings            | 系统配置        | 否       | 未接入       | 0                    | 0              | 0           | 2          | N/A                 |
| platform | /tenants/[id]        | 租户管理        | 否       | 未接入       | 0                    | 0              | 0           | 0          | N/A                 |
| platform | /tenants             | 租户管理        | 否       | 未接入       | 0                    | 0              | 0           | 0          | N/A                 |
