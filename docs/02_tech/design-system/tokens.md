---
status: active
last_verified: 2026-07-30
accuracy: high
---

# 设计 Token 规范

> 实现：`frontend/packages/design-tokens`。本文是结构与用法契约。

## 三层结构

```
lib/primitives.ts    # 原语层：色阶/间距/圆角/阴影/字体（不随主题变化）
lib/themes.ts        # 语义层：lightTokens / darkTokens（satisfies SemanticTokens，键集合必须一致）
lib/antd.ts          # getAntdTheme(mode)：antd 6 ThemeConfig 派生
dist/theme.css       # 生成物：Tailwind 4 @theme + 语义 CSS 变量（勿手改）
```

- **原语 → 语义 → 组件**：业务代码消费语义 token，不直接消费色阶档位
- **命名**：点分语义命名（`color.bg.surface`、`text.primary`、`border.base`），与 antd Alias Token 语义对齐
- **暗色不是反色**：dark 主题独立赋值（如品牌主色暗色下提亮为 `#4ade80`）

## 原语层

### 色阶

品牌绿与琥珀橙各 11 档（50–950），锚点 + 人工微调：

| 色板           | 锚点（500） | 用途                              |
| -------------- | ----------- | --------------------------------- |
| `color.brand`  | `#16a34a`   | 品牌主色                          |
| `color.accent` | `#f59e0b`   | 琥珀橙，`brand.accent` 的色阶来源 |

feedback 语义色（success/warning/danger/info）定义在语义层，不单独建原语色板。

### 度量

| 类别     | 刻度                                                                |
| -------- | ------------------------------------------------------------------- |
| 间距     | 4px 基数：4/8/12/16/24/32/48/64（Tailwind 4 动态 spacing 原生兼容） |
| 圆角     | sm 6 / md 10 / lg 14 / xl 18 / pill 999                             |
| 阴影     | sm/md/lg 三级柔和投影（6–13% 透明度，带绿色基调）                   |
| 触控目标 | AA 下限 24 / 桌面 32 / 触控 44 / 主 CTA 48                          |
| 焦点     | 2px 外描边 + 2px offset（`focus.ring` 语义色）                      |

### 字体

| 组                 | 内容                                                                                                         |
| ------------------ | ------------------------------------------------------------------------------------------------------------ |
| `font.family.body` | 系统字体栈：`-apple-system / PingFang SC / Hiragino Sans GB / Microsoft YaHei / sans-serif`，不引入 Web 字体 |
| `font.family.mono` | 系统等宽栈（SF Mono / ui-monospace），用于批次码、ID                                                         |
| `font.size`        | 12/13/14/16/18/20/24/30/36 一套刻度两端共用；Admin 基准 14，H5 基准 16                                       |
| `font.lineHeight`  | heading 1.3 / body 1.6 / dense 1.4（密集表格）                                                               |
| `font.weight`      | regular 400 / medium 500 / semibold 600 / bold 700（bold 仅数字场景）                                        |

数字场景（表格数值、金额、扫码量）一律 `font-variant-numeric: tabular-nums`（`theme.css` 提供 `.ymt-tabular-nums` 工具类）。

## 语义层

v1 最小可用集（light/dark 双份）：

| 组               | 键                                                                            |
| ---------------- | ----------------------------------------------------------------------------- |
| `color.bg`       | canvas / surface / elevated / muted                                           |
| `color.text`     | primary / secondary / tertiary / inverse                                      |
| `color.border`   | base / strong                                                                 |
| `color.brand`    | primary / subtle / accent                                                     |
| `color.action`   | primary / primaryHover / primaryActive / onPrimary / link / accent / onAccent |
| `color.feedback` | success / warning / danger / info（各含 `*Bg`）                               |
| `color.focus`    | ring                                                                          |
| `color.chrome`   | sider / header / tableHeader / rowHover / rowSelected                         |
| `shadow`         | surface / overlay                                                             |

## 消费方式

**antd 端（Admin/Platform）**：

```ts
import { getAntdTheme } from "@yimatong/design-tokens";

<ConfigProvider theme={getAntdTheme(mode)} />
```

dark 主题 = `darkAlgorithm` + 语义覆盖；components 级只放跨端一致的品牌修正（Layout/Menu/Table 等），页面级特殊需求留在应用侧。

**Tailwind 端（H5）**：

```css
@import "@yimatong/design-tokens/theme.css";
```

- 语义变量：`--ymt-color-bg-canvas` 等，`:root` / `html[data-theme="dark"]` 双声明
- 工具类：`bg-canvas`、`text-foreground`、`bg-brand`、`text-success` 等经 `@theme inline` 映射
- 原语工具类：`bg-brand-500`、`text-md`、`radius-lg`、`shadow-md`、`font-mono`

## 演进策略

| 变更       | 级别  | 策略                             |
| ---------- | ----- | -------------------------------- |
| 新增 token | minor | 直接加                           |
| 修改值     | minor | 设计评审 + 双端视觉走查          |
| 重命名     | major | 旧名 deprecated 别名保留一个周期 |
| 删除       | major | 先全仓 grep 零引用               |
| 语义变更   | 禁止  | 视同重命名 + 新增                |

门禁（`pnpm --filter @yimatong/design-tokens check`）：light/dark 键集合 parity、对比度契约（见 [accessibility.md](accessibility.md)）、生成产物防漂移、typecheck。
