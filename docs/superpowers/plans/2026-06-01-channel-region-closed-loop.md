# Channel Region Closed Loop Implementation Plan

> **Status:** ⏸ Partial — basic channel/region models and API exist, advanced tracking/analytics not implemented
> **Completed date:** —
> **Evidence:** `backend/app/models/channel.py`, `backend/app/api/v1/channels.py` — basic CRUD only

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make channel management close the business loop with distributor as the required responsibility owner, region as the primary operating scope, and store as an optional finer-grained capability.

**Architecture:** Keep the existing Admin app and FastAPI channel APIs. Backend owns code generation, validation, scope filtering, and summary aggregation; frontend owns guided creation flows, context-prefilled forms, and next-step actions. Store-level data remains supported but must not block distributor, region, code allocation, statistics, or diversion clue handling.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, pytest, Ruff, Next.js 16, React 19, Ant Design, Vitest, Playwright.

---

## Scope Decision

- Distributor is required for the channel loop.
- Region is the default operating granularity for allocation, statistics, account scope, and diversion clue ownership.
- Store is optional. It is only shown as a next step or extra detail when a user explicitly enables store management or existing data has store associations.

## Files

- Modify: `backend/app/models/channel.py` if `Region.status` or normalized province/city fields are missing.
- Modify: `backend/app/schemas/channel.py` if channel response/request schemas exist there.
- Modify: `backend/app/api/v1/channels.py` for region create/update/list, overview, account scopes, and portal summaries.
- Modify: `backend/app/services/channel.py` for region validation, suggested code/name behavior, allocation scope behavior, and portal aggregation.
- Modify: `backend/tests/test_api/test_channel.py` for API behavior.
- Modify: `backend/tests/test_services/test_channel_svc.py` for service behavior.
- Modify: `frontend/apps/admin/src/app/(dashboard)/channels/page.tsx` for the guided region flow.
- Modify: `frontend/apps/admin/src/app/(dashboard)/channel-portal/page.tsx` for distributor portal visibility if present.
- Modify: `frontend/apps/admin/src/app/(dashboard)/store-portal/page.tsx` only to keep store optional and hidden when no store scope exists.
- Modify: `frontend/apps/admin/src/app/(dashboard)/channels/__tests__/page.test.tsx` for component coverage.
- Modify: `frontend/apps/admin/src/lib/api.ts` only if request/response types need adjustment.
- Modify or create: `frontend/e2e/channel-region-loop.spec.ts` for browser coverage.
- Modify: seed/demo data files under `backend/app/seed*` or existing seed scripts if present.

---

## Task 1: Backend Region Contract

- [ ] Add tests that `POST /api/v1/channels/regions` accepts `name`, `province`, `city`, `distributor_id`, and optional `status`, but does not require `store_id`.
- [ ] Add tests that region code is generated when omitted and starts with `REG-`.
- [ ] Add tests that inactive or cross-tenant distributors cannot be used.
- [ ] Implement service validation in `backend/app/services/channel.py`.
- [ ] Keep `PATCH /api/v1/channels/regions/{id}` for name, province, city, distributor, and status updates.
- [ ] Run:

```bash
cd backend
uv run pytest tests/test_api/test_channel.py tests/test_services/test_channel_svc.py -q
uv run ruff check app/api/v1/channels.py app/services/channel.py tests/test_api/test_channel.py tests/test_services/test_channel_svc.py
uv run ruff format --check app/api/v1/channels.py app/services/channel.py tests/test_api/test_channel.py tests/test_services/test_channel_svc.py
```

Expected: channel tests pass and Ruff reports no issues.

## Task 2: Backend Closed-Loop Aggregation

- [ ] Add tests that code allocation can target distributor or region without store.
- [ ] Add tests that region list returns distributor name, store count, allocated code count, status, and `updated_at`.
- [ ] Add tests that diversion clue list/detail can filter by distributor and region; store fields are nullable.
- [ ] Add tests that distributor portal summary returns region-level allocation, scan statistics, and pending clue counts.
- [ ] Add tests that store portal summary returns data only when the current account has a store scope.
- [ ] Implement aggregation in `backend/app/services/channel.py` and expose it through `backend/app/api/v1/channels.py`.
- [ ] Run the same backend command set from Task 1.

Expected: distributor/region flows work without store data; store-scoped APIs still work when store data exists.

## Task 3: Admin Region Creation Flow

- [ ] Add a Vitest case that opening region creation from a distributor context preselects the distributor.
- [ ] Add a Vitest case that province/city selection suggests a region name such as `上海区域` while keeping it editable.
- [ ] Add a Vitest case that creating a region shows generated code and next-step buttons: `分配码段`, `绑定入口账号`, `查看区域统计`, `可选创建门店`, `完成`.
- [ ] Update `frontend/apps/admin/src/app/(dashboard)/channels/page.tsx`:
  - Replace free-text province/city inputs with a province-city cascader or two linked selects.
  - Prefill distributor when the user enters from distributor creation success or distributor row action.
  - Default status to `启用`.
  - Do not show store as required copy or required input.
  - Keep the auto-code explanation concise.
- [ ] Run:

```bash
cd frontend/apps/admin
pnpm exec vitest run 'src/app/(dashboard)/channels/__tests__/page.test.tsx'
```

Expected: channel component tests pass.

## Task 4: Allocation, Scope, and Portal UX

- [ ] Update allocation UI so target level is explicit: `经销商`, `区域`, optional `门店`.
- [ ] When region is selected, derive distributor automatically and keep it visible.
- [ ] Update account scope UI so distributor and region scopes are primary; store scope is shown as optional.
- [ ] Update distributor portal to show region allocation, scan statistics, and pending clues.
- [ ] Update store portal so it is accessible only for store-scoped accounts and does not appear as the default next step for distributor/region users.
- [ ] Add Vitest coverage for the allocation target selector and portal visibility.
- [ ] Run:

```bash
cd frontend/apps/admin
pnpm exec vitest run
```

Expected: Admin tests pass.

## Task 5: Demo Data and E2E

- [ ] Update seed/demo data with one brand admin, one distributor account, two regions, one optional store, code allocations, scan records, and one pending diversion clue.
- [ ] Add Playwright flow:
  - Brand admin creates distributor.
  - Brand admin creates region from distributor context.
  - Brand admin allocates code to region without creating a store.
  - Distributor account views region allocation and clue summary.
  - Brand admin resolves the diversion clue.
  - Optional store account sees store portal only when bound.
- [ ] Run:

```bash
cd frontend
pnpm test:e2e
```

Expected: full channel loop passes with region as the primary scope and store as optional.

## Task 6: Full Verification and Handoff

- [ ] Run:

```bash
cd backend
uv run pytest tests/test_api/test_channel.py tests/test_services/test_channel_svc.py -q
uv run ruff check .
uv run ruff format --check .
```

- [ ] Run:

```bash
cd frontend/apps/admin
pnpm exec vitest run
```

- [ ] Run:

```bash
cd frontend
pnpm lint:admin
pnpm build
```

- [ ] Browser-verify:
  - `http://localhost:3000/channels`
  - `http://localhost:3000/channel-portal`
  - `http://localhost:3000/store-portal`

Expected: all checks pass; `/channels` supports distributor and region closed-loop flow without forcing store creation.

## Bead

Track execution under `yimatong-n9r`.
