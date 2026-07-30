import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import tailwind from "eslint-plugin-tailwindcss";

// 设计体系门禁（docs/02_tech/design-system/governance.md）：
// H5 行：拦截 Tailwind 任意值（text-[13px]、bg-[#16a34a]）。
// 批3 收尾后 baseline 已清空，规则从 warn 升级为 error（零硬编码契约）。
const designGates = {
  plugins: { tailwindcss: tailwind },
  rules: {
    "tailwindcss/no-arbitrary-value": "error",
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
