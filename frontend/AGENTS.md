<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# Frontend Workspace Guidelines

## Scope

This file applies to `frontend/`, including `apps/admin`, `apps/h5`, `packages/shared`, and `e2e`.

## Structure

- `apps/admin/`: Admin console. Default dev server: `http://localhost:3000`.
- `apps/h5/`: Consumer H5 app.
- `packages/shared/`: Shared TypeScript types and utilities imported as `@yimatong/shared`.
- `e2e/`: Playwright browser-flow tests.

## Commands

Run commands from `frontend/` unless a package-specific command requires another directory.

```bash
pnpm install
pnpm dev:admin
pnpm dev:h5
pnpm build
pnpm lint:admin
pnpm lint:h5
pnpm --filter @yimatong/shared typecheck
pnpm test:e2e
```

Use targeted commands first, then broader checks before handoff. For Admin component tests, run Vitest from the Admin package, for example:

```bash
cd frontend/apps/admin
pnpm exec vitest run
```

## Coding Style

Use TypeScript, React 19, and Next.js 16 conventions already present in the app. Keep components in PascalCase, hooks as `useSomething`, route-specific code under the matching `src/app/...` segment, and shared cross-app code in `packages/shared/src`.

Prefer Ant Design components in Admin where the surrounding page already uses Ant Design. Keep H5 UI lightweight and mobile-first. Do not add new styling systems unless the existing stack cannot support the change.

## Testing

Place Admin unit/component tests near the feature or under existing `__tests__` directories. Browser flows belong in `frontend/e2e/` and should assume Admin on `3000`, H5 on `3003` for Playwright config, and backend on `8000`. Capture screenshots for visual regressions or UI verification when they help review.
