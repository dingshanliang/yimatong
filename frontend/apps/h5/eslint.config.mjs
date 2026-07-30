import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import tailwind from "eslint-plugin-tailwindcss";

// 设计体系门禁（docs/02_tech/design-system/governance.md）：
// H5 行：拦截 Tailwind 任意值（text-[13px]、bg-[#16a34a]）与标准调色板工具类
// （text-gray-900、bg-red-50 等），强制消费 design tokens。
// 批3 收尾后 baseline 已清空，规则从 warn 升级为 error（零硬编码契约）。
const designGates = {
  plugins: { tailwindcss: tailwind },
  rules: {
    "tailwindcss/no-arbitrary-value": "error",
    "no-restricted-syntax": [
      "error",
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
      "@next/next/no-img-element": "off",
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
