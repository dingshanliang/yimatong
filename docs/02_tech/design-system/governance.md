---
status: active
last_verified: 2026-07-31
accuracy: high
---

# 设计体系治理：防漂移机制

> 来源：Wayfinder 地图（Beads `yimatong-pifo`）决策落地。**自动化门禁为主，人工走查兜底**——防漂移靠门禁不靠自觉。迁移批次与验收标准见 [migration-plan.md](migration-plan.md)。

## 一、CI 自动化门禁

### 规则范围（本次落地）

| 方向                       | 规则                                                                                                       | 覆盖                                                  |
| -------------------------- | ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| 禁硬编码（H5）             | `eslint-plugin-tailwindcss` 的 `no-arbitrary-value`                                                        | 拦截 `text-[13px]`、`bg-[#16a34a]` 等 Tailwind 任意值 |
| 禁硬编码（Admin/Platform） | ESLint `no-restricted-syntax` 拦截 JSX inline style 中的 `color`/`fontSize`/`backgroundColor` 硬编码字面量 | inline style                                          |
| 禁硬编码（CSS）            | stylelint `color-no-hex` + `declaration-property-value-disallowed-list`（px 字号等）                       | `.css` / styled-jsx / CSS Modules                     |
| token 产物快照             | Vitest `toMatchFileSnapshot` 对 `dist/theme.css` 产物快照                                                  | token 演进意外变更                                    |
| 对比度                     | 现有 `contrast` 测试（`contrast.ts` + `accessibilityPairs`）纳入 CI                                        | WCAG AA 对比度契约                                    |

token 变更 PR 清单：改 `primitives.ts`/`themes.ts` 必须同步 `pnpm --filter @yimatong/design-tokens build`、更新快照、升版本号、记 changelog。

### 明确不做（留后续）

- pa11y / `@axe-core/cli` 对 H5 关键页的补充扫描（需要起服务环境，后续可接入 Playwright E2E 层）
- 自定义插件 `@yimatong/eslint-plugin-design-tokens`（中长期，沉淀白名单比对与自动修复）
- pre-commit hook（唯一权威门禁在 CI；本地只提供 `pnpm lint:design` 自查脚本）

### 接入位置

- `frontend/packages/design-tokens` 增加 lint 入口；root `package.json` 暴露 `lint:design`
- `.github/workflows/ci.yml` 新增 `design-gates` job

### 豁免与收敛机制（baseline 随批次收敛）

1. 门禁落地时，用 baseline 文件一次性收录全部现状违规，CI 即转绿、新增违规即被拦
2. 每个迁移批次完成时，移除该批页面在 baseline 中的条目（批次验收的一部分）
3. 批 3 完成时 baseline 必须为空，规则从 warning 转 error
4. 个别确有理由的豁免用行内 disable 注释，**必须带理由**（走查时核对）

详细方案对比与配置示例见调研报告：`research/design-gates` 分支 `docs/research/design-gates-ci.md`。

## 二、人工走查工作流

自动化覆盖不了的（视觉一致性、交互模式、插画/空状态风格、深色模式目测）由人工走查兜底。**事件驱动，不设定期会议**。

### 触发时机

- **迁移期内**：每个迁移批次验收时，对该批页面走查
- **迁移完成后**：每个涉及 UI 的 PR 附自查清单

### 走查清单（分层）

**基础项（所有页面）**：

- [ ] 视觉一致性：对照静态预览页样张（`preview/index.html`）无违和
- [ ] 深色模式目测正常（Admin/Platform）
- [ ] a11y 交互基线：focus-ring 可见、触控目标达标、状态双编码、字号不低于下限
- [ ] 空/加载/错误态完整，空状态符合 V2 品牌容器规范（见 [components.md](components.md) 边角状态）

**骨架项（仅核心流程页）**：

- [ ] 列表页四段式骨架、Table/Modal/Drawer 选型硬规则
- [ ] 三级反馈使用正确、危险操作有确认词
- [ ] 新记录排第一契约

### 记录与归档

- 批次验收 PR 模板内嵌上述清单勾选项，执行人逐项打勾
- 验收结论与遗留问题写进对应批次 beads 票的 notes

### 走查人

- 迁移执行人按清单自查勾选
- **批 0（样板批）与批 2（核心流程页）由用户抽查确认**；辅助页免检

## 三、antd 状态色契约（Tag preset 单一事实来源）

> 2026-07-31 收口：Admin/Platform 全量 `<Tag color>` 不再使用硬编码 hex，改走 antd **preset 状态名**，由 `getAntdTheme` 的 `colorSuccess/Warning/Error/Info` 种子 token 计算颜色。

### 机制

- `@yimatong/design-tokens/lib/antd.ts` 的 `getAntdTheme(mode)` 把 `colorSuccess/Warning/Error/Info` 绑定到 feedback 语义 token；深色模式叠加 `darkAlgorithm`。
- 因此 antd `<Tag color="success|processing|warning|error|default">` 自动：① 颜色来自 design tokens 单一事实来源；② 深色模式自适应（无需维护双色）。
- 集中映射 `apps/{admin,platform}/src/lib/status-colors.ts` 导出 `STATUS_COLORS`（值即 preset 名）与 `HEX_TO_STATUS`（历史 hex→preset 速查），是业务状态→语义色的唯一入口。

### 语义口径

| preset (STATUS_COLORS key) | 语义                                  | 历史 hex |
| -------------------------- | ------------------------------------- | -------- |
| `success`                  | 成功 / 活跃 / 已完成 / 健康           | #16a34a  |
| `processing`               | 进行中 / 待处理 / 信息 / 蓝           | #1d4ed8  |
| `warning`                  | 暂停 / 警告 / 预警 / 中危 / 琥珀类    | #f59e0b  |
| `error`                    | 失败 / 危险 / 已终止 / 高危 / 红      | #b91c1c  |
| `neutral`（→ "default"）   | 默认 / 草稿 / 未激活 / 占位（中性灰） | #8c8c8c  |

### 使用约束

- **`STATUS_COLORS` 仅用于 antd `<Tag color>`**。不要把 preset 名喂给 inline style 的 `color`/`background`、`Progress strokeColor`、`Badge status` 或 antd `Timeline color`——那些需要真实 CSS 颜色值，preset 名无效。
- 需要真实颜色时走 CSS var（如 `Timeline` 用 `var(--ymt-color-feedback-info)`），仍来自 token。
- 新增业务状态色：先归入上述语义口径，引用 `STATUS_COLORS.<preset>`；不要新增本地 hex 或本地 STATUS_MAP。

### 收口范围（已验收）

- 全量 `<Tag color="#hex">` 与本地 STATUS_MAP（约 50 文件）已迁移到 `STATUS_COLORS` 引用。
- 深色模式已浏览器目测：campaigns 页 warning Tag 浅色 `#92400e`、深色 `#d9b644`，均来自 feedback token 且自适应。
