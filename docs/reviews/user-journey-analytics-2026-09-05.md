# User Journey Review Report - M8 数据分析（analytics/campaign-analytics/gmv/工作台）

### 📊 Review Overview

- Review Date: 2026-09-05
- Scope: 工作台首页、深度分析、扫码统计、活动看板、GMV 归因（看板/ROI/订单导入）
- 方式: 代码驱动审查 + 基线测试根因定位（代码评审模块）
- Issues Found: 3 P0（时区口径混用、GMV 导入完全不可用、夹具漂移）+ 2 P1（假空态遍布、导出错误误导）+ 9 P2/P3；P0/P1 + ROI 口径（P2）修复
- Journey Score（修复后均值）: 4.1

---

### 🔴 P0（全部已修复）

1. **analytics 日期口径混用**: `get_scan_stats`/`get_campaign_scan_stats` 默认本地 `date.today()`，而 `get_dashboard` 与 `aggregate_daily_stats` 按 UTC 日切——中国租户「今日扫码」有 8 小时错位、趋势日期与聚合口径不一致，且集成测试在 CST 0-8 点必然失败（时辰型 flaky）。**Fix**: 三条读路径统一 UTC 日切（与权威聚合一致），测试同步；docstring 注明口径。**待决策**: 是否改 Asia/Shanghai 日切（涉及存量聚合数据重算）。
2. **Admin GMV 订单导入完全不可用**: 缺必填 `Idempotency-Key` header 与顶层 `source_system`；占位示例把 source_system 写进 item、漏必填 `order_time`；部分失败仍提示「导入成功」。**Fix**: 四处全修，新增 3 个组件测试。
3. **夹具漂移**: `test_analytics_code_stats.py` 缺 Idempotency-Key + 直接 activate 409（基线剩余 2 个 setup error 根因）→ 补 header + 完整交付链。

### 🟡 P1（全部已修复）

- **假空态遍布**: CampaignAnalyticsContent 趋势失败置空数组、GMV Dashboard/ROI 静默 catch、工作台 6 个子组件静默吞错。**Fix**: 3 个主内容组件改「错误 Alert + 重试」（区分加载失败与暂无数据）；6 个卡片子组件补 `message.error(extractErrorMessage)`。
- **导出错误误导**: 固定「请确认管理员权限」掩盖限流/服务错误。**Fix**: 429 读 Retry-After 提示、403 权限文案、blob 错误体解析 detail；代运营受限模式隐藏导出按钮（原必然 403）。

### 🟠 P2（已修复 1 项）

- **ROI 缺预算算 0**: 未设预算活动显示红色 0x 并拉低平均 ROI。**Fix**: 后端无预算返回 null（schema 放宽），前端渲染「—」且不计入平均。

### 📋 记录（P2/P3，未修）

1. 活动分析导出与页面内容错位（导出固定 campaign_dashboard，页面主体是 scan-stats；后端 scan_stats 导出无入口）。
2. 「扫码数」恒空列；UV 文案口径（逐日求和）；归因率 Alert 常驻；ChannelHealth 无 feature 门控每次 403；RangePicker 清空无效；OrdersTab 422 数组未走 extractErrorMessage（导入主路径已修）；RecentEvents 裸解析 localStorage role。
3. admin 全量 vitest 在并行负载下仍有 1-7 个 20s 超时类浮动失败（单跑全绿）——建议 CI 分片或重文件限并发。

### ✅ 验证记录（2026-09-05）

| 项                                               | 结果                                   |
| ------------------------------------------------ | -------------------------------------- |
| backend analytics/gmv/campaign_scan 相关         | 127 通过                               |
| admin vitest gmv/analytics 相关（含新增 3 用例） | 21 通过                                |
| tsc / eslint / ruff                              | 通过                                   |
| GMV acceptance 2 个迁移链断言失败                | 干净工作区同样失败（既有，非本次引入） |
