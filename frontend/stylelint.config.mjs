/** @type {import('stylelint').Config} */
// 设计体系门禁（docs/02_tech/design-system/governance.md）：
// 拦截 .css（及未来 styled-jsx / CSS Modules）中的硬编码颜色与 px 字号，
// 强制消费 design tokens。存量违规由 frontend/.design-gates-baseline.json 收敛。
//
// 当前 lint 脚本只对 **/*.{css,scss} 调用 stylelint（仓库尚无 styled-jsx 文件）；
// 一旦引入 styled-jsx / CSS Modules，admin/platform 的 lint:style glob 需扩展到
// **/*.{css,tsx} 并依赖下方的 postcss-styled-syntax override。
export default {
  extends: ["stylelint-config-standard"],
  // 预置 styled-jsx / CSS-in-JS 解析（引入 styled-jsx 后即生效）。
  overrides: [
    {
      files: ["**/*.tsx", "**/*.ts", "**/*.jsx", "**/*.js"],
      customSyntax: "postcss-styled-syntax",
    },
  ],
  rules: {
    // 允许标准 CSS 特性，关掉 stylelint-config-standard 里与本仓库无关的噪音规则。
    "at-rule-no-unknown": null,
    "no-descending-specificity": null,
    "comment-empty-line-before": null,
    "rule-empty-line-before": null,
    "selector-class-pattern": null,
    "import-notation": null,
    // ── 设计 token 门禁 ──
    "color-no-hex": [
      true,
      {
        message: "使用 design tokens var(--ymt-color-*) 替代 hex 色值。",
      },
    ],
    "declaration-property-value-disallowed-list": {
      "font-size": ["/^\\d+px$/"],
      color: ["/^#/"],
      "background-color": ["/^#/"],
      "border-color": ["/^#/"],
      "border-top-color": ["/^#/"],
      "border-right-color": ["/^#/"],
      "border-bottom-color": ["/^#/"],
      "border-left-color": ["/^#/"],
      "box-shadow": ["/^#[0-9a-fA-F]/"],
    },
  },
};
