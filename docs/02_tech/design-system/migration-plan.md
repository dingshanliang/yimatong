---
status: active
last_verified: 2026-07-30
accuracy: high
---

# 设计体系迁移规划

> 来源：Wayfinder 地图（Beads `yimatong-pifo`）决策落地。本文回答"旧页面按什么顺序迁、每批怎么算迁完"。治理机制（门禁/走查）见 [governance.md](governance.md)。
> 本图只做规划；迁移执行由执行 epic 承接（Beads 批次票）。

## 排序原则

**用户可见性 × 规范差距**。无外部时间约束，不使用"风险从低到高"或"按功能域整块迁"。

- 消费者可见面（H5）与品牌面优先
- 硬编码越多的页面越早清剿（漂移最严重的先止血）
- 硬标准为全员底线；组件骨架规范只对核心流程页强制，辅助页不做结构改造（守住"不重写"边界）

## 批次划分

四批，共 63 页（Admin 48 / Platform 11 / H5 4）。页面事实清单见 `page-inventory.md` / `page-inventory.csv`。

### 批 0 · 样板批 + 门禁落地（6 页）

| 端    | 页面                                                  | 说明                                                                                         |
| ----- | ----------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| h5    | `/`、`/c/[publicId]`、`/preview`、`/redpacket/result` | 合规检查口径：验证主题层接入、补缺不重构；顺手将 10 个文件的手写内联 SVG 替换为 lucide-react |
| admin | `/benefits`（硬编码 11）                              | 重灾页样板                                                                                   |
| admin | `/page-preview`（硬编码 37）                          | 最重灾页，页面引擎预览                                                                       |

**同批交付 CI 门禁基础设施**：禁硬编码三件套规则配置 + 全仓 baseline 收录现状 + `design-gates` CI job + 本地 `pnpm lint:design` 自查脚本。详见 [governance.md](governance.md)。

**样板批复盘**：批 0 完成后复盘验收清单可操作性与单页耗时，允许微调批 2/3 边界（如按功能域再细分）。

### 批 1 · 重灾清剿（8 页）

| 端       | 页面           | 硬编码 |
| -------- | -------------- | ------ |
| admin    | `/risk-center` | 5      |
| admin    | `/agency`      | 4      |
| admin    | `/imports`     | 4      |
| platform | `/analytics`   | 9      |
| platform | `/`            | 9      |
| platform | `/health`      | 7      |
| platform | `/plans`       | 7      |
| platform | `/login`       | 4      |

### 批 2 · 核心流程页（24 页）

规则：盘点中"核心业务流程 = 是"且未进批 0/1 的全部页面。全部在 admin：

- **码管理/页面引擎**：`/codes`、`/codes/[id]`、`/pages`、`/pages/[id]`、`/pages/[id]/edit`
- **活动**：`/campaigns`、`/campaigns/[id]`、`/campaign-analytics`
- **会员**：`/members`
- **商品**：`/brands`、`/brands/[id]`、`/products`、`/products/[id]`、`/skus`、`/skus/[id]`、`/batches`
- **渠道**：`/accounts`、`/channels`、`/channel-portal`、`/regional`、`/store-portal`
- **风控**：`/anti-diversion`、`/risk`、`/risk-dashboard`

### 批 3 · 辅助长尾（25 页）

规则：其余全部辅助页。admin：首页、`/ai-assistant`、`/analytics`、`/stats`、`/gmv`、`/exports`、`/connectors`、`/crm-sync`、`/integrations`、`/i18n`、`/launch-checklist`、`/login`、`/reset-password`、`/settings/*`（6 页）；platform：`/tenants`、`/tenants/[id]`、`/quota`、`/providers`、`/settings`、`/audit-logs`。

## 验收标准（分层）

### 全员硬标准（每页必过，自动化可验证）

1. **零硬编码**：页面目录无硬编码色值/字号/Tailwind 任意值——由 CI 门禁 lint 规则验证，baseline 中该页条目同步移除
2. **a11y 基线**：focus-ring / 触控目标 / 双编码 / 字号下限四项 + 对比度（见 [accessibility.md](accessibility.md)）
3. **深色模式正常**（Admin/Platform）：明暗双主题目测无破版

### 核心页追加（批 0 的 admin 页 + 批 2 全部）

按 [components.md](components.md) 执行：列表页四段式骨架、Table/Modal/Drawer 选型硬规则、三级反馈 + 危险操作确认词、统一空/加载/错误态（V2 品牌容器空状态）、新记录排第一契约。

### 走查要求

每批验收按 [governance.md](governance.md) 的人工走查工作流执行：执行人按分层清单自查勾选，批 0 与批 2 由用户抽查。

## 执行入口

迁移执行的 beads epic 与各批次票由收口票 `yimatong-pifo.8` 创建，批次票的 description 即本规划对应章节的执行契约。
