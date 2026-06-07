---
name: demo-smoke
description: "Verify Yimatong demo readiness end to end. Use when asked to run a demo smoke, validate pnpm dev:stack, check seeded demo accounts, or confirm Admin/H5/backend are ready for a customer demo."
---

# Demo Smoke

## Purpose

Run a focused Yimatong demo-readiness smoke check across backend, Admin, H5, seed data, and the most important browser flows. Prefer proving the actual demo path over broad test-suite coverage.

## Workflow

1. Confirm the working tree first.
   - Run `git status --short` and preserve unrelated user changes.
   - If a bead exists for the demo task, claim or update it before editing.

2. Start or verify the demo stack.
   - Prefer `pnpm dev:stack` from the repository root when the stack is not already running.
   - Expected local targets are Admin `http://localhost:3000`, H5 `http://localhost:3001`, backend `http://localhost:8000`.
   - Use `docker-compose -f docker-compose.dev.yml up -d postgres redis minio` only when backend dependencies are needed without the full demo stack.

3. Verify backend health and demo identity.
   - Check `GET http://localhost:8000/health`.
   - Login through real demo accounts instead of relying on stale cookies.
   - Demo tenant accounts:
     - `admin@demo.com / Admin1234`
     - `ops@demo.com / Ops123456`
     - `agency@demo.com / Agency1234`
     - `dist@demo.com / Dist123456`
     - `store@demo.com / Store123456`
   - Platform admin is separate from tenant demo login and uses `/api/v1/platform/auth/login`.

4. Verify the browser path.
   - Use Browser or Playwright for Admin login, dashboard load, and at least one business page with seeded data.
   - Verify H5 loads from the public/demo route used in seed output or existing fixtures.
   - Treat browser CORS-looking failures as suspect backend errors until an authenticated API request confirms the real status.

5. Run broader checks only when the smoke path is stable.
   - Backend focused tests: `cd backend && uv run pytest <target>`.
   - Frontend broad E2E: `cd frontend && pnpm test:e2e`.
   - Note that Playwright config may run H5 on `3003` for E2E even though the demo stack serves H5 on `3001`.

## Reporting Format

Return a short readiness report:

- `Status`: ready, blocked, or partial.
- `Demo URLs`: Admin, H5, backend.
- `Accounts checked`: list roles, not tokens.
- `Checks run`: commands and browser/API flows.
- `Blockers`: concrete failing route, request, or command.
- `Next action`: the single highest-value follow-up.

## Common Failure Patterns

- Admin login appears like CORS but backend returns a 500 without CORS headers.
- Demo quick-login picks an old tenant when `tenant_slug: demo` is missing.
- Next.js dev server starts but `/login` hangs because workspace/root configuration drifted.
- H5 port confusion: demo stack uses `3001`; E2E uses `3003`.
