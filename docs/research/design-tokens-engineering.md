---
status: active
last_verified: 2026-07-29
accuracy: high
---

# design-tokens 包工程实践调研

> Beads: yimatong-ew87.3（wayfinder 研究票）
> 调研日期：2026-07-29
> 背景：前端 pnpm workspace（apps/admin、apps/platform 用 antd 6；apps/h5 用 Tailwind CSS 4 + Headless UI；packages/shared 为无构建直出 TS 的 `@yimatong/shared`）。已决定新建 `packages/design-tokens` 作为设计 token 单一事实来源，需派生 antd 6 的 ThemeConfig（light/dark 双主题）与 Tailwind 4 的 `@theme` / CSS 变量。

---

## 1. 设计 token 包的组织形态：三种业界路线对比

### 事实发现

业界主流有三种组织形态：

**A. TS 对象直出**：token 就是 TypeScript 常量/对象，包直接导出源码或 tsc 产物。Material UI（`createTheme` 的 palette 对象）、Chakra UI、Radix Themes 主题包均属此类。消费方直接 `import` 获得完整类型推导。

**B. JSON（DTCG 格式）+ Style Dictionary 构建**：token 以 W3C DTCG 标准 JSON（`$value` / `$type` / `$description`）存储，用 Style Dictionary v4 构建出多平台产物（CSS 变量、SCSS、TS 常量、iOS/Android）。Style Dictionary v4 已稳定，ESM-only、异步 API、原生支持 DTCG；Tokens Studio 团队参与共同维护，配套 `@tokens-studio/sd-transforms` 处理 Tokens Studio 特有语法。DTCG 规范 2025.10 版已发布（color、border、shadow、dimension 等模块）。官方有 `tokens-studio/sd-tailwindv4` 示例，可直接生成 Tailwind 4 产物。

**C. Tokens Studio 生态**：设计师在 Figma 中用 Tokens Studio 插件（免费/$10/月 Pro）维护 token，同步到 Git 仓库中的 JSON，再由 Style Dictionary 构建。即「Tokens Studio 定义 → DTCG JSON 交换 → Style Dictionary 构建」三段流水线。

### 成本收益对比（针对本场景：三端消费 + pnpm workspace）

| 维度 | A. TS 对象直出 | B. JSON + Style Dictionary | C. Tokens Studio 生态 |
|---|---|---|---|
| 初始成本 | 极低（与 shared 包同构，零新依赖） | 中（引入 style-dictionary + sd-transforms + 构建脚本 + DTCG 格式学习） | 高（设计侧工具链 + 同步流程 + 构建管线） |
| 类型安全 | 原生 TS，导出即类型，改动即时反映到消费方编译错误 | 需要生成 `.d.ts` 或 TS 产物，类型是构建产物 | 同 B |
| antd ThemeConfig 派生 | 直接写映射函数，类型可校验 | 需生成 TS/JS 产物再映射 | 同 B |
| Tailwind 4 @theme 生成 | 写一个小 Node 脚本从 TS 源生成 `theme.css`（产物入库） | Style Dictionary 原生支持 css/variables 与 Tailwind 4 输出 | 同 B，且有现成示例 |
| 设计师协作（Figma 源） | 不支持，设计师需改 TS | 可后接 DTCG 转换 | 原生支持，设计师自治 |
| 跨平台（未来小程序/RN） | 需自行序列化 | 原生多平台输出 | 原生多平台输出 |
| 与 packages/shared 约定一致性 | 完全一致（无构建直出 TS） | 需新增构建约定 | 需新增构建约定 |

### 推荐：A（TS 对象直出）+ 轻量生成脚本

理由：
1. 当前消费端全部是 Web/TS（antd、Tailwind、Next.js），没有 iOS/Android/RN 输出需求，Style Dictionary 的多平台能力用不上。
2. 团队目前没有设计师用 Figma Tokens Studio 维护 token 的流程，引入 C 是过早优化；DTCG JSON 的「工具间交换格式」价值在本场景为零。
3. 与 `@yimatong/shared` 的无构建直出 TS 约定完全一致，workspace 接入成本为零。
4. Tailwind 侧只需一个几十行的 Node 脚本从 TS 源生成 `@theme` CSS（产物提交入库），即可获得 B 路线 90% 的收益。
5. 保留演进路径：token 源保持「语义化扁平键值」结构，未来若引入 DTCG/Style Dictionary，可写转换器从 TS 源导出 DTCG JSON，无需推翻重来。

---

## 2. antd 6 的 ThemeConfig / Design Token 体系

### 事实发现（来源：antd 官方文档 customize-theme、migration-v6，context7 `/ant-design/ant-design`，2026-07-29）

