---
name: project-conventions
description: Apply current Yimatong repository conventions while implementing or reviewing backend, frontend, tests, migrations, tenant isolation, and package boundaries. Use for code changes in this repository; verify local neighboring patterns instead of imposing legacy templates.
---

# Yimatong Project Conventions

## Start from current sources

Read the root `AGENTS.md` and the closest nested `AGENTS.md`. Use CodeGraph before locating or changing indexed code. Treat manifests, current models, middleware, tests, and neighboring implementations as more authoritative than examples in this skill.

## Backend

- Keep FastAPI routes focused on request validation, authorization, and orchestration. Put reusable business behavior in services.
- Use async SQLAlchemy 2.0 and the transaction behavior supplied by repository database dependencies. Do not add service-level `commit()` unless the existing workflow explicitly owns its transaction.
- Tenant-scoped reads and writes preserve both application-layer tenant constraints and PostgreSQL RLS.
- Do not assume one universal model base or service signature. Follow the current neighboring model/service while preserving UUID, tenant, timestamp, and serialization behavior.
- Reuse `PaginatedResponse`, current dependencies, RBAC helpers, and error conventions when applicable.
- Public, scan-token, platform, worker, and tenant routes have different database/authentication boundaries; inspect `TenantScopeMiddleware` and database dependencies before changing them.

## Frontend

- Use React 19 and Next.js 16 patterns documented in the installed Next.js package.
- Admin and Platform reuse Ant Design and shared design tokens; H5 remains lightweight and mobile-first.
- Import workspace packages only through their declared public entrypoints. Do not deep-import another package's private `lib/` or `src/` implementation.
- Preserve Admin, H5, and Platform authentication separation.
- Verify business behavior, permissions, payloads, and accessibility; do not freeze ordinary explanatory copy in tests.

## Verification

Choose checks from the changed surface:

```bash
cd backend && uv run ruff check <files>
cd backend && uv run pytest <focused-tests>
cd frontend && pnpm check
cd frontend && pnpm lint:admin
cd frontend && pnpm lint:h5
cd frontend && pnpm lint:platform
```

Use real browser verification for user journeys and the repository acceptance suite when the acceptance criteria require Browser + API + read-only database evidence.

## Hard stops

- Do not bypass tenant filtering or RLS for convenience.
- Do not add public-route exceptions without security tests.
- Do not hardcode credentials, production identifiers, or PII.
- Do not auto-commit, push, merge, deploy, close Beads, or skip failed acceptance criteria unless the user explicitly authorized that workflow.
