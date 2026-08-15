# 产品进化改进报告

> 项目: 一码通 | 日期: 2026-08-15 | Evolution ID: EV-20260815-01

## 扫描摘要

- **技术栈**: FastAPI + PostgreSQL/Redis，Next.js 16 + Ant Design，Vitest + Playwright
- **PRD 状态**: 主 PRD（`docs/01_product/PRD.md`）+ `docs/prd/launch-safety-gate.md` + `docs/prd/pilot-learning-retrospective.md`（均 Confirmed / Pilot-ready）
- **活跃开发区域**: 上次扫描后 29 个提交几乎全部为租户隔离/权限/权威数据路径安全加固（gmv_authority、code lifecycle、page version、takeover cutover 等），另有试点里程碑与复盘落地
- **上次扫描**: 2026-08-06
- **发现**: 5 条历史方向延续（其中 2 条降级为 partial、2 条 resolved）+ 12 条本次新增方向；共 17 条 active → 6 个改进包
- **扫描边界**: 未启用 `--discover`，不做竞品搜索，仅基于产品文档、路由、页面、API、模型证据分析

### 本次去伪核验

- **试点里程碑与复盘已完整交付**：`backend/app/constants/pilot.py:10` 五类里程碑、`services/pilot_milestone.py:102` 真实事实源派生、`api/v1/pilot_milestones.py:40` 时间线/更正 API、admin `pilot/page.tsx` 时间线；复盘侧 `constants/retrospective.py:14`（7/14/30 天）、`models/retrospective.py`（scorecard 快照/动作/下次验证日期）、`tasks/worker.py:285` 定时生成、`RetrospectiveCard.tsx` 前端表单。雷达 #2/#5 → resolved（`438d9933`、`cdd16787`，PRD `yimatong-bgag` 落地）。
- **外部订单退款后端已补，降级 partial**：`api/v1/gmv.py:199-226` refund、`:229-256` cancel 走 `gmv_authority` ledger 权威路径（`4727c8da`），但 Admin 前端无任何退款/修正 UI，旧 `services/gmv.py:92 refund_order` 成死代码。雷达 #15 剩余缺口收窄为"前端入口"。
- **活动期切换配置侧已落地，降级 partial**：admin `RoutingConfig.tsx` 活动期配置 UI + `schemas/page_dsl.py:59` 校验完整，但 `services/page_render.py:104` 无时间维度、`resolver.py:70` 选页无时间窗、H5 零消费，且 DSL 的 `CampaignPeriod` 无 per-period 内容绑定——"配了但不生效"比原先完全缺失更具误导性。
- **主 PRD P0/P1 复核**：50 项中 48 项 implemented，缺口仍只有 ACT-08 抽奖（missing）与 PAGE-05 活动期切换（partial），未发现退化。
- **新增候选去伪抽查通过**：红包结果页全页 0 次请求（本次独立 grep 复核）；`benefit_claims.py` 仅 POST 无状态查询 GET；CRM 三个前端调用路径全后端零命中；`/code-items` 仅 code_batch_id+status 两个过滤参数；agency 页面恒 `page_size: 100`。

---

## 改进包列表

### 改进包 1: H5 消费者旅程韧性完善 [综合 RICE: 12.6]

**包含改进**:

1. **H5 扫码失败无重试** [RICE: 12.6] — `CodePageClient.tsx:50` 任意异常一次失败即落到静态 `FallbackError` 卡片；`ErrorPage.tsx:24,131` 已支持 `onRetry` 渲染重试按钮但全 H5 无调用方（死能力）。（新增 #18）
2. **H5 现金红包结果页状态闭环** [RICE: 9.5] — `redpacket/result/page.tsx` 只读 URL 参数、全页 0 次请求：pending 永不自动转成功（静态文案"1-3 分钟到账"）、failed 无重试入口；`benefit_claims.py:34` 仅 POST，无按 claim_id 查询发放状态的 GET，想轮询也无接口。（新增 #17）

**证据链**:

- `frontend/apps/h5/src/app/c/[publicId]/CodePageClient.tsx:50-51` 异常→`setPayload(null)` 单次尝试；`components/FallbackError.tsx` 纯静态无按钮。
- `frontend/apps/h5/src/app/redpacket/result/page.tsx:63,119-123,49-56`（页面行为）；`backend/app/api/v1/benefit_claims.py:34`（路由面）。现金红包是已上线的核心权益类型（ACT-10）。
- 置信度: High（页面代码、API 路由清单、组件能力闲置三类证据，且经本次独立复核）。

**建议 PRD 方向**:

补一个按 claim_id 的发放状态查询端点（沿用 scan-token/claim 鉴权与限流），结果页对 pending 轮询直到终态、failed 给重试或客服入口；扫码失败路径把 `ErrorPage.onRetry` 接上（带一次退避重试）。只做状态闭环，不改动发放权威路径。

**下一步**:

→ `/prd-generator "为一码通完善 H5 消费者旅程韧性：扫码失败重试与现金红包结果页状态闭环"`

---

### 改进包 2: 查码与扫码明细运营支持 [综合 RICE: 11.3]

**包含改进**:

1. **码明细按码号/公开码查询** [RICE: 11.3] — `/code-items` 列表仅 `code_batch_id`+`status` 两个过滤（`code_batches.py:572-584`），admin 码列表工具栏仅一个状态筛选（`codes/page.tsx:721`）；客服处理"某消费者报某码异常"时只能猜批次翻页。（新增 #22）
2. **扫码明细列表/导出缺失** [RICE: 5.4] — `scan_events.py:85` 仅 POST 埋点写入，租户侧无明细查询；`analytics.py:98` recent-events 上限 20 仅供首页卡片；对照：码表/风控/分析报表均有导出（`code_batches.py:256`、`risk_dashboard.py:229`、`analytics_dashboard.py:178`），量最大的扫码数据反而两端无出口。（新增 #21）

**证据链**:

- 后端过滤参数清单 + 前端工具栏代码 + 同类数据导出能力对照，三类证据交叉。
- 置信度: High。

**建议 PRD 方向**:

`/code-items` 增加 code/public_id 精确与前缀查询（客服查码刚需）；新增租户侧扫码明细分页列表（时间/页面/地域/风控标记筛选）与带导出理由、走 ExportLog 审计的导出——数据含 PII 边界（设备/IP）需在 PRD 中明确脱敏口径。

**下一步**:

→ `/prd-generator "为一码通实现查码与扫码明细运营支持：码号查询、扫码明细分页与合规导出"`

---

### 改进包 3: 运营数据维护与出口补完 [综合 RICE: 9.0]

**包含改进**:

1. **外部订单退款/状态维护 Admin 入口** [RICE: 9.0] — 后端 refund/cancel 端点已上 ledger 权威路径，但 `OrdersTab.tsx:70` 前端仅有导入，全 admin 无退款 UI；复盘 scorecard 已消费净 GMV，导入错误无在线修正手段直接影响复盘口径。（雷达 #15，降级为 partial）
2. **通用导入记录历史** [RICE: 7.5] — `imports/page.tsx:85` 请求 `/imports/records`，后端 `imports.py` 仅 4 端点无该路由（无 ImportRecord 模型）；中间件 `tenant.py:719` 已白名单该路径但路由不存在仍 404；导入失败明细弹窗永远为空。（雷达 #11，持续）
3. **GMV 订单筛选/导出缺失** [RICE: 5.6] — `/gmv/orders` 仅 `matched` 一个业务过滤（`gmv.py:259-263`），无时间范围/渠道/订单号筛选，订单与归因数据均无导出按钮。（新增 #26）

**证据链**:

- `backend/app/api/v1/gmv.py:199-256`（refund/cancel 已存在）；`frontend/apps/admin/src/app/(dashboard)/gmv/_components/OrdersTab.tsx:70,95`（仅导入+单筛选）。
- `frontend/apps/admin/src/app/(dashboard)/imports/page.tsx:85,87-89`（前端自带"接口尚未就绪"注释）；`backend/app/api/v1/imports.py` 端点清单。
- 置信度: High（前端调用、后端路由清单、中间件白名单三类证据）。