**三层 token 结构**：
- **Seed Token（种子）**：设计意图的本源，如 `colorPrimary`、`borderRadius`、`fontSize`。改 Seed 会触发 antd 内部算法自动推导一系列关联属性。
- **Map Token（梯度）**：由 Seed 派生的梯度变量，如 `colorPrimaryBg`、`colorPrimaryHover` 等。官方建议通过 `theme.algorithm` 定制以保持梯度关系，也可在 `theme.token` 中单独覆盖。
- **Alias Token（别名）**：Map Token 的别名/加工版本，组件实际消费，如 `colorText`、`colorBgContainer`。

**algorithm 机制**：`theme.algorithm` 是一个函数（或函数数组），输入 Seed Token 输出整套 Map Token。内置 `theme.defaultAlgorithm`、`theme.darkAlgorithm`、`theme.compactAlgorithm`，可组合：`algorithm: [theme.darkAlgorithm, theme.compactAlgorithm]`。也可写自定义算法。

**动态切换**：修改 `ConfigProvider` 的 `theme` 属性即可随时切换主题（如 `algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm`），无需额外配置。

**components 级覆盖**：`theme.components.<Component>` 覆盖单组件 token，优先级高于全局 token；自 v5.8 起组件级也支持自己的 `algorithm` 属性。

**v6 重要变化**（migration-v6 文档）：
- **CSS 变量模式（cssVar）默认开启**，不再支持 IE。这意味着所有 token 会编译为 CSS 变量输出，运行时切换主题性能更好，且 token 可直接在自定义 CSS 中以 `var(--ant-color-primary)` 形式引用。
- ThemeConfig API 本身无破坏性变更；组件级 `xxxStyle`/`xxxClassName` 属性统一迁移到 `styles.*` / `classNames.*`（旧属性格式化后仍可用，v7 移除）。
- `cssVar` 配置项含 `prefix`（默认 `ant`）和 `key`（主题唯一键）。
- `theme.useToken()` 可在运行时读取解析后的完整 token（含派生值）。
- `zeroRuntime` 选项可完全关闭运行时样式生成（需额外引入预生成 CSS 文件）。

### 把自定义 token 体系映射进 antd 的成熟做法

1. **只把「语义 token」映射到 antd 的 Seed/Alias token**，梯度交给 antd 算法推导。例如自有 `color.brand.primary` → antd `colorPrimary`；`color.bg.surface` → `colorBgContainer`。不要逐一手写 antd 的 Map Token，否则暗色主题需要双倍维护且梯度容易失调。
2. **暗色主题用 `darkAlgorithm` + 少量 Seed 覆盖**，而不是全套手写 token。现有 `apps/admin/src/lib/theme.ts` 的 darkTheme 已是此模式（`algorithm: antdTheme.darkAlgorithm` + token 覆盖 + components 覆盖），可直接沉淀进 token 包。
3. **components 级覆盖只放「跨端一致的品牌修正」**（如 Layout/Menu 背景），页面级特殊需求留在应用侧，避免 token 包变成全局样式垃圾场。
4. **派生函数而非静态对象**：token 包导出 `getAntdTheme(mode: 'light' | 'dark'): ThemeConfig`，内部组合 algorithm + token + components，admin/platform 直接消费。这样 antd 版本升级时映射逻辑只有一处。
5. 利用 v6 cssVar 默认开启的特性：自定义组件（非 antd 部分）可直接 `var(--ant-color-primary)` 引用 antd 变量，实现 admin 内 antd 组件与自绘部分的色彩一致。

---

## 3. Tailwind CSS 4 的 CSS-first 配置

### 事实发现（来源：Tailwind CSS 4 官方文档 theme/dark-mode/colors 页，context7 `/tailwindlabs/tailwindcss.com`，2026-07-29）

- **@theme 指令**：v4 不再有 `tailwind.config.js` 的 theme 概念（兼容但非推荐），主题直接在 CSS 中定义：

  ```css
  @import "tailwindcss";

  @theme {
    --color-brand-500: #1677ff;
    --font-display: "Satoshi", sans-serif;
    --breakpoint-3xl: 1920px;
  }
  ```

- **theme 变量即 CSS 变量**：`@theme` 中定义的变量既是工具类生成依据，也会作为真实 CSS 自定义属性输出到 `:root`，可在任意值/内联样式中引用。命名空间共 20 个，与本项目相关的有：`--color-*`（颜色工具类）、`--font-*`、`--text-*`、`--font-weight-*`、`--tracking-*`、`--leading-*`、`--breakpoint-*`、`--spacing-*`、`--radius-*`、`--shadow-*`、`--ease-*`、`--animate-*` 等。例如 `--color-mint-500` 生成 `bg-mint-500`、`text-mint-500` 等。
- **覆盖默认主题**：`--color-*: initial` 可清空整个命名空间后再定义自定义值。
- **@theme inline**：需要引用其他 CSS 变量的 token 用 `@theme inline { --color-canvas: var(--acme-canvas-color); }`——这是双主题的关键机制：把语义 token 指向一个会随 `[data-theme="dark"]` 切换的普通 CSS 变量。
- **dark variant**：v4 默认 dark 变体基于 `prefers-color-scheme`；需要类名/属性驱动时用：
  `@custom-variant dark (&:where(.dark, .dark *));`
