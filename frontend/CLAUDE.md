# 一码通前端 Monorepo

通用开发命令、技术栈和架构说明见根目录 `CLAUDE.md`。本文件只补充前端特有的信息。

## 结构

```
apps/admin/     管理后台 — Next.js 16 + Ant Design 6（端口 3000）
apps/h5/        消费者扫码页 — Next.js 16 + Tailwind 4 + Headless UI（端口 3001）
apps/platform/  平台管理后台 — Next.js 16 + Ant Design 6（端口 3002，紫色主题）
packages/shared/ 共享 TypeScript 类型定义（@yimatong/shared）
e2e/            Playwright E2E 测试
```

## 关键文件

| 文件 | 用途 |
|------|------|
| `apps/admin/src/lib/api.ts` | Axios 实例（Bearer token 拦截器） |
| `apps/admin/src/lib/auth.ts` | Zustand auth store（localStorage + cookie 双写） |
| `apps/admin/src/lib/theme.ts` | Ant Design 主题配置 |
| `apps/admin/src/middleware.ts` | Admin 路由守卫（cookie 检查） |
| `apps/h5/src/middleware.ts` | H5 路由守卫 |
| `apps/platform/src/lib/platform-auth.ts` | 平台管理员 Zustand auth store（独立 cookie 键） |
| `apps/platform/src/lib/api.ts` | 平台 Axios 实例（读 platform_access_token） |
| `apps/platform/src/middleware.ts` | Platform 路由守卫（cookie 检查） |

## Admin 路由模块

`(dashboard)/` 下业务模块：accounts, agency, ai-assistant, batches, benefits, brands, campaign-analytics, campaigns, channel-portal, channels, codes, connectors, crm-sync, exports, gmv, i18n, imports, integrations, launch-checklist, members, pages, products, regional, risk, risk-dashboard, settings, skus, stats, store-portal。另有 `_components` 共享组件目录。

## 前端开发注意事项

- **构建顺序**：`pnpm build:shared` 必须在 `pnpm dev:admin` / `pnpm dev:platform` 或构建命令之前执行，因为所有应用依赖 `@yimatong/shared`
- **Platform 认证**：使用独立的 cookie 键 `platform_access_token`，与 admin 的 `access_token` 隔离。登录端点为 `/platform/auth/login`（非 `/auth/login`）
- **Ant Design 6**：API 与 v5 有 breaking changes，不确定的组件用法先查 context7-mcp
- **Tailwind CSS 4**：配置方式与 v3 不同（CSS-first 配置），使用 `@theme` 指令而非 `tailwind.config.js`
- **Admin 单元测试**：`cd frontend/apps/admin && pnpm exec vitest run`
- **E2E 测试端口**：H5 的 Playwright E2E 测试使用端口 3003（非 dev 端口 3001），配置见 `playwright.config.ts`
- **共享包类型检查**：`pnpm --filter @yimatong/shared typecheck`

## AGENTS.md

@AGENTS.md
