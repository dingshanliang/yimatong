import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// 设计体系门禁（docs/02_tech/design-system/governance.md）：
// 拦截 JSX inline style 中的硬编码颜色/字号字面量与 Tailwind 任意值，
// 强制消费 design tokens。
// 批3 收尾后 baseline 已清空，规则从 warn 升级为 error（零硬编码契约）。
const designGates = {
  rules: {
    "no-restricted-syntax": [
      "error",
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
      {
        selector:
          "JSXAttribute[name.name='className'] Literal[value=/\\b(text|bg|border|from|via|to|ring|divide|outline|fill|stroke)-(red|green|blue|gray|grey|slate|amber|orange|yellow|purple|indigo|violet|pink|emerald|teal|cyan|sky|lime|rose)-\\d/i]",
        message:
          "禁止使用 Tailwind 标准调色板工具类，请改用 design token 语义类（text-foreground / bg-muted / text-success 等）。",
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
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "public/react-grab.js",
  ]),
]);

export default eslintConfig;
