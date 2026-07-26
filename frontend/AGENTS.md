<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# Frontend Workspace Guidelines

## Scope

This file applies to `frontend/`, including `apps/admin`, `apps/h5`, `apps/platform`, `packages/shared`, and `e2e`.

## Structure

- `apps/admin/`: Admin console. Default dev server: `http://localhost:3000`.
- `apps/h5/`: Consumer H5 app.
- `apps/platform/`: Platform admin console. Default dev server: `http://localhost:3002`.
- `packages/shared/`: Shared TypeScript types and utilities imported as `@yimatong/shared`.
- `e2e/`: Playwright browser-flow tests.

## Commands

前端 workspace 通用命令见根目录 `AGENTS.md` 第 5 节。以下补充前端专属注意事项：

- `pnpm dev:h5` 未在 `package.json` 中固定端口，默认使用 Next.js 的 `3000`；同时启动 Admin 时请用 `pnpm dev:h5 --port 3001`。
- Admin 组件/单元测试在 `apps/admin` 内执行：

```bash
cd frontend/apps/admin
pnpm exec vitest run
```

## Coding Style

Use TypeScript, React 19, and Next.js 16 conventions already present in the app. Keep components in PascalCase, hooks as `useSomething`, route-specific code under the matching `src/app/...` segment, and shared cross-app code in `packages/shared/src`.

Prefer Ant Design components in Admin and Platform where the surrounding page already uses Ant Design. Keep H5 UI lightweight and mobile-first. Do not add new styling systems unless the existing stack cannot support the change.

Build `@yimatong/shared` before app builds or when a clean workspace cannot resolve shared types. Preserve the explicit `turbopack.root` settings in Admin and H5; if a Next dev server starts but `/login` hangs or manifests fail, inspect the app `next.config.ts` before rewriting page logic.

Platform auth is separate from tenant Admin auth. Platform code uses `platform_access_token` and `/api/v1/platform/auth/login`; tenant Admin uses `access_token`, optional `tenant_slug`, and `/api/v1/auth/login`.

## Testing

Place Admin unit/component tests near the feature or under existing `__tests__` directories. Browser flows belong in `frontend/e2e/` and should assume Admin on `3000`, H5 on `3003` for Playwright config, and backend on `8000`. The H5 Playwright port differs from the normal H5 demo port. Capture screenshots for visual regressions or UI verification when they help review.
