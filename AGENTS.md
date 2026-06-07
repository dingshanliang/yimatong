# Repository Guidelines

## Project Structure & Module Organization

Yimatong is a full-stack repository with a FastAPI backend and a pnpm-managed Next.js frontend workspace.

- `backend/`: Python 3.12 API service. Application code lives in `backend/app/`, database migrations in `backend/alembic/`, and pytest suites in `backend/tests/`.
- `frontend/`: pnpm workspace for web clients and shared TypeScript code.
- `frontend/apps/admin/`: Admin console, served on port `3000` by default.
- `frontend/apps/h5/`: Consumer H5 experience.
- `frontend/apps/platform/`: Platform admin console, served on port `3002` by default.
- `frontend/packages/shared/`: Shared frontend types and utilities.
- `frontend/e2e/`: Playwright end-to-end tests.
- `docker-compose.dev.yml`: Local PostgreSQL, Redis, MinIO, backend, worker, admin, H5, and mock services.

## Build, Test, and Development Commands

Backend commands:

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Frontend commands:

```bash
cd frontend
pnpm install
pnpm dev:admin
pnpm dev:h5
pnpm dev:platform
pnpm build
pnpm lint:admin
pnpm lint:h5
pnpm lint:platform
pnpm test:e2e
```

Use `docker-compose -f docker-compose.dev.yml up -d postgres redis minio` for backend dependencies. Use `pnpm dev:stack` from the repository root for demo validation because it starts dependencies, seeds data, and serves Admin, H5, backend, workers, and mocks.
For routine restarts when dependencies and images have not changed, prefer restarting existing services without a rebuild before using `pnpm dev:stack`, because `dev:stack` intentionally runs with `--build --force-recreate`.

## Demo Data & Login Debugging

- `cd backend && uv run python -m app.cli.seed demo` generates rich demo data; add `--clean` when the demo tenant (`slug=demo`) must be rebuilt.
- Demo tenant quick-login accounts are `admin@demo.com / Admin1234`, `ops@demo.com / Ops123456`, `agency@demo.com / Agency1234`, `dist@demo.com / Dist123456`, and `store@demo.com / Store123456`.
- Admin quick-login accounts can share emails with old test tenants; non-platform demo login fixes should preserve `tenant_slug: demo`.
- Platform admin is not a demo tenant account. It uses `/api/v1/platform/auth/login`, the `platform_access_token` cookie, and backend platform admin settings.
- Browser CORS-looking errors on Admin pages can be backend 500s without CORS headers; verify the real API status with an authenticated request before blaming local proxy or HMR.

## Coding Style & Naming Conventions

Backend code uses Ruff with Python 3.12, import sorting, and a 120-character line length. Keep first-party imports under `app`. Tests follow pytest naming: `test_*.py`, `Test*`, and `test_*`.

Frontend code uses TypeScript, React 19, Next.js 16, ESLint, and workspace imports such as `@yimatong/shared`. Keep React components in PascalCase, hooks as `useSomething`, and colocate route-specific UI under the relevant `src/app/...` segment. Read `frontend/AGENTS.md` before changing Next.js code.

## Testing Guidelines

Add or update focused tests with behavior changes. Backend unit and API tests belong in `backend/tests/`; frontend component tests use Vitest under app source directories; browser flows belong in `frontend/e2e/`. Run the smallest relevant test first, then a broader check before handoff.

## Commit & Pull Request Guidelines

Recent history uses concise Conventional Commit style, for example `feat(docker): ...`, `fix(frontend): ...`, `style: ...`, and `refactor: ...`. Keep commits scoped and avoid mixing unrelated backend, frontend, and infrastructure changes. PRs should include a short summary, linked issue or bead, verification commands, screenshots for UI changes, and migration or configuration notes when applicable.

## Agent-Specific Instructions

Use beads (`bd`) for task tracking. Claim work before editing when a bead exists, create one for newly defined work, and close or update it before handoff. Do not overwrite user changes or run destructive git commands unless explicitly requested.

When the user asks for subagent or parallel review, route Alembic/database changes to `.codex/agents/migration-reviewer.toml` and auth, tenant isolation, public-route, encryption, or PII changes to `.codex/agents/security-reviewer.toml`. Summarize the reviewer findings before handoff.
