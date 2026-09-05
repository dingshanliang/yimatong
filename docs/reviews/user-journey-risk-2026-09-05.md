# User Journey Review Report - M7 风控中心

### 📊 Review Overview

- Review Date: 2026-09-05
- Scope: 风控中心看板、实时告警（SSE）、规则启停、告警处置、窜货处置、拦截/暂停/通知管理
- 方式: 代码驱动审查（代码评审模块）
- Issues Found: 3 P0（两个统计指标恒 0、SSE 空转）+ 7 P1（失败分支静默、处置弹窗时序、规则启停无确认）；P0/P1 全部修复
- Journey Score（修复后均值）: 4.0

---

### 🔴 P0（全部已修复）

1. **「待处理告警」恒 0**: risk-center 调不存在的 `/risk-dashboard/alerts`（404）。**Fix**: 改调 `GET /risk-alerts?resolved=false&page_size=1` 取 total。
2. **「未处理窜货线索」恒 0**: 前端读 `unresolved_count`，后端 summary 无此字段。**Fix**: `get_diversion_summary` 补 `unresolved_count`（独立 resolved=false 查询），测试断言更新。
3. **SSE 实时告警空转**: `_broadcast_alert` 全仓库无调用点（`risk.alert` 事件无订阅者）。**Fix**: risk_dashboard 注册 `init_alert_broadcaster()`（lifespan 调用、幂等）订阅 event_bus 并广播 SSE；前端铃铛改 Popover（拉最近 10 条告警 + 与实时消息合并去重）。新增 5 个单测（emit→队列/租户隔离/满队列丢弃/幂等注册）。

### 🟡 P1（全部已修复）

- 规则启停失败吞 detail 且 expected_version 不刷新（连续 409）→ extractErrorMessage + finally mutate()；block 类规则停用加 Popconfirm 二次确认。
- 窜货处置 Modal「先关窗后请求」→ async onOk + confirmLoading，成功才关；备注 2000→500 对齐后端 reason 上限；失败 extractErrorMessage。
- 告警「处理」无任何错误分支、通知已读无错误分支 → 补 try/catch + 失败 toast。
- Interceptions/Notifications/Pauses 三个 Tab 加载失败呈假空态 → 复制 AlertsTab 的错误 + 重试模式。

### 📋 记录（P2，未修）

1. `/risk-center` 与 `/risk` 双菜单同名「风控中心」，`/risk-dashboard` 无菜单入口——IA 需产品决策。
2. RulesTab 无规则编辑/删除/配置查看入口（后端支持 PATCH/DELETE）；新建规则空 config 必 422 无引导（需按类型给配置模板）。
3. DELETE /risk-rules 实为禁用（语义与 REST 相悖）；risk/evaluate 死接口且评分无时间窗；规则错误 detail 英文直出（risk_authority.py）；解冻无原因无幂等键；导出按钮对非 admin 可见且 blob 错误体不解析；通知未读数只统计当前页（应调 unread-count）；SSE 队列进程内存态多 worker 失效。

### ✅ 验证记录（2026-09-05）

| 项                            | 结果    |
| ----------------------------- | ------- |
| 后端 risk 相关测试            | 55 通过 |
| SSE 广播新单测                | 5 通过  |
| admin vitest risk 相关 5 文件 | 16 通过 |
| tsc / eslint / ruff           | 通过    |
