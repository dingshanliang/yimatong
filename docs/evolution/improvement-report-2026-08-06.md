# 产品进化改进报告

> 项目: 一码通 | 日期: 2026-08-06 | Evolution ID: EV-20260806-01

## 扫描摘要

- **技术栈**: FastAPI + PostgreSQL/Redis，Next.js + Ant Design 6，Vitest + Playwright
- **PRD 状态**: 主 PRD（`docs/01_product/PRD.md`）+ `docs/prd/launch-safety-gate.md`（Pilot-ready）
- **活跃开发区域**: 客户上线安全门禁（launch-releases）、既有码接管控制面（takeovers）、品牌定制五槽位、安全/风控加固、测试基线修复
- **上次扫描**: 2026-07-31
- **发现**: 3 条历史方向仍需推进 + 4 条本次新增方向；6 条历史方向已实现并移入 Resolved；3 个改进包
- **扫描边界**: 未启用 `--discover`，不做竞品搜索，仅基于产品文档、路由、页面、API、模型证据分析

### 本次去伪核验

- **上线门禁三方向已实现**：`backend/app/api/v1/launch_releases.py` 提供版本创建/确认/确认并上线/暂停/恢复，`ops_launch_releases.py` 提供代运营请求确认与发布执行；`launch-checklist/page.tsx` 已改为服务端 `readiness_snapshot` 驱动（localStorage 计数为 0）。雷达 #1/#3/#4 → resolved（`df780005`）。
- **接管三方向已实现**：`takeovers.py` 提供方案评估（`/assess`）、导入 dry-run/提交/失败重试/错误 CSV、域名检查（`services/takeover.py:1042` 真实 `dns.resolver` + TLS 证书校验）、切换/回滚/事件流；前端有 1075 行 `codes/takeover/page.tsx`。雷达 #7/#8/#10 → resolved（`cc7ef708`、`dc9c9358`）。
- **域名验证部分降级**：接管流程内域名检查已真实化，但品牌设置的独立域名验证 `services/whitelabel.py:96` 仍是“简化版：标记为已验证”，雷达 #9 主体 resolved，残留部分记为新条目 #14。
- **抽奖为确认缺口**：前端可创建“抽奖活动”（`campaigns/page.tsx:97`）并展示 `lottery_chance` 权益文案，但后端 `BenefitType` 仅 5 种且无 lottery，全后端服务无抽奖/概率/奖池逻辑，仅 `constants/campaign.py:29` 将 lottery 列为活动目标。消费者无法真正抽奖。
- **通用导入记录仍缺失**：`imports/page.tsx:68` 请求 `/imports/records`，后端 `imports.py` 仅 4 个端点（template/excel/products/existing-codes），全后端无该路由；页面捕获错误后静默清空记录。与上次报告判断一致，仍未修复。

---

## 改进包列表

### 改进包 1: 试点数据学习与复盘 [综合 RICE: 43.2]

**包含改进**:

1. **试点里程碑与首个活动上线时长** [RICE: 36.0] — 记录客户开通、确认、正式上线、首扫、首个活动发布时间点，形成可比较的上线效率指标。（雷达 #2，持续）
2. **7/14/30 天复盘工作流** [RICE: 21.3] — 自动生成复盘任务，沉淀目标、数据结果、问题、动作负责人和下一次验证日期。（雷达 #5，持续）

**证据链**:

- `docs/01_product/PRD.md:317-325` 定义“首个活动上线时长、客户活跃度、客户健康度”运营指标；租户模型仍无里程碑字段（本次全模型 grep `milestone|复盘` 无命中）。
- `docs/01_product/USER_JOURNEYS.md:48-64` 与 `docs/03_delivery/IMPLEMENTATION_PLAYBOOK.md:62-80` 要求第 7/14/30 天复盘。
- `backend/app/models/tenant.py` 的 `OpsTask` 仍是通用任务字段，`backend/app/api/v1/ops.py` 仅通用任务 CRUD，无复盘对象、周期生成或 scorecard 结构。
- 上线安全门禁 PRD（`docs/prd/launch-safety-gate.md:171`）明确将 7/14/30 天复盘列为 Out of Scope，由 `yimatong-bgag` 跟踪——门禁已落地，复盘成为下一阶段最突出的 PRD 差距。

**置信度**: High（PRD 指标、用户旅程、交付手册、模型/API、门禁 PRD 范围五类证据一致）。

**建议 PRD 方向**:

借助刚落地的 launch-releases 事件时间线（版本创建/确认/上线时间已是持久化事实），里程碑只需消费现有事件流即可计算上线效率；复盘定义最小 scorecard（上线效率、有效访问、权益确认、企微确认、订单/净 GMV、复盘动作），每个指标写清分母、事件来源、归因窗口。

**下一步**:

→ `/prd-generator "为一码通实现试点数据学习与复盘，包括里程碑、7/14/30 天复盘和运营 scorecard"`

---

### 改进包 2: 运营数据维护补完 [综合 RICE: 12.0]

