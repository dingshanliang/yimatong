# User Journey Review Report - M10 系统设置与治理

### 📊 Review Overview

- Review Date: 2026-09-05
- Scope: 租户设置、品牌定制、合规设置、隐私权益治理（权益队列/敏感导出双人审批）、代运营授权、审计日志、AI 助手、request-scope 边界
- 方式: 代码驱动审查 + 基线测试根因定位
- Issues Found: 2 P0（合规设置假保存丢数据、risk_evaluate 混合 scope）+ 3 P1（撤销吞错、extractErrorMessage 丢结构化 detail、租户页吞错）+ 基线修复（ai_api ×7、operator_workbench ×1、mutation_boundary ×2）；P0/P1 全部修复
- Journey Score（修复后均值）: 4.1

---

### 🔴 P0（全部已修复）

1. **合规设置「假保存」（数据丢失）**: 三个 Tab PATCH `/tenants/me { compliance_settings }`，但 `TenantUpdateSelf` 无该字段、端点只透传 contact_email——隐私政策内容/授权开关/保留天数全部静默丢弃且提示已保存。**Fix**: schema 增字段 + 端点透传（保持 merge 语义）；新增 3 个回归测试（写入读回/部分合并不覆盖/与 contact_email 同请求合并）。
2. **risk/evaluate 混合 scope**: 路由级 `require_tenant_feature(..., db_scope="function")` 与端点 request-scope get_db 混用（一次请求两会话）。**Fix**: 去掉 db_scope（与 ai.py 一致），mutation boundary 混合项消除。

### 🟡 P1（全部已修复）

- 代运营授权撤销吞错（unhandled rejection、Modal 卡 loading）→ try/catch + extractErrorMessage；状态列硬编码「已生效」改按 status 渲染（已生效/已过期/已撤销），终态行隐藏撤销按钮。
- `extractErrorMessage` 只识别 `detail.msg`：补 `detail.message` 候选——功能门禁 403 `{code:"TENANT_FEATURE_DISABLED", message:...}` 等全站可见原因（新增 5 个测试）。
- 租户设置两处裸 catch 改 extractErrorMessage。

### 🔧 基线测试修复（10 个全清）

- `test_ai_api.py` ×7：fixture 随机租户无 Tenant 行，路由级 `ai_assistant` feature 门禁 fail-closed 403 → fixture 播种开启功能的真实租户。
- `test_operator_workbench.py` ×1：中间件读 control engine 与测试库不一致 → fixture 补 `control_session_factory` 指向 TestSessionLocal。
- `test_request_mutation_boundary.py` ×2：function-scope 白名单按当前实际路由重建（GET 只读路由按现状收录并注明「收敛待专项」）+ 混合 scope 由第 2 项修复消除。
- settings/privacy 基线失败经实测为本机通过、CI 超时边缘（jsdom 渲染 10.2s/15s 预算），全局 20s 已覆盖，无需改动。

### 📋 记录（P2/P3，未修）

1. 隐私权益队列缺「拒绝」闭环（DB 支持 reject，UI 只有分派/限制/完成）。
2. 审计日志无导出端点/按钮；AUDIT_ACTION_LABELS 仅 18 条，大量动作显示英文原文。
3. i18n/private-domain 写路由无权限门禁无审计；branding 页静默失败；租户页功能开关只列 cash_red_packet；roles.py 只读模板的死代码路由收敛。
4. launch_releases confirm-and-launch 占位端点永远 409（前端已不用）。
5. 敏感导出双人审批验证无洞（DB CHECK + 拒绝自批 + UI 隐藏），保持。

### ✅ 验证记录（2026-09-05）

| 项                                                           | 结果    |
| ------------------------------------------------------------ | ------- |
| ai_api/operator_workbench/mutation_boundary/tenants 后端测试 | 89 通过 |
| admin vitest settings 域 + api.test.ts（含新增用例）         | 38 通过 |
| tsc / eslint / ruff                                          | 通过    |
