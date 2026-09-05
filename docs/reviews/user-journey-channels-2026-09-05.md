# User Journey Review Report - M6 渠道组织与门户

### 📊 Review Overview

- Review Date: 2026-09-05
- Scope: 渠道/区域/门店管理、码段分配（CAS+幂等）、窜货线索处置、经销商门户、门店门户、区域组织（regional）、组织账户
- 方式: 代码驱动审查 + 基线测试根因定位（代码评审模块）
- Issues Found: 3 P0（regional 无 RBAC 越权、经销商门户窜货预警永远为空、regional 页全静默吞错）+ 3 P1（幂等键失败不轮换、CAS 409 英文直出不刷新、测试时序）+ 12 P2/P3；P0/P1 全部修复
- Journey Score（修复后均值）: 4.0

---

### 🔴 P0（全部已修复）

1. **regional.py 全路由无 RBAC（越权）**: 27 条路由仅 get_current_tenant，任意角色（含 viewer）可创建区域组织/增删成员/下发模板/产品授权/改数据隔离策略。**Fix**: 写路由挂 `channel:manage`（15 条）、读路由挂 `channel:read`（12 条）——均为已存在权限码，admin/operator 持有；前端 regional 页接入 `channelAccessForPrincipal` 门控（无权限全页 Alert、写按钮仅 canManage）。新增 `test_regional_permissions.py` 42 用例（viewer 逐条 403 + admin 过权限层）。
2. **经销商门户「窜货预警」永远为空**: 门户调 `/risk-notifications` 需 `risk:read`（distributor 角色权限为空 → 403 静默）。**Fix**: 新增 portal 专用端点 `GET /channels/portal/distributor/diversion-alerts`（复用 distributor portal principal 鉴权 + AccountChannelScope 范围过滤 risk_notifications），门户前端改调新端点；4 个 API 测试（scope 隔离/region 回退/403/404）。
3. **regional 页全静默吞错**: fetchOrgs/handleCreate 等 catch 空。**Fix**: extractErrorMessage + message + 失败 Alert/重试（新增 4 个前端用例）。

### 🟡 P1（全部已修复）

- **幂等键失败不轮换**: channels 页 `completeMutationIntent` 仅成功删 key，失败后同 key+新 payload 撞 409 死循环。**Fix**: 新增 `failMutationIntent`，11 个 mutation 失败路径统一轮换 key；新增回归测试（失败重试后 key 必不同）。
- **CAS 409 反馈**: 409 → 中文提示「记录已被他人更新，已为您刷新最新数据，请重试」+ 自动重拉数据（不再英文直出 + 带过期版本继续提交）。
- **基线测试时序**: channels 测试 5 处 per-test 10s 覆盖删除（全局 20s 生效）；channels/accounts 测试 `configure({ asyncUtilTimeout: 5000 })`。M6 的 5 个基线失败经实测在 HEAD 全部通过——根因均为重渲染页面在并行 CPU 争抢下超默认 1s RTL 等待/旧 10s 超时（时序脆弱，非功能回归）。

### 📋 记录（P2/P3，未修）

1. 停用门店后从列表消失（`/channels/stores` 默认 status=active，前端未带 status；distributor/region 无此默认值，行为不一致）。
2. channels 页 9 请求 Promise.all 单点失败全页失效（建议 allSettled / 按 tab 局部加载）；窜货处置依赖 investigation 加载成功，失败无重试即死锁。
3. 两个门户页失败文案不分级（404/403/500 一律「未找到入口范围」）。
4. regional.py 坏入参 500（uuid 无校验）、update_member 未找到返回 200、写操作无审计/幂等键。
5. channels/_components/ 5 个文件（633 行）死代码；resolveForm 默认值不在选项内；区域/门店等表格无定制空态；channels/page.tsx 2985 行建议拆分。

### ✅ 验证记录（2026-09-05）

| 项                                                                                   | 结果     |
| ------------------------------------------------------------------------------------ | -------- |
| regional 权限回归（新增 42 用例）+ channel/regional 既有测试                         | 103 通过 |
| 渠道门户窜货预警 API 测试（新增 4 用例）                                             | 通过     |
| admin vitest regional/channels/accounts/channel-portal/store-portal（含新增 6 用例） | 43 通过  |
| tsc / eslint / ruff                                                                  | 通过     |
