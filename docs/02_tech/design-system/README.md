---
status: active
last_verified: 2026-07-30
accuracy: high
---

# 一码通设计体系

> 来源：Wayfinder 地图（Beads `yimatong-ew87`）八项决策的落地契约。任何与本文档冲突的既有实现，以本文档为准并逐步迁移。

## 品牌方向

**方向 A · 自然可信**。主色品牌绿 `#16a34a`，琥珀橙 `#f59e0b` 为 `brand.accent`（增长/活力场景专用：积分、奖励、活动氛围，与 feedback 色系分离）。大圆角（6/10/14/18 + pill）、柔和阴影（6–13% 透明度）。B 端清爽不冰冷，C 端传递"新鲜、安心"。

## 三端角色

| 端              | 技术栈               | 主题                                            | 角色                             |
| --------------- | -------------------- | ----------------------------------------------- | -------------------------------- |
| `apps/admin`    | Next.js + antd 6     | 品牌绿，light/dark 双主题                       | 租户品牌方内部运营工具           |
| `apps/platform` | Next.js + antd 6     | **与 Admin 共用绿色主题**，仅靠产品名/Logo 区分 | 平台管理                         |
| `apps/h5`       | Next.js + Tailwind 4 | 品牌绿，仅浅色                                  | 消费者扫码触点，支持租户受控定制 |

## 单一事实来源

`@yimatong/design-tokens`（`frontend/packages/design-tokens`）是三端设计 token 的唯一来源：

- **三层结构**：`lib/primitives.ts`（原语，不随主题变）→ `lib/themes.ts`（light/dark 语义层，`satisfies SemanticTokens` 强制键一致）→ `getAntdTheme(mode)`（antd 派生）
- **CSS 产物**：`dist/theme.css` 由 `scripts/build-css.mjs` 从 TS 源生成并提交入库；改 token 后必须运行 `pnpm --filter @yimatong/design-tokens build`，CI 用 `check` 防漂移
- **静态预览页**：`preview/index.html` 同为生成物，双击可看色板/语义表/对比度/排版/组件样张/租户定制演示
- **消费约定**：应用只能从包根入口（`@yimatong/design-tokens`）或 `./tokens`、`./theme.css` 子路径导入，不得直接引用 `lib/`

## 文档目录

| 文档                                 | 内容                                                        |
| ------------------------------------ | ----------------------------------------------------------- |
| [tokens.md](tokens.md)               | 三层 token 结构、命名规范、语义集、度量刻度、演进策略       |
| [components.md](components.md)       | Admin/Platform 页面骨架、组件选型硬规则、反馈分级、边角状态 |
| [h5-branding.md](h5-branding.md)     | H5 租户定制五槽位、主色校验、三层回退、CSS 变量注入         |
| [accessibility.md](accessibility.md) | WCAG AA 基线、四项交互基线、走查清单                        |

## 演进规则

- 新增 token = minor；改值 = minor + 双端视觉走查；重命名/删除 = major + 一个周期 deprecated 别名
- 禁止语义变更（同名不同含义），视同重命名 + 新增
- token 增删改受测试门禁：键集合 parity、对比度契约、产物防漂移，`pnpm --filter @yimatong/design-tokens check` 必须全绿
