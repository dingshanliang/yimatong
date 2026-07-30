---
name: create-migration
description: Create or revise a Yimatong Alembic migration. Use for model/schema changes, new tenant tables, data migrations, indexes, constraints, or RLS policy changes that require safe upgrade, downgrade, and clean-database verification.
---

# Create a Yimatong Migration

## Establish current truth

1. Read the affected SQLAlchemy models and the nearest relevant migrations.
2. Run:

```bash
cd backend
uv run alembic heads
uv run alembic current
```

3. Confirm whether the table is tenant-scoped, platform-scoped, public/consumer-facing, large, or partitioned.
4. Do not copy an old migration template without checking the current model and RLS helpers.

## Generate and edit

Use the repository environment:

```bash
cd backend
uv run alembic revision --autogenerate -m "<description>"
```

Use `uv run alembic revision -m "<description>"` for data-only, RLS, or other changes that autogenerate cannot express safely.

Review generated operations line by line. In migrations, current UUID columns use `sa.Uuid()`. In application models, follow the existing SQLAlchemy 2.0 `Mapped` / `mapped_column` style. Do not assume every model already inherits `TenantModel`; follow the neighboring model while preserving explicit `tenant_id`.

## Safety requirements

- Every new tenant business table includes a non-null UUID `tenant_id`, an appropriate tenant index, and the repository's current RLS policy pattern.
- Platform-global tables are exceptions only when the domain and access path are explicitly platform-scoped.
- Every `upgrade()` operation has a practical `downgrade()` counterpart. If downgrade would lose transformed data, state that limitation and require an explicit decision.
- Add non-null columns to populated tables with a safe staged backfill or justified server default.
- Validate foreign keys, unique constraints, tenant-aware uniqueness, and indexes used by filters or ordering.
- Use concurrent index creation only when table size and PostgreSQL transaction constraints justify it; use Alembic's autocommit block correctly.
- Do not alter partitioned or high-volume tables such as `scan_events` without inspecting the current partition strategy and requesting migration review.
- Never embed production secrets or environment-specific identifiers.

## Verify

Run the smallest relevant checks, then the migration gates:

```bash
cd backend
uv run ruff check <model-files> <migration-file>
uv run pytest tests/test_alembic_migration.py tests/test_migration_on_clean_db.py
```

For a disposable development database, also prove the intended transition and rollback:

```bash
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

Do not run downgrade against shared, UAT, or production data. Route migrations that change RLS, tenant boundaries, encryption/PII, large-table indexes, or destructive data through the repository migration reviewer before handoff.

## Report

Return:

- revision and parent revision;
- schema/data/RLS impact;
- upgrade and downgrade behavior;
- checks run and exact result;
- rollout or recovery caveats.