**包含改进**:

1. **外部订单退款/状态维护入口** [RICE: 12.0] — 后端有 `refund_order` 服务和净额回冲测试，但 `api/v1/gmv.py` 仅导入/列表/归因/看板端点，无退款或订单状态维护 API/UI；净 GMV 数据一旦导入错误无法在线修正。（上报告 P2，持续）
2. **通用导入记录历史** [RICE: 4.7] — 产品/批次导入历史与失败明细不可见：`imports/page.tsx:68` 请求不存在的 `/imports/records` 并静默显示空列表。（新增 #11）

**证据链**:

- `backend/app/api/v1/gmv.py:45-169` 端点清单无 refund/status；Admin `gmv` 页面仅有看板与导入。
- `backend/app/api/v1/imports.py:26-193` 仅 template/excel/products/existing-codes 四端点；全后端 grep `imports/records` 无命中。
- 接管导入已有完整记录追踪（dry-run/errors.csv），证明模式可行，通用导入可复用同一设计。

**置信度**: High（前端调用、后端路由清单、全库 grep 三类证据一致）。

**建议 PRD 方向**:

小步补完而非大设计：为 GMV 订单增加退款/状态修正端点 + Admin 入口（复用现有回冲服务）；为通用导入增加 records 列表端点（操作者、文件、成功/跳过/失败、错误明细），对齐接管导入的记录结构。

**下一步**:

→ `/prd-generator "为一码通补完运营数据维护：订单退款/状态修正入口与通用导入记录历史"`

---

### 改进包 3: 活动玩法与页面引擎增强 [综合 RICE: 6.4]

**包含改进**:

1. **抽奖活动闭环缺失** [RICE: 5.3] — 前端可配置抽奖活动但后端无抽奖机制（奖池、概率、中奖记录），消费者端无法真正开奖；当前形态会产出“配了但不能用”的活动。（新增 #12，PRD ACT-08 P1）
2. **活动期内容切换** [RICE: 4.2] — 页面 DSL 不支持预热/活动中/结束后自动切换内容，只能靠人工发布新版本。（新增 #13，PRD PAGE-05 P1）

**证据链**:

- `frontend/apps/admin/src/app/(dashboard)/campaigns/page.tsx:97` 提供“抽奖活动”类型，`[id]/page.tsx:44` 映射 `lottery_chance` 权益；但 `backend/app/constants/campaign.py:15-22` 的 `BenefitType` 无 lottery 类型，全 `backend/app/services/` 无抽奖逻辑。
- `backend/app/services/page_render.py:103-116` 的上下文构建不含活动阶段/时间维度；H5 `ResolveContent.tsx` 仅按 `is_first_scan` 切换验真状态，无活动期内容分支。
- `docs/01_product/PRD.md:208`（ACT-08 P1 抽奖）与 `PRD.md:198`（PAGE-05 P1 活动期切换）。

**置信度**: 抽奖 High（常量、模型、服务、前端四类证据）；活动期切换 Medium（2 类证据，可能有人工版本切换的工作流约定未记录在代码中）。

**建议 PRD 方向**:

抽奖先做“非现金权益抽奖”最小闭环：奖池配置（权益 + 概率 + 总量）、服务端开奖、中奖记录与风控（同设备/同手机号限制）。活动期切换可复用页面版本机制：定义预热/活动中/结束三个内容快照 + 按活动时间自动生效，避免全天候人工值守发布。

**下一步**:

→ `/prd-generator "为一码通实现非现金抽奖闭环与活动期内容自动切换"`

---

## 低优先级改进（P3）

| #   | 改进方向             | RICE | 维度     | 状态 | 说明                                                                                     |
| --- | -------------------- | ---: | -------- | ---- | ---------------------------------------------------------------------------------------- |
| 14  | 品牌设置域名验证占位 |  3.6 | 功能深度 | new  | `whitelabel.py:96` 仍直接标记已验证；接管流程已有真实 DNS+TLS 校验代码可复用，工作量小。 |

## 已实现或不再作为缺口的方向（本轮 Resolved）

- **上线准备度单一事实源 / 客户级上线清单 / 客户预览与上线确认**（雷达 #1/#3/#4）：`df780005` launch-releases 全链路落地，前端清单已服务端驱动。
- **切换前验证与回滚 / 接管方案评估 / 既有码导入追踪**（雷达 #7/#8/#10）：`cc7ef708`+`dc9c9358` 接管控制面落地，含真实 DNS/TLS 校验。
- **客户域名/CNAME 接管配置**（雷达 #9）：接管流程内已真实验证，残留品牌设置占位验证另记 #14。

## 雷达状态摘要

- **Active**: 7 条（3 known + 4 new）
- **Resolved 累计**: 7 条（本轮新增 6 条）
- **趋势**: 上次扫描的 3 个改进包中 2 个（上线门禁、接管迁移）已完整交付，剩余最高价值方向集中在试点数据复盘

详细雷达 → `docs/evolution/radar.md`
