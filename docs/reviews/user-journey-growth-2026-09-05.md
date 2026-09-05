# User Journey Review Report - M5 营销增长（campaigns/benefits/members/复购）

### 📊 Review Overview

- Review Date: 2026-09-05
- Scope: 创建活动→建权益→绑定页面→发布→领取；领取管理（发放重试）；会员生命周期；复购券工作台；营销通知
- 方式: 代码驱动深度审查 + 基线测试根因定位 + 浏览器冒烟
- Issues Found: 3 P0（权限码失效=线上 403、套餐门顺序、vitest 随机超时）+ 4 P1 + 5 P2；P0/P1 全部修复，P2 记录
- Journey Score（修复后均值）: 4.0

---

### 🔴 P0（全部已修复）

#### 1. `campaign:write` 权限码不存在——复购券/工作台/通知管理端写路径线上全部 403

- **Location**: `repurchase_coupons.py:46`、`repurchase_workbench.py:23`、`member_notifications.py:155`
- **Analysis**: 权限码 `campaign:write` 不在任何角色/VALID_PERMISSIONS 中，复购券规则创建/发布/发券/核销、工作台处置/分派、营销通知发送全部 403；前端只显示通用「处置失败」。测试只覆盖消费者/门店路径故未暴露。
- **Fix**: 三处改 `campaign:manage`（campaigns/benefits 同款）。**同根因深挖**：迁移 `26336e5a1635` 的 DB 权威函数 `mutate_repurchase_work_item_authority` 也校验 `campaign:write`，仅改 API 层会变成 DB 层 42501——新增迁移 `a7f2c9d41e58_align_repurchase_workbench_permission_code.py`（可逆）+ 同步验收测试种子，并在 infra PG 真实跑通。
- **Verification**: 新增 `test_repurchase_permissions.py` 4 用例（admin 打通三个写端点 + viewer 负向 403 对照）。

#### 2. 领取端点套餐门顺序错误：过期租户得到 401「请重新扫码」而非 403 套餐提示

- **Location**: `benefit_claims.py`（launch authority 校验在 lock_active_tenant_context 之前）
- **Fix**: 套餐门上移到 tenant 解析后、launch authority 前。`test_expired_plan_blocks_consumer_benefit_claim` 转绿（403 TENANT_PLAN_EXPIRED）。

#### 3. admin vitest 随机超时（全量跑随机红 13-15 个文件的根因）

- **Analysis**: antd v6 重渲染页面（Tabs+Drawer+Form）单测 3-10s，全量并行 CPU 争抢超默认 5s。
- **Fix**: `vitest.config.ts` 全局 `testTimeout: 20_000`。M3-M5 的 benefits/members 基线失败均属此类（members 用例实测已无法复现）。

### 🟡 P1（全部已修复）

- **「重试发放」按钮生产必失败**: PG 环境发放重试为全自动（端点固定 409），按钮无条件展示误导用户。移除按钮，pending/failed 行内展示「系统将自动重试」+ Tooltip 说明；对应 vitest 用例更新。
- **活动上线检查不含 launch release**: campaigns 页上线前检查只查产品/权益/库存/企微，未提示「页面绑定 + 上线版本发布」——活动可「上线成功」但消费者扫码领不了。本轮先修构建向导硬编码注释与详情页类型映射；检查项扩展记录待办（需产品确认展示位置）。
- **吞 detail**: benefits 页 5 处、campaigns 页 2 处 catch 改 `extractErrorMessage`（含对象型 detail 渲染 [object Object] 的点）；删除草稿无反馈（unhandled rejection）修复。
- **campaigns/[id] BENEFIT_TYPE_MAP 旧词表**: 更新为现行 5 类中文映射（platform_coupon/external_link/form_benefit/cash_red_packet/private_domain）。

### 📋 记录（P2，未修/待决策）

1. members.py 积分 award/spend 端点只查角色不挂 `require_permission`，与同文件其它端点不一致；积分功能首发已隐藏，口径待产品收敛。
2. 活动上线检查需补「页面绑定 + launch release」引导项（与 /launch-checklist 旅程衔接）。
3. 向导建权益仅支持平台券一种（现状即如此，已注释说明）；RepurchaseWorkbench 终态结论必填仅后端校验。
4. OAuth 成功回跳后自动续领（M2 待决策项，属于本旅程转化优化）。

### ✅ 验证记录（2026-09-05）

| 项                                                            | 结果                                                              |
| ------------------------------------------------------------- | ----------------------------------------------------------------- |
| wecom/campaign_safety/repurchase/member_notification 后端测试 | 44 通过（含 3 个过期测试按现行契约修复 + 新增权限回归 4 用例）    |
| workbench RLS 验收测试（infra PG 真实迁移+权威路径）          | 1 通过                                                            |
| 全量后端 pytest                                               | 2599 通过，14F+2E 均为既有基线（与 M8/M9/M10 桶对应），零新增失败 |
| admin vitest benefits/campaigns + tsc + eslint                | 全绿                                                              |
| 浏览器冒烟：campaigns 列表渲染（5 活动）、权限改动无回归      | ✓                                                                 |
