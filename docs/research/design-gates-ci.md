# 前端 CI 设计门禁方案调研报告

> ticket: yimatong-pifo.2「前端 CI 设计门禁方案调研」
> 查询日期: 2026-07-30
> 分支: `research/design-gates`

## 背景与目标

`frontend/packages/design-tokens/` 已成为一码通三端（admin / platform / h5）的 token 单一事实来源：

- TS 对象定义 primitive、light/dark semantic tokens、可访问性配对。
- `scripts/build-css.mjs` 生成 `dist/theme.css`（Tailwind v4 `@theme` + `:root` CSS 变量）。
- 派生 Ant Design `ThemeConfig` 与 H5 Tailwind 主题。

本报告调研「防漂移 CI 自动化门禁」的可行方案，覆盖：

1. 禁硬编码色值/字号（ESLint + stylelint）。
2. design-tokens 包快照测试。
3. 构建期 WCAG AA 对比度校验。
4. monorepo 落地位置与豁免/收敛机制。

---

## 1. 禁硬编码色值/字号

### 1.1 方案对比

| 方案                                                                           | 覆盖范围                                                    | 能力                                                                                                          | 成本                                                  | 适用场景                                  |
| ------------------------------------------------------------------------------ | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ----------------------------------------- |
| **A. ESLint `no-restricted-syntax` + AST selector**                            | JSX inline style、className 中的 Tailwind 任意值、JS 字面量 | 用内置规则即可拦截常见硬编码；无需新增依赖；可随设计系统演进扩展 selector                                     | 中：selector 需要维护，className 字符串匹配是正则近似 | 立即落地，作为第一层兜底                  |
| **B. `eslint-plugin-tailwindcss`**                                             | Tailwind `className` 中的任意值                             | 提供 `no-arbitrary-value`、`no-unnecessary-arbitrary-value` 规则；对 `text-[13px]`、`bg-[#16a34a]` 等原生识别 | 低：安装插件并开启规则即可                            | H5（Tailwind 4）首选                      |
| **C. 自定义 ESLint 插件（`@yimatong/eslint-plugin-design-tokens`）**           | JSX inline style、className、任意 JS 表达式                 | 可精确比对 className 是否在 token 白名单；可感知语义 token 路径；自动修复潜力最大                             | 高：需要独立包开发、单测、文档                        | 中长期规范化目标                          |
| **D. stylelint `color-no-hex` + `declaration-property-value-disallowed-list`** | `.css`、`.scss`、styled-components / CSS Modules 等         | 对 CSS 声明精确拦截 hex、px 字号、非 token 字体族等；message 函数可提示改用 `var(--ymt-*)`                    | 低：stylelint 生态成熟                                | admin / platform 的 CSS / styled-jsx 文件 |
| **E. stylelint 自定义插件强制 CSS 变量**                                       | `.css` 等                                                   | 要求 `color`、`background-color`、`font-size` 等必须引用 `--ymt-*` 变量                                       | 中：需按 token 清单生成规则                           | 与 design-tokens 包联动                   |

### 1.2 关键规则/配置示例

**ESLint `no-restricted-syntax` 拦截 JSX inline style 硬编码颜色与字号**

```js
// eslint.config.mjs
{
  rules: {
    "no-restricted-syntax": [
      "error",
      {
        selector:
          "JSXAttribute[name.name='style'] JSXExpressionContainer Literal[value=/#[0-9a-f]{3,8}|rgb\\(/i]",
        message: "Inline style 中禁止使用硬编码颜色，请使用 design tokens 或 CSS 变量。",
      },
      {
        selector:
          "JSXAttribute[name.name='style'] JSXExpressionContainer Literal[value=/\\b\\d+px\\b/]",
        message: "Inline style 中禁止使用硬编码 px 字号，请使用 var(--ymt-font-size-*)。",
      },
      {
        selector:
          "JSXAttribute[name.name='className'] Literal[value=/\\[[#\\d]+/]",
        message: "Tailwind 任意值可能绕过 design tokens，请使用标准 utility 或 token 变量。",
      },
    ],
  },
}
```