**建议 PRD 方向**:

小步补完：OrdersTab 操作列接已有 refund/cancel 端点（`order:manage` 权限 + 幂等键提示）；后端增 ImportRecord 模型 + `GET /imports/records`（对齐接管导入 dry-run/errors.csv 的记录结构，前端表格已就绪）；订单列表加时间/渠道/订单号筛选与带理由导出。

**下一步**:

→ `/prd-generator "为一码通补完运营数据维护：GMV 退款入口、导入记录历史与订单筛选导出"`

---

### 改进包 4: 工作台与平台端列表体验 [综合 RICE: 9.0]

**包含改进**:

1. **代运营工作台列表硬上限 100** [RICE: 9.0] — `agency/page.tsx:71` 恒 `page: 1, page_size: 100` 且无分页 UI，客户超 100 静默截断；后端 `ops.py:168` 实际支持分页，是前端没接。（新增 #25）
2. **Platform 审计日志无分页** [RICE: 3.6] — `platform/audit-logs/page.tsx:43` 写死 `limit=200` 后纯前端过滤，`platform.py:639` 返回裸 list 无分页结构；对照 admin 侧 `audit_logs.py:40` 是分页的——超 200 条的平台审计直接不可见。（新增 #24）
3. **Platform 里程碑更正无 UI** [RICE: 2.7] — `pilot_milestones.py:54` 平台侧 POST corrections 已实现，platform 前端 grep milestone 零命中，管理员只能裸调 API。（新增 #27）

**证据链**:

- 前端请求参数写死 + 后端能力已存在（ops 分页）/缺失（platform 分页）两端对照。
- 置信度: High（#25/#24）；#27 双端对照 High。

**建议 PRD 方向**:

agency 工作台接标准分页控件；platform audit-logs 改分页响应结构并加时间/操作者过滤；platform 租户详情页补里程碑更正入口。均为小工作量体验补完，可随迭代顺手做。

**下一步**:

→ `/prd-generator "为一码通补完工作台与平台端列表体验：分页、审计日志过滤与里程碑更正入口"`

---

### 改进包 5: 集成与渠道配置闭环 [综合 RICE: 7.9]

**包含改进**:

1. **CRM 同步管理页整体失效且伪装成功** [RICE: 7.9] — `crm-sync/page.tsx:53,65,82` 调 `/crm/sync-mappings`、`/crm/sync-logs`、`/crm/trigger-sync`，全后端零命中（唯一相关是 `integration.py:52` 的 customer-sync，路径语义不匹配）；`:84-85` 手动同步 404 也提示"将在下一个 cron 周期自动执行"——整页数据恒空且错误被伪装为正常。（新增 #16）
2. **经销商/区域无删除入口** [RICE: 4.8] — `channels.py` 经销商（:178-222）与区域（:249-297）有 POST/PUT/PATCH 无 DELETE，仅门店（:404）可删；前端 DistributorTab/RegionTab 亦无删除按钮，合作终止的渠道只能停用不能下线。（新增 #19）
3. **私域配置管理入口缺失** [RICE: 4.5] — `private_domain.py:28,49,74` 后端 CRUD 完整，但 admin/h5 全前端零调用；`BenefitConfigFields.tsx:117` 私域权益仍让用户手填二维码 URL，不关联配置实体——而 campaigns 已有 `private_domain_repurchase` 活动类型，产品方向明确但管理断档。（新增 #20）

**证据链**:

- CRM：前端调用路径 vs 后端路由全集差集（本次独立 grep 复核确认零命中）+ 伪装成功提示代码。
- 渠道/私域：两端 CRUD 动作与入口逐一 grep 对照。
- 置信度: High（#16/#19）；#20 High（后端路由+前端零调用+权益表单三类）。

**建议 PRD 方向**:

CRM 页先做产品决策：实现最小 mappings/logs/trigger（挂现有 integration customer-sync）或暂时隐藏入口，二选一都比"整页假数据"好；渠道补软删除（保留审计轨迹）；私域配置建管理页并让 `private_domain` 权益引用配置实体而非手填 URL。

**下一步**:

→ `/prd-generator "为一码通补完集成与渠道配置闭环：CRM 同步页处置、渠道删除与私域配置管理"`

---

### 改进包 6: 活动玩法与页面引擎增强 [综合 RICE: 7.2 = 6.0 × 1.2（PRD 明确定义加权）]

**包含改进**:

1. **活动期内容自动切换（partial）** [RICE: 6.0] — 配置侧（RoutingConfig UI + DSL 校验）已落地，但运行时零消费：`page_render.py:104` 上下文无活动阶段/时间维度、`resolver.py:70` 选页无时间窗、H5 渲染不读 routing；且 `page-dsl.ts:41` 的 CampaignPeriod 无 per-period 内容绑定字段——即使渲染侧想切也无差异化内容可切。运营"配了排期"实际不生效。（雷达 #13，降级为 partial，PRD PAGE-05 P1）
2. **抽奖活动闭环缺失** [RICE: 4.5] — `constants/campaign.py:15-22` BenefitType 无 lottery；`:29` lottery 仅为活动目标标签；H5 `BenefitClaimCard.tsx:96` 把 lottery 降级映射为 external_link；admin 抽奖模板 `benefit_enabled: false` 纯文案预设。无奖池/概率/开奖/中奖记录任何机制。（雷达 #12，持续，PRD ACT-08 P1）

**证据链**:

- 配置侧存在（`RoutingConfig.tsx:68-135`、`schemas/page_dsl.py:59-72`）vs 运行时零消费（page_render/resolver/H5 三处 grep 零命中）——"能配置的空壳"。
- PRD `docs/01_product/PRD.md` PAGE-05、ACT-08 均为 P1 明确定义。
- 置信度: High（双项均 3+ 证据源 + PRD 定义）。

**建议 PRD 方向**:

先补运行时再谈玩法：DSL 扩展 per-period 内容快照（预热/活动中/结束后）→ resolver 按时间窗+活动状态选版本 → H5 消费 routing，让已上线的配置真正生效；抽奖维持上次结论——非现金权益最小闭环（奖池+概率+服务端开奖+中奖记录+同设备/手机号风控），进入条件不变。

**下一步**:

→ `/prd-generator "为一码通实现活动期内容运行时切换（per-period 内容绑定 + resolver 时间窗）"`

---

## 低优先级改进（P2/P3）

| #   | 改进方向                    | RICE | 维度     | 状态  | 说明                                                                                                                                     |
| --- | --------------------------- | ---: | -------- | ----- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| 14  | 品牌设置域名验证占位        |  7.2 | 功能深度 | known | `whitelabel.py:91-106` 仍无条件 `verified=True`；`takeover.py:122-180` 真实 DNS+TLS 校验可直接复用，工作量小、性价比高，可作插队小任务。 |
| 23  | Platform 分析页为健康分衍生 |  1.7 | 功能深度 | new   | `platform/analytics/page.tsx:22-31` 自述"derive from health data for now"，无真实经营分析端点。置信度 Medium（后端字段内容未逐项验证）。 |

## 已实现或不再作为缺口的方向（本轮 Resolved）

- **试点里程碑与首个活动上线时长**（雷达 #2）：`438d9933` 五类里程碑真实事实源派生 + 时间线/更正 API + admin/agency 前端。
- **7/14/30 天复盘工作流**（雷达 #5）：`cdd16787` 复盘实体（scorecard 快照/动作/下次验证日期）+ worker 定时生成 + 前端复盘卡片。
- 上次最高分改进包"试点数据学习与复盘"（43.2）已随 PRD `yimatong-bgag` 完整交付。

## 雷达状态摘要

- **Active**: 17 条（5 known + 12 new）
- **Resolved 累计**: 9 条（本轮新增 2 条：#2、#5）
- **趋势**: 主 PRD P0/P1 覆盖率 48/50 保持稳定；上次扫描后的安全加固周期未引入新功能面缺口；本轮新发现集中在 H5 消费者失败路径（扫码/红包）与运营数据出口（查码/明细/订单），均为"已有数据/能力差最后一公里"型缺口，单项工作量小

详细雷达 → `docs/evolution/radar.md`
