# 全产品系统用户旅程评审汇总报告

- 评审日期：2026-09-05 ~ 2026-09-06
- 方法：user-journey-review 技能方法论，按 11 个模块迭代「代码驱动旅程映射 → 三维度审查（功能完整性/体验流畅度/错误处理）→ 修复 → 验证 → 提交」；核心模块（认证/H5 扫码/码与落地页/营销）叠加 seed 数据真实浏览器走查
- 范围：Admin 品牌后台 13 个菜单组、消费者 H5 全旅程、平台后台、backend 全部 API 域
- 分模块报告：`docs/reviews/user-journey-{auth,h5-scan,codes-pages,catalog,growth,channels,risk,analytics,integrations,settings,platform}-2026-09-05.md`

## 成果总览

| 模块          | 关键修复                                                                                                                                                                         | 报告                      |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- |
| P0 环境       | seed 清理对齐库级外键约束（拓扑序 + 不可变表跳过）；tests 子目录 `__init__.py` 修复全量收集冲突                                                                                  | Phase 0 commit            |
| M1 认证       | 锁定账户 423 可区分提示（原误导为密码错误）；重置链接失效持久错误页；change-password 限流+审计；静默刷新网络抖动不再硬登出；演示密码面板仅 dev 构建                              | user-journey-auth         |
| M2 H5 扫码    | **作废码/假码不再显示「网络异常」**（终态契约 410/404/503 直达落地页）；微信授权失败 303 回跳码页（原 webview 裸 JSON 死路）；领取终态文案直显；回访凭证防损坏；跨码凭证污染修复 | user-journey-h5-scan      |
| M3 码与落地页 | 编辑器非草稿态死路（创建草稿入口）；导出重复审计；幂等键失败轮换（CAS 死循环）；单码作废导出警告；批次终态派生展示；15 处吞 detail；接管失败行重试/明细                          | user-journey-codes-pages  |
| M4 商品资料   | **CSV 导入 SKU 归属错误（数据正确性）**；批次过期态展示；catalog 冲突文案中文化；品牌删除入口                                                                                    | user-journey-catalog      |
| M5 营销       | **`campaign:write` 权限码不存在 → 复购券/工作台/通知管理端全 403**（API+DB 权威函数双修 + 迁移）；领取套餐门顺序（过期租户 401→403）；重试发放按钮生产必失败移除                 | user-journey-growth       |
| M6 渠道       | **regional.py 27 路由无 RBAC（越权）**；经销商门户窜货预警永远为空（专用 portal 端点）；幂等键轮换 + CAS 409 中文反馈                                                            | user-journey-channels     |
| M7 风控       | 风控中心两个指标恒 0（调不存在接口/字段）；**SSE 实时告警空转**（event_bus 订阅打通 + 铃铛面板）；规则启停失败处理与二次确认                                                     | user-journey-risk         |
| M8 分析       | **日期口径混用（本地 vs UTC 8 小时错位）**；GMV 订单导入完全不可用（缺必填字段/头）；假空态改错误态；ROI 无预算算 0 误导                                                         | user-journey-analytics    |
| M9 集成       | **Webhook 开关/删除全坏（缺 If-Match）**；integration 三端点越权写；导入行级错误不可见；crm-sync 空壳假成功诚实化                                                                | user-journey-integrations |
| M10 设置      | **合规设置假保存（静默丢数据）**——注意最终实现改走管理端点而非放宽 /me（见回归收尾）；risk_evaluate 混合 scope；代运营撤销吞错                                                   | user-journey-settings     |
| M11 平台      | 登录限流 429 被显示为密码错误；email 大小写误拒；健康重算超时打断；错误伪装「租户不存在」；套餐停用闭环                                                                          | user-journey-platform     |

## 回归验证（最终状态）

- 后端全量 `pytest`：**2674 passed / 2 failed → 2 个失败已修复并提交**（/me 合规契约还原 + webhook 摘要固定 pepper）；acceptance 默认排除
- admin vitest：401 用例（2 个并行负载 flaky 已通过 30s 预算治理，建议后续 CI 分片根治）
- platform vitest：31 通过；h5 vitest：142 通过
- `pnpm check`（depcruise）、三应用 `tsc --noEmit`、admin/platform `next build` 全绿；h5 build 需 https API URL（`.env.local` http 值触发 CSP 校验，既有环境行为）
- e2e 未跑：本机 8000 端口被其他项目容器（fsc-cfm）占用，Playwright API server 无法绑定；需在该项目停机后运行
- 全部提交在 dev 分支（Phase 0 起 14 个 commit），未 push

## 待决策清单（产品/架构）

1. **H5 scan_token 存储按租户隔离**（M2-M7）：现全局 localStorage 键跨码污染，靠 401 自愈；需存储键体系专项。
2. **OAuth 成功回跳自动续领**（转化优化）：授权后仍需手动再点领取。
3. **领取链路 IP 绑定放宽**：结果页轮询已不校验 IP，领取仍校验——弱网体验与安全的权衡。
4. **导出排除已作废码**：作废单码后整批不可导出（已加警告），是否支持排除式导出需改合约。
5. **analytics 日切时区**：已统一 UTC；改 Asia/Shanghai 需重算存量聚合。
6. **非微信 UA 扫码 HTML 降级页**不理解 modules DSL（近乎空白）：改 H5 渲染或 Jinja 支持，需专项。
7. **CRM 同步**：页面已诚实声明未接入；是否立项接入由产品定。
8. **多工作区演示账号语义**：agency@demo.com 同时存在于 demo 品牌租户与 demo-agency，登录页演示按钮预填 slug=demo 登录的是品牌租户副本。
9. **access_token 存 localStorage 的 XSS 窗口**：改内存 token + 静默刷新需专项评估。
10. **平台后台审计日志分页**：现 limit 截断，超 200 条历史不可回看（合规面）。

## 建议的后续专项

- `channels/page.tsx`（2985 行）拆分；`channels/_components/` 633 行死代码清理
- admin vitest CI 分片（根治并行负载 flaky）；重文件（privacy/products/[id]）抽取轻量渲染
- i18n/private-domain 写路由补权限与审计；审计动作字典补全
- 风控规则编辑/删除 UI、配置模板；平台审计分页