> 来源：ESLint 官方文档 `no-restricted-syntax`（context7 `/eslint/eslint`，2026-07-30）。

**`eslint-plugin-tailwindcss` 拦截任意值**

```js
import tailwindcss from "eslint-plugin-tailwindcss";

{
  plugins: { tailwindcss },
  rules: {
    "tailwindcss/no-arbitrary-value": "error",
    // 可选：关闭误报较严重的规则，待配置完善后再开启
    // "tailwindcss/no-unnecessary-arbitrary-value": "warn",
  },
}
```

> 来源：`eslint-plugin-tailwindcss` npm 页面与 issue 讨论（网络搜索，2026-07-30）。
> 注意：Tailwind v4 的 CSS-first 配置可能使该插件部分规则需要配合 `tailwindcss.config.js` 路径或实验性支持；实际接入前应在 H5 代码库试点。

**stylelint 拦截 CSS 中硬编码颜色与字号**

```js
// stylelint.config.mjs
export default {
  rules: {
    "color-no-hex": [
      true,
      { message: "使用 CSS 变量 var(--ymt-color-*) 替代 hex 色值。" },
    ],
    "declaration-property-value-disallowed-list": {
      "font-size": ["/^\\d+px$/"],
      "font-weight": ["normal", "bold"], // 强制使用 var(--ymt-font-weight-*)
      color: ["/^#/"],
      "background-color": ["/^#/"],
    },
  },
};
```

> 来源：stylelint 官方文档 `color-no-hex`、`declaration-property-value-disallowed-list`（context7 `/stylelint/stylelint`，2026-07-30）。

### 1.3 推荐结论

- **H5（Tailwind 4）**：优先启用 `eslint-plugin-tailwindcss/no-arbitrary-value`，拦截 `text-[13px]`、`bg-[#16a34a]` 等最常见的绕过方式。
- **admin / platform（Next.js + antd）**：以 ESLint `no-restricted-syntax` 拦截 JSX inline style 中的 `color`、`fontSize`、`backgroundColor` 硬编码；配合 stylelint 拦截 `.css` / styled-jsx / CSS Modules 中的硬编码。
- **中长期**：将常用 selector 沉淀为自定义 ESLint 插件 `@yimatong/eslint-plugin-design-tokens`，统一维护允许的颜色、字号、间距、圆角白名单，并提供自动修复建议。

---

## 2. design-tokens 包快照测试

### 2.1 方案对比

| 方案                                            | 能力                                                                        | 优点                                                                | 缺点                                        |
| ----------------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------- |
| **A. Vitest `toMatchSnapshot()` 导出对象**      | 对 `primitives`、`lightTokens`、`darkTokens`、`accessibilityPairs` 整体快照 | 与 admin 已有的 vitest 一致；diff 清晰；可直接发现 token 意外变更   | 大对象快照 diff 噪音较多                    |
| **B. Vitest `toMatchFileSnapshot()` 生成产物**  | 对 `dist/theme.css` 做文件快照                                              | 可直接在 PR 中 review CSS 变量变化；与 `build-css.mjs --check` 互补 | 需要确保构建产物稳定（无时间戳、无哈希）    |
| **C. 节点原生 `assert.snapshot` / `node:test`** | 当前 design-tokens 已用 `node --test`                                       | 零额外依赖                                                          | 生态较弱，自动更新、PR diff 体验不如 Vitest |
| **D. 语义化版本 + 快照联动**                    | 当快照变更时，要求同步提升 minor/patch 版本并写 changelog                   | 把 token 变更显式化，便于下游消费                                   | 需要流程约束，不能仅靠工具                  |

### 2.2 关键用法示例

**Vitest 文件快照校验 theme.css**

