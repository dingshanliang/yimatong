---
name: demo-smoke
description: Verify Yimatong demo readiness end to end. Use when asked to run a demo smoke, validate the local stack and seed, check demo roles, or confirm Admin, H5, backend, API, and browser journeys before a customer demonstration.
---

# Demo Smoke

## Preserve local work

Run `git status --short` first and do not overwrite unrelated changes. If the request belongs to an existing Bead, read and update that Bead; do not create tracker records merely for a one-off smoke check.

## Select one runtime mode

Do not mix port assumptions between modes.

- Host applications + Docker infrastructure:

```bash
pnpm dev:stack
cd backend && uv run uvicorn app.main:app --reload --port 8000
cd frontend && pnpm dev:admin
cd frontend && pnpm dev:h5 --port 3001
```

- Full Docker stack:

```bash
docker compose -f docker-compose.dev.yml up -d
```

`pnpm dev:stack` starts infrastructure only; it does not start backend, Admin, or H5.

Expected demo URLs are Admin `http://localhost:3000`, H5 `http://localhost:3001`, and backend `http://localhost:8000`. Playwright uses H5 `3003` and is a separate test mode.

## Verify readiness

1. Check backend `GET /health`.
2. Rebuild demo data only when requested or when the current seed is unusable:

```bash
cd backend
uv run python -m app.cli demo --clean
```

3. Use the current seed command output for demo accounts and scan URLs. Login through the real endpoint instead of reusing stale cookies.
4. Verify with Browser or Playwright:
   - tenant Admin login;
   - dashboard and one seeded business page;
   - one public H5 scan journey;
   - role-specific access when the demo depends on that role.
5. Confirm suspicious browser/CORS failures with the authenticated API response before diagnosing frontend CORS.

Run broader tests only after the smoke journey itself is stable:

```bash
cd backend && uv run pytest <focused-target>
cd frontend && pnpm test:e2e
```

## Report

- `Status`: ready, blocked, or partial.
- `Runtime mode`: host + infra or full Docker.
- `URLs`: Admin, H5, backend.
- `Roles checked`: role names only; never tokens.
- `Evidence`: browser flows and API checks.
- `Blockers`: failing command, route, or state.
- `Next action`: one highest-value follow-up.
