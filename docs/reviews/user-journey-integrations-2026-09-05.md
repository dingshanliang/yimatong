# User Journey Review Report - M9 导出/导入与集成

### 📊 Review Overview

- Review Date: 2026-09-05
- Scope: 码表导出、批量导入、集成管理（webhook/API Key/企微/connector）、CRM 同步页
- 方式: 代码驱动审查 + 基线测试根因定位
- Issues Found: 4 P0（webhook 开关/删除全坏、integration 三端点越权、导入错误链路死路、crm-sync 空壳假成功）+ 3 P1；P0/P1 全部修复
- Journey Score（修复后均值）: 3.9

---

### 🔴 P0（全部已修复）

1. **Webhook 启用/禁用/删除完全不可用**: PATCH/DELETE 缺必填 `If-Match: config_version` 头，每次 422。**Fix**: 携带版本头 + 409 冲突提示刷新 + extractErrorMessage 统一 + 剪贴板降级 + batch_size 显隐修正；新增 7 个组件测试。
2. **integration.py 三端点无权限（越权）**: batch-import/inventory-sync/customer-sync 仅需登录，API Key 可越权写且绕过导入配额。**Fix**: 分别挂 `product:create` / `product:update` / `consumer:detail`（均为已存在权限码，与同语义端点一致；报告注：catalog:manage 等候选码不存在）；新增 6 用例权限回归。
3. **导入错误链路死路**: 页面调用不存在的 `/imports/records`（假表格）；`/imports/excel` 返回 200+`success:false+errors[]` 时按成功提示，行级错误不可见；上传 action 与 axios baseURL 不一致。**Fix**: 死代码改诚实 Empty；success:false 渲染「成功 X 失败 Y」+ 前 5 条错误（可测纯函数）；上传 action 用共享 `API_BASE_URL` 常量；新增 4 个测试。
4. **crm-sync 空壳页假成功**: 后端无任何 `/crm/*` 路由，映射/日志恒空、手动同步假提示「将在下一个 cron 周期自动执行」。**Fix**: 页面诚实声明「CRM 同步功能尚未接入」+ 按钮禁用 + 不可用提示替代静默置空；新增 4 个测试。（后端补 CRM 接入属产品立项，记录待决策。）

### 🟡 P1（全部已修复）

- 导出失败吞 detail：改 `extractErrorMessage` + 套餐到期专门文案（与 ApiKeysTab 同款）。
- 基线测试：`test_sse_ticket.py` 2 个失败为夹具未播种租户/未开 risk_module（功能门禁演进），按 `test_risk_dashboard.py` 模式修 fixture 后 7/7 绿。exports/ApiKeysTab 4 个基线失败经实测在 HEAD 全部通过（属此前 vitest 超时类 flaky，全局 20s 已覆盖）。

### 📋 记录（P1/P2，未修）

1. Webhook 无「测试发送」端点与按钮；DeliveriesTab 无投递详情入口（后端已有 deliveries/{id}）。
2. imports 配额耗尽整单回滚丢弃行级错误；integrations loadStatus 错误态与空态混淆。
3. wecom callback_token 明文回传（复制所需）建议加审计。

### ✅ 验证记录（2026-09-05）

| 项                                                                    | 结果                             |
| --------------------------------------------------------------------- | -------------------------------- |
| sse_ticket/integration/webhook/commerce/wecom 后端测试                | 82 通过（含新增权限回归 6 用例） |
| admin vitest integrations/imports/crm-sync/exports（含新增 ~17 用例） | 47 通过                          |
| tsc / eslint / ruff                                                   | 通过                             |