- **跨项目共享**（官方明确支持的场景）：「theme 变量定义在 CSS 里，跨项目共享就是把它们放进单独的 CSS 文件各自 import；在 monorepo 中可以放进独立 package 甚至发布到 npm，像普通第三方 CSS 一样引入。」即 apps 侧 `@import "@yimatong/design-tokens/theme.css";`。

### 由 token 源生成 theme CSS 的构建方式

推荐结构（token 源为 TS）：

```css
/* packages/design-tokens 生成的 theme.css */
@import "tailwindcss";  /* 或不含，由消费端 import */

@custom-variant dark (&:where(.dark, .dark *));

:root {
  --ymt-color-bg-surface: #ffffff;      /* 由 light token 生成 */
}
.dark {
  --ymt-color-bg-surface: #0f1a2a;      /* 由 dark token 生成 */
}

@theme inline {
  --color-surface: var(--ymt-color-bg-surface);
  /* ...语义 token → 命名空间映射 */
}

@theme {
  --color-brand-500: #1677ff;           /* 不随主题变化的原语 token */
}
```

要点：
- **原语 token**（品牌色阶、字阶、间距等不随主题变化）→ `@theme` 直出。
- **语义 token**（surface/text/border 等随主题变化）→ 普通 CSS 变量在 `:root` / `.dark` 双声明 + `@theme inline` 引用，与官方 dark-mode 文档推荐模式一致。
- 生成脚本为包内 `scripts/build-css.mjs`（Node 直接跑，无额外依赖），产物 `theme.css` 提交入库；提供 `pnpm --filter @yimatong/design-tokens build` 与 `--check`（CI 校验产物与源一致，防止手改产物）。
- h5 消费：`globals.css` 中 `@import "@yimatong/design-tokens/theme.css";`（Tailwind 4 官方文档确认 monorepo 包内 CSS 可被 import）。

---

## 4. pnpm workspace 内纯 token 包的构建与类型导出策略

### 现状约定（packages/shared）

`@yimatong/shared` 的约定：**无构建直出 TS 源码**，`"main": "./index.ts"`、`"types": "./index.ts"`，`build` 脚本只是 `tsc --noEmit` 类型检查。消费端（Next.js 16）通过 `transpilePackages` 直接编译 workspace TS 源码。

### 方案对比

| 方案 | 优点 | 缺点 |
|---|---|---|
| **无构建直出 TS**（同 shared） | 零构建步骤；改 token 即时生效无需 watch；类型即源码；与 shared 约定一致 | 消费端必须 transpile TS（Next.js 已满足）；无法发布到外部 npm（本项目 private，不需要） |
| tsc 构建（emit .js + .d.ts） | 产物可被任意工具消费 | 需要 watch/构建编排（pnpm -r build、turbo 或手动顺序）；改 token 多一步 |
| tsup（esbuild 打包） | 快、可出 ESM+CJS 双格式 | 引入新构建依赖；对纯常量包是杀鸡用牛刀；双格式对内部包无意义 |

### 推荐：无构建直出 TS + 一个 CSS 产物

- `package.json`：

  ```json
  {
    "name": "@yimatong/design-tokens",
    "version": "0.1.0",
    "private": true,
    "main": "./src/index.ts",
    "types": "./src/index.ts",
    "exports": {
      ".": "./src/index.ts",
      "./theme.css": "./dist/theme.css",
      "./themes/*": "./src/themes/*.ts"
    },
    "scripts": {
      "build": "node scripts/build-css.mjs",
      "check": "node scripts/build-css.mjs --check",
      "typecheck": "tsc --noEmit"
    }
  }
  ```

- 与 shared 唯一差异：多一个 **CSS 产物**（Tailwind 只能消费 CSS）。产物放 `dist/`（git 跟踪提交，`exports["./theme.css"]` 指向），源与产物一致性由 `check` 脚本在 CI 保证。
- antd 派生函数（`getAntdTheme`）依赖 `antd` 包类型：把 `antd` 声明为 `peerDependencies`（版本范围与 apps 对齐 `^6.4.3`），避免 token 包把 antd 打进依赖树导致多实例。
- 确认 apps 的 `next.config` 中 `transpilePackages` 加入 `@yimatong/design-tokens`（与 shared 相同处理）。

---

## 5. 设计 token 的版本与演进策略

### 兼容性规则（语义化版本思想应用到 token）

