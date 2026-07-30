import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// 设计体系门禁（docs/02_tech/design-system/governance.md）：
// 拦截 JSX inline style 中的硬编码颜色/字号字面量与 Tailwind 任意值，
// 强制消费 design tokens。
// 这里设为 warn（不阻断 pnpm lint）；error 级增量阻断由 frontend/scripts/design-gates.mjs
// 配合 frontend/.design-gates-baseline.json 完成（CI design-gates job）。
const designGates = {
  rules: {
    "no-restricted-syntax": [
      "warn",
      {
        selector:
          "JSXAttribute[name.name='style'] ObjectExpression > Property[key.name='color'] Literal[value=/#[0-9a-fA-F]{3,8}|rgba?\\(/i]",
        message:
          "Inline style 中禁止硬编码颜色，请使用 design tokens (var(--ymt-color-*))。",
      },
      {
        selector:
          "JSXAttribute[name.name='style'] ObjectExpression > Property[key.name='backgroundColor'] Literal[value=/#[0-9a-fA-F]{3,8}|rgba?\\(/i]",
        message:
          "Inline style 中禁止硬编码颜色，请使用 design tokens (var(--ymt-color-*))。",
      },
      {
        selector:
          "JSXAttribute[name.name='style'] ObjectExpression > Property[key.name='fontSize'] Literal[value=/\\d+px/]",
        message:
          "Inline style 中禁止硬编码 px 字号，请使用 var(--ymt-font-size-*)。",
      },
    ],
  },
};

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      "react-hooks/refs": "off",
      "react-hooks/set-state-in-effect": "off",
    },
  },
  designGates,
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "public/react-grab.js",
  ]),
]);

export default eslintConfig;
