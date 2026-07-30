---
status: active
last_verified: 2026-07-30
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
