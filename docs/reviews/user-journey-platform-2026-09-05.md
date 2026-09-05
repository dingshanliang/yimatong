# User Journey Review Report - M11 平台后台

### 📊 Review Overview

- Review Date: 2026-09-05
- Scope: 平台登录（cookie+CSRF）、租户生命周期、套餐、额度监控、客户健康度、邀请码、审计日志
- 方式: 代码驱动审查（代码评审模块）
- Issues Found: 4 P1（登录吞 detail、401 自刷新、健康刷新超时英文报错、错误伪装成「租户不存在」）+ 1 中（email 大小写敏感）+ 6 P2；P1 + email + 3 个 P2 修复
- Journey Score（修复后均值）: 4.1
- Cookie 边界专项核查：控制面 cookie-only/Bearer 拒绝/租户 cookie 拒绝/Origin+CSRF 双校验/e2e fail-closed 均无问题

---

### 🟡 P1（全部已修复）

1. **平台登录吞 detail**: 限流 429（含 5 分钟 Retry-After）/503/500 统一显示「密码错误」，管理员被锁死仍继续重试。**Fix**: `buildPlatformLoginErrorMessage` 透传 detail，429 拼入「约 N 秒后可重试」（Retry-After 大小写兼容）；新增 6 个测试。
2. **登录失败触发 401 拦截器自刷新**: 在 /login 页 `location.href="/login"` 整页 reload 冲掉错误 toast。**Fix**: 拦截器豁免 `/platform/auth/login`。
3. **健康度全量重算被打断**: axios 15s 超时 < 同步重算耗时，英文 "timeout of 15000ms exceeded" 且与实际结果不一致。**Fix**: 单请求 120s 超时 + timeout 中文文案。
4. **错误伪装成「租户不存在」**: 详情页 `!data` 一律渲染不存在，列表页忽略 SWR error。**Fix**: 404 与其他错误区分，错误 Alert + 重试（详情 + 列表）。

### 🟠 中（已修复）

- **登录 email 大小写/空白敏感**: normalized 变量算了没用，凭证比较仍用原始输入——「Platform@Yimatong.CN」401 且白耗限流额度。**Fix**: 比较改用归一化值。

### 🟠 P2（已修复 3 项）

- 套餐停用闭环：编辑表单补 `is_active` Switch（后端已支持），卡片显示「已停用」Tag——「当前套餐已停用」兜底流程此前从 UI 永远触发不了。
- 邀请码误停用后无法重新启用 → 补「重新启用」操作。
- 审计日志结束日整天被排除 → end_time 取 `endOf("day")`。

### 📋 记录（P2/P3，未修）

1. 审计日志仅 limit 截断（最大 500）无分页/游标，超 200 条历史不可回看（合规面）；details 字段返回但 UI 未展示。
2. 套餐 name 自由文本 vs 后端枚举（应改 Select）；创建租户激活链接签发失败静默无日志；seed 套餐缺 quota 键致额度列「无法判断」、seed 审计 action 格式与真实写入不一致；静态 message 未走 App.useApp；service-providers N+1；PATCH /config 零校验。
3. **部署陷阱（informational）**: 写请求 Origin 必须严格等于 `PLATFORM_PUBLIC_URL`（默认 3002），反代/映射端口未同步时「能读不能写」症状隐蔽——建议部署文档/启动检查显式校验。

### ✅ 验证记录（2026-09-05）

| 项                                    | 结果    |
| ------------------------------------- | ------- |
| platform/invite_codes 后端测试        | 51 通过 |
| platform 前端 vitest（含新增 6 用例） | 31 通过 |
| tsc / eslint / ruff                   | 通过    |
