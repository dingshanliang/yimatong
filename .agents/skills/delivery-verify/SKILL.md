---
name: delivery-verify
description: Verify a Yimatong Bead, implementation, branch, or working-tree change before closure or handoff. Use when asked to validate delivery, check acceptance criteria, prove a story complete, run change-aware gates, review release readiness, or collect Browser + API + read-only database evidence.
---

# Verify Yimatong Delivery

## Establish scope

1. Read root and nested `AGENTS.md`.
2. Preserve unrelated work:

```bash
git status --short
git diff --name-only
git diff --cached --name-only
```

3. If the user supplies a Bead ID, run `bd show <id>` and treat its acceptance criteria as the contract. Otherwise verify only the explicitly requested diff or feature.
4. Do not create, update, or close Beads merely because this skill is invoked. Tracker changes require an existing task relationship or explicit user authorization.
5. Identify the fixed point: working tree, staged diff, commit, branch, or merge-base. Never silently widen the review to unrelated changes.

## Build the verification matrix

Select gates from the affected surface; do not run every suite mechanically.

| Changed surface                                   | Required minimum                                                                                              |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Backend Python                                    | Ruff on changed files plus focused unit/service/API tests                                                     |
| Auth, middleware, permissions, public routes, PII | Focused API tests, isolation/security tests, and security reviewer                                            |
| Models or Alembic                                 | Migration chain/config tests; clean-database test when disposable PostgreSQL is available; migration reviewer |
| Shared or design-token package                    | Package tests/typecheck plus `pnpm check`                                                                     |
| Admin, H5, Platform                               | App lint/typecheck, focused component tests, and build when routing/config/bundling changed                   |
| Browser journey                                   | Real browser flow with the correct runtime ports and fresh authentication                                     |
| Cross-layer product acceptance                    | Browser + API + read-only database evidence for each hard gate                                                |
| Workflow/config/docs                              | Syntax/parser check and validation of every referenced command/path                                           |

When a migration or security boundary is present, route the diff to the repository reviewer agent if subagent tools are available. The reviewer is advisory evidence; the primary agent still owns validation and the final status.

## Run deterministic checks first

Use repository commands and expand only when earlier gates pass:

```bash
cd backend && uv run ruff check <changed-python-files>
cd backend && uv run pytest <focused-tests>

cd frontend && pnpm check
cd frontend && pnpm lint
cd frontend && pnpm test:admin
cd frontend && pnpm build
```

Confirm `pnpm --version` is 10.x before frontend checks. Do not repair unrelated baseline failures under the delivery's name; report them separately with evidence.

For migrations, use only the dedicated disposable database configured for tests. Never downgrade, reset, seed, or mutate shared/UAT/production data during verification.

## Collect product evidence

For acceptance criteria that require the three-layer gate:

1. **Browser:** complete the real Admin, H5, Platform, distributor, or store journey; record the business-visible result.
2. **API:** capture the relevant authenticated request, status, and business payload fields without exposing tokens or PII.
3. **Database:** use a read-only query to prove tenant, lifecycle, attribution, audit, or evidence state.

Screenshots, seed success, component tests, or mocked APIs cannot replace missing layers. External callbacks or credentials that cannot be exercised are `pending_external`, not passed.

## Decide status

Use exactly one:

- `passed`: every acceptance criterion and required gate has direct evidence.
- `failed`: implementation or verification contradicts the contract.
- `pending_external`: only an unavailable external system, credential, approval, or callback blocks proof.

Do not use “mostly passed.” A failed or pending hard gate blocks completion.

## Close only when authorized

If the user explicitly requested full delivery through closure and status is `passed`:

1. Recheck the scoped diff and worktree.
2. Update/close the existing Bead with concise evidence.
3. Commit only the verified files if commit authorization is part of the request.

Never push, merge, deploy, or close a task solely because verification passed.

## Report

```markdown
Status: passed | failed | pending_external
Scope: <Bead/diff/fixed point>

Acceptance evidence:

- <criterion>: Browser=<result>; API=<result>; DB=<result>

Checks:

- `<command>`: passed/failed

Reviewers:

- migration/security: <result or not applicable>

Blockers:

- <concrete blocker or none>

Next action:

- <single highest-value action>
```