```ts
// frontend/packages/design-tokens/tests/theme-snapshot.test.ts
import { describe, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const themeCss = readFileSync(
  resolve(fileURLToPath(import.meta.url), "../../dist/theme.css"),
  "utf-8"
);

describe("design-tokens generated artifacts", () => {
  it("theme.css matches snapshot", async () => {
    await expect(themeCss).toMatchFileSnapshot("__snapshots__/theme.css");
  });
});
```

> 来源：Vitest 官方文档 `toMatchFileSnapshot`、`vitest -u` 更新流程（context7 `/vitest-dev/vitest`，2026-07-30）。

**语义化版本约束**

```ts
// tests/version-policy.test.ts
import { describe, it, expect } from "vitest";
import pkg from "../package.json";

describe("token version policy", () => {
  it("has a non-zero minor/major when theme snapshot changed", () => {
    // 该测试本身不自动判断，需要配合 PR 模板 / CODEOWNERS 审查：
    // 若 snapshot diff 非空，则必须更新 changelog 并提升版本。
    expect(pkg.version).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
```

### 2.3 推荐结论

- **首选 B**：用 `toMatchFileSnapshot` 对 `dist/theme.css` 做产物快照，配合已有的 `build-css.mjs --check` 在 CI 中先 build 再校验，确保 TS token 与 CSS 输出一致。
- **辅助 A**：对 `accessibilityPairs`、`primitives` 等核心对象做结构化断言（推荐明确断言而非全快照），避免大对象快照噪音。
- **联动 D**：在 PR 模板中增加「token 变更检查清单」：如修改了 `primitives.ts` 或 `themes.ts`，必须同步运行 `pnpm build:tokens`、更新快照、提升版本号、记录 changelog。

---

## 3. 构建期 WCAG AA 对比度校验

### 3.1 方案对比

| 方案                                                                | 接入点                                                     | 能力                                                                               | 成本                                              | 推荐度   |
| ------------------------------------------------------------------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------- | -------- |
| **A. 复用 design-tokens 内置 `contrast.ts` + `accessibilityPairs`** | `packages/design-tokens/tests/contrast.test.mjs`（已存在） | 基于 token 值直接计算对比度；light/dark 全覆盖；无外部依赖；已在 `pnpm check` 路径 | 低                                                | **首选** |
| **B. pa11y CLI 扫描构建产物或本地 URL**                             | CI 中启动 dev server / 静态导出后执行 `pa11y`              | 可校验真实渲染后的对比度，包括租户品牌注入后的动态组合                             | 中：需要启动服务、处理 false positive、配置规则集 | 补充     |
| **C. `@axe-core/cli` 扫描页面**                                     | 类似 pa11y，对运行中的页面执行 axe-core                    | 与 axe-core 生态一致，规则成熟                                                     | 中：同样需要运行服务                              | 补充     |
| **D. 自定义 Playwright + axe-core 在 E2E 中校验**                   | `frontend/e2e` 现有 Playwright 测试                        | 可覆盖关键用户流程（登录、扫码页）的实际渲染对比度                                 | 高：需编写场景化测试                              | 可选补充 |

### 3.2 当前代码状态

`packages/design-tokens` 已提供：

- `contrastRatio(foreground, background)` —— WCAG 2.2 对比度算法。
- `accessibilityPairs` —— 14 组语义前景/背景配对与最低要求（3 / 4.5 / 7）。
- `build-css.mjs` 在生成预览页时输出 `PASS/FAIL` 表格。
- `tests/contrast.test.mjs` 已校验所有配对。

> 来源：仓库源码 `frontend/packages/design-tokens/lib/contrast.ts`、`lib/themes.ts`、`scripts/build-css.mjs`（2026-07-30）。

### 3.3 推荐结论

- **必选 A**：将已有的 `contrast.test.mjs` 纳入 CI `design-gates` job，作为构建期门禁。它直接读取 token 源数据，确定性高、速度快、零 false positive。
- **可选 B/C**：在 H5 消费者扫码页等关键页面，用 pa11y 或 `@axe-core/cli` 对构建产物做补充扫描，捕获 token 组合未覆盖的 UI 状态（如租户品牌色注入后的主按钮文字对比度）。
- **E2E 层**：当后续增加可访问性专项测试时，再在 Playwright E2E 中用 `@axe-core/playwright` 做流程级校验，不纳入本次构建期门禁。

