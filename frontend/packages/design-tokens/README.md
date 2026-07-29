# @yimatong/design-tokens

一码通三端设计 token 的单一事实来源。包对外只提供三个入口：

- `@yimatong/design-tokens`：antd 主题与公共工具。
- `@yimatong/design-tokens/tokens`：不依赖 React/antd 的原语、语义主题和对比度工具。
- `@yimatong/design-tokens/theme.css`：Tailwind CSS 4 与普通 CSS 使用的生成变量。

常用命令：

```bash
pnpm --filter @yimatong/design-tokens build
pnpm --filter @yimatong/design-tokens check
```

`build` 从 TypeScript token 生成 `dist/theme.css` 和 `preview/index.html`；
`check` 会验证生成产物未漂移、light/dark 对比度契约和 TypeScript 类型。
应用只能从包根入口导入，不能直接引用 `lib/`。
