<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# Frontend Workspace Guidelines

## Scope

This file applies to `frontend/`, including `apps/admin`, `apps/h5`, `apps/platform`, `packages/shared`, `packages/design-tokens`, and `e2e`.

## Structure

- `apps/admin/`: Admin console. Default dev server: `http://localhost:3000`.
- `apps/h5/`: Consumer H5 app.
- `apps/platform/`: Platform admin console. Default dev server: `http://localhost:3002`.
- `packages/shared/`: Shared TypeScript types and utilities imported as `@yimatong/shared`.
- `packages/design-tokens/`: Shared visual tokens, Ant Design theme values, generated CSS, and token checks.
- `e2e/`: Playwright browser-flow tests.

Packages are deep modules — see [packages/README.md](./packages/README.md) before adding or importing one.

## Commands

前端 workspace 通用命令见根目录 `AGENTS.md`。以下补充前端专属注意事项：

- The repository is pinned to pnpm 10. Confirm `pnpm --version` resolves to 10.x before installing or checking; pnpm 11 may try to rebuild the existing modules directory.
- `pnpm dev:h5` 未在 `package.json` 中固定端口，默认使用 Next.js 的 `3000`；同时启动 Admin 时请用 `pnpm dev:h5 --port 3001`。
- `pnpm check` 检查 design tokens、shared/design-tokens 类型和 package 边界；它不代替三个应用各自的 lint。
- `pnpm build` 按 shared → design tokens → Admin → H5 → Platform 的顺序构建全部 workspace。
- Admin 组件/单元测试在 `apps/admin` 内执行：

```bash
cd frontend/apps/admin
pnpm exec vitest run
```

## Coding Style

Use TypeScript, React 19, and Next.js 16 conventions already present in the app. Keep components in PascalCase, hooks as `useSomething`, route-specific code under the matching `src/app/...` segment, and shared cross-app code in `packages/shared/src`.

Prefer Ant Design components in Admin and Platform where the surrounding page already uses Ant Design. Keep H5 UI lightweight and mobile-first. Do not add new styling systems unless the existing stack cannot support the change.

Build or check `@yimatong/shared` and `@yimatong/design-tokens` before isolated app builds when a clean workspace cannot resolve shared types or generated CSS. Preserve the explicit `turbopack.root` settings in Admin and H5; if a Next dev server starts but `/login` hangs or manifests fail, inspect the app `next.config.ts` before rewriting page logic.

Platform auth is separate from tenant Admin auth. Platform code uses `platform_access_token` and `/api/v1/platform/auth/login`; tenant Admin uses `access_token`, optional `tenant_slug`, and `/api/v1/auth/login`.

## Testing

Place Admin unit/component tests near the feature or under existing `__tests__` directories. Browser flows belong in `frontend/e2e/` and should assume Admin on `3000`, H5 on `3003` for Playwright config, and backend on `8000`. The Playwright H5 port is intentionally separate from the normal H5 development port. Capture screenshots for visual regressions or UI verification when they help review.