---

## 4. monorepo 落地与豁免收敛机制

### 4.1 当前仓库结构

```
frontend/
  package.json           # workspace root，已有 build / lint:* / check 脚本
  apps/
    admin/               # Next.js 16 + antd 6 + Tailwind 4
    h5/                  # Next.js 16 + Tailwind 4
    platform/            # Next.js 16 + antd 6 + Tailwind 4
  packages/
    design-tokens/       # token 单一事实来源
    shared/              # 类型定义
  e2e/                   # Playwright E2E
```

当前各 app `lint` 脚本均只执行 `eslint`；`design-tokens` 有 `check:generated`、`test`、`typecheck`。CI workflow `.github/workflows/ci.yml` 在 `frontend` job 中执行 typecheck 与 build，尚无 lint 步骤。

### 4.2 接入位置

| 层级                                           | 新增脚本                                                                                               | 说明                          |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------- |
| `frontend/packages/design-tokens/package.json` | `"lint:tokens": "eslint ."`（自定义规则）+ 保留现有 `check`                                            | token 包自身先守规矩          |
| `frontend/package.json`                        | `"lint:tokens": "pnpm --filter @yimatong/design-tokens lint:tokens"`                                   | root 统一入口                 |
| `frontend/apps/admin/package.json`             | `"lint": "eslint . && stylelint '**/*.{css,scss}'"`                                                    | 合并 ESLint + stylelint       |
| `frontend/apps/platform/package.json`          | 同 admin                                                                                               | 平台后台同构                  |
| `frontend/apps/h5/package.json`                | `"lint": "eslint . && eslint . --config tailwind-gates.config.mjs"` 或扩展 `eslint-plugin-tailwindcss` | 重点拦截 Tailwind 任意值      |
| `.github/workflows/ci.yml`                     | 新增 `design-gates` job                                                                                | 在 build 之前运行，失败即阻断 |

### 4.3 建议 CI workflow 片段

```yaml
# .github/workflows/ci.yml 新增 job
design-gates:
  runs-on: ubuntu-latest
  defaults:
    run:
      working-directory: frontend
  steps:
    - uses: actions/checkout@v4
    - uses: pnpm/action-setup@v4
      with: { version: 10 }
    - uses: actions/setup-node@v4
      with:
        node-version: 22
        cache: pnpm
        cache-dependency-path: frontend/pnpm-lock.yaml
    - run: pnpm install --frozen-lockfile
    - name: Build design-tokens
      run: pnpm build:tokens
    - name: Token snapshot & contrast
      run: pnpm --filter @yimatong/design-tokens check
    - name: Lint hardcoded tokens
      run: pnpm lint:tokens && pnpm lint:admin && pnpm lint:platform && pnpm lint:h5
```

### 4.4 豁免/白名单与渐进式收敛

| 机制                                     | 用途                            | 做法                                                                                                                             |
| ---------------------------------------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **Baseline 文件**                        | 记录存量违规，允许已知问题通过  | 生成 `frontend/.design-gates-baseline.json`，记录文件路径、规则、行号；CI 脚本对比增量，只允许 baseline 内违规存在，新增违规阻断 |
| **ESLint disable 注释 + 理由**           | 单个文件/行临时豁免             | `// eslint-disable-next-line yimatong/no-hardcoded-token -- 租户品牌色由运行时注入，非设计 token`                                |
| **stylelint `severity: "warning"` 阶段** | 初期不阻断，先暴露问题          | 对新增规则先设 warning，运行 1-2 个 sprint 后切 error                                                                            |
| **路径/文件忽略**                        | 第三方代码、legacy 页面、预览页 | 在 `eslint.config.mjs` 与 `stylelint.config.mjs` 中配置 `ignores`，如 `preview/**`、`*.stories.tsx`                              |
| **白名单正则**                           | 允许少量通用值                  | 例如允许 `transparent`、`currentColor`、`inherit`、`initial`；允许 antd 内部使用的少量硬编码                                     |
| **版本化收敛**                           | 逐步收紧                        | 每两周缩减 baseline 10%，直到 baseline 为空；用 beads / issue 追踪                                                               |