| 变更类型 | 兼容级别 | 策略 |
|---|---|---|
| 新增 token | 兼容（minor） | 直接加，changelog 记录 |
| 修改 token 值（调色、调间距） | 视觉变更但 API 兼容（minor/patch） | 设计评审后改；双端视觉走查 |
| 重命名 token | 破坏（major） | 旧名保留为 deprecated 别名一个周期（TS 侧 `@deprecated` JSDoc；CSS 侧旧变量映射新变量），消费端迁移完成后删除 |
| 删除 token | 破坏（major） | 先全仓库 grep 确认零引用；CI `check` 兜底 |
| 语义变更（同名不同含义） | 禁止 | 视同重命名+新增 |

工程保障：token 包内加一个快照测试（vitest，对导出键集合与值做 snapshot），任何 token 增删改在 CI 显式可见。

### 双主题 token 的组织方式

推荐**三层结构**，与 antd 三层和 Tailwind 语义/原语分层对齐：

```
src/
  primitives.ts        # 原语：色阶、字阶、间距、圆角（不随主题变化）
  themes/
    light.ts           # 语义 token 的 light 值：bg.surface、text.primary、border.base...
    dark.ts            # 语义 token 的 dark 值（键集合与 light 完全一致）
  index.ts             # 导出 primitives、lightTokens、darkTokens、getAntdTheme(mode)
dist/
  theme.css            # 生成产物（入库）
```

约束：
- **light/dark 键集合必须一致**：`themes/` 导出一个共享的 `SemanticTokens` 类型，light.ts 和 dark.ts 都声明为 `SemanticTokens`（`satisfies`），漏键即编译错误——这是 TS 源路线相对 JSON 路线的核心优势。
- 语义 token 命名与 antd Alias Token 语义对齐（`text.primary` ↔ `colorText`、`bg.container` ↔ `colorBgContainer`），降低映射函数的心智成本。
- 暗色不止「反色」：现有 admin darkTheme 已体现（`colorPrimary` 暗色下用更亮的 `#4c9dff`、阴影/边框独立定义），语义层独立赋值而非程序反转。
- 主题切换入口：admin/platform 走 `ConfigProvider theme={getAntdTheme(mode)}`；h5 走 `<html class="dark">`（配合 `@custom-variant dark`）。两端 mode 状态来源各自管理，token 包不掺和状态。

---

## 总体推荐方案

1. **组织形态**：TS 对象直出（原语 + light/dark 语义三层），不引入 Style Dictionary / Tokens Studio；保留未来转 DTCG 的演进路径。
2. **antd 消费**：包内导出 `getAntdTheme(mode)`，暗色 = `darkAlgorithm` + Seed/Alias 覆盖 + 少量 components 覆盖（沉淀现有 admin theme.ts 逻辑）；利用 antd v6 cssVar 默认开启特性。
3. **Tailwind 消费**：包内 `build-css.mjs` 从 TS 源生成 `theme.css`（原语 → `@theme`；语义 → `:root`/`.dark` 双声明 + `@theme inline` + `@custom-variant dark`），产物入库，CI `--check` 防漂移；h5 `@import "@yimatong/design-tokens/theme.css"`。
4. **包工程**：与 `@yimatong/shared` 一致的无构建直出 TS；`exports` 分出 `.`、`./theme.css` 两个入口；`antd` 列为 peerDependency；apps 侧 `transpilePackages` 登记。
5. **演进策略**：增 = minor，改值 = minor + 视觉走查，重命名/删除 = major + 一个周期 deprecated 别名；light/dark 键集合用共享 TS 类型强制一致；快照测试让变更在 CI 显式可见。

## 信息来源

| 来源 | 内容 | 查询日期 |
|---|---|---|
| antd 官方文档 customize-theme（context7 `/ant-design/ant-design`，含 v6.5.0） | 三层 token、algorithm、components 覆盖、动态切换、cssVar 配置 | 2026-07-29 |
| antd 官方 migration-v6 文档（ant.design） | v6 cssVar 默认开启、ThemeConfig 无破坏性变更 | 2026-07-29 |
| Tailwind CSS 4 官方文档 theme / dark-mode / colors（context7 `/tailwindlabs/tailwindcss.com`） | `@theme`、20 个命名空间、`@theme inline`、`@custom-variant dark`、monorepo 包共享 theme CSS | 2026-07-29 |
| styledictionary.com v4 statement + Style Dictionary GitHub（web 搜索） | Style Dictionary v4 稳定、ESM-only、DTCG 原生支持、DTCG 2025.10 规范 | 2026-07-29 |
| tokens.studio 文档 + tokens-studio/sd-tailwindv4（web 搜索） | Tokens Studio 生态定位、Tailwind 4 输出示例 | 2026-07-29 |
| 本仓库 `frontend/apps/admin/src/lib/theme.ts`、`frontend/packages/shared/package.json` | 现有双主题实现与 shared 包约定 | 2026-07-29 |