### 4.5 落地步骤建议

1. **第 1 周：基础设施**
   - 在 `design-tokens` 包引入 vitest（保持 node:test 兼容或迁移），增加 `theme.css` 文件快照测试。
   - 统一 `design-tokens` 的 `check` 脚本已包含对比度校验与生成产物校验。
2. **第 2 周：lint 规则试点**
   - 在 `apps/h5` 安装 `eslint-plugin-tailwindcss`，开启 `no-arbitrary-value`（warning 模式），跑全量统计生成 baseline。
   - 在 `apps/admin` 试点 `no-restricted-syntax` selector 拦截 inline style 硬编码颜色/字号（warning 模式）。
3. **第 3 周：stylelint 接入**
   - 在 admin / platform 增加 stylelint + `color-no-hex` + `declaration-property-value-disallowed-list`，同样 warning 模式。
4. **第 4 周：CI 集成**
   - 在 `.github/workflows/ci.yml` 新增 `design-gates` job，将 `build:tokens`、`check`、lint 串起来。
   - 将 warning 规则按文件维度 baseline 化，确保 CI 通过。
5. **第 5-8 周：收敛**
   - 每周 review baseline，按模块修掉存量违规；逐步把 warning 提升为 error。

---

## 5. 总体推荐汇总

| 方向                       | 一句话推荐                                                                                                                                                                                                                                                               |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **禁硬编码色值/字号**      | H5 用 `eslint-plugin-tailwindcss/no-arbitrary-value`；admin / platform 用 ESLint `no-restricted-syntax` 拦截 inline style，并用 stylelint `color-no-hex` + `declaration-property-value-disallowed-list` 拦截 CSS；中长期沉淀为 `@yimatong/eslint-plugin-design-tokens`。 |
| **design-tokens 快照测试** | 用 Vitest `toMatchFileSnapshot` 对 `dist/theme.css` 做产物快照，结合 PR 清单要求 token 变更时同步更新快照、版本号与 changelog。                                                                                                                                          |
| **构建期对比度校验**       | 必选复用现有 `contrast.ts` + `accessibilityPairs` 的单元测试；pa11y / axe-core 作为 H5 关键页面的补充扫描。                                                                                                                                                              |
| **monorepo 落地**          | 在 `frontend/packages/design-tokens` 增加 lint 入口，在 root `package.json` 暴露 `lint:tokens`，在 `.github/workflows/ci.yml` 新增 `design-gates` job；通过 baseline 文件 + 渐进 warning→error + 有理由的 disable 注释收敛存量违规。                                     |

---

## 6. 信息来源

1. ESLint 官方文档：`no-restricted-syntax`、custom rule selectors（context7 `/eslint/eslint`，2026-07-30）。
2. stylelint 官方文档：`color-no-hex`、`declaration-property-value-disallowed-list`（context7 `/stylelint/stylelint`，2026-07-30）。
3. Vitest 官方文档：`toMatchSnapshot`、`toMatchFileSnapshot`、`-u` 更新流程（context7 `/vitest-dev/vitest`，2026-07-30）。
4. `eslint-plugin-tailwindcss` npm 页面与 GitHub issue 讨论（网络搜索，2026-07-30）。
5. pa11y / axe-core CLI 可访问性测试对比文章（网络搜索，2026-07-30）。
6. monorepo design tokens linting 与 CI 最佳实践（网络搜索，2026-07-30）。
7. 仓库源码：`frontend/packages/design-tokens/`、`frontend/package.json`、`.github/workflows/ci.yml`（2026-07-30）。
