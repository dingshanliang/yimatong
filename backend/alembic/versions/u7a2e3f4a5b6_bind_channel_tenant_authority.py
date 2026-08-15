"""backfill and bind channel tenant authority

Revision ID: u7a2e3f4a5b6
Revises: u7a1d2e3f4a5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "u7a2e3f4a5b6"
down_revision: str | Sequence[str] | None = "u7a1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ATTACHED = (
    ("distributors", "uq_distributors_tenant_id_id_u7a"),
    ("regions", "uq_regions_tenant_id_id_u7a"),
    ("stores", "uq_stores_tenant_id_id_u7a"),
    ("channel_action_receipts", "uq_channel_receipts_idem_u7a"),
    ("channel_action_receipts", "uq_channel_receipts_audit_u7a"),
    ("code_allocations", "uq_code_alloc_tenant_id_u7a"),
    ("code_allocations", "uq_code_alloc_root_version_u7a"),
)

_FKS = (
    ("distributors", "fk_distributors_tenant", "(tenant_id) REFERENCES public.tenants(id)"),
    ("regions", "fk_regions_tenant", "(tenant_id) REFERENCES public.tenants(id)"),
    ("regions", "fk_regions_tenant_distributor", "(tenant_id,distributor_id) REFERENCES public.distributors(tenant_id,id)"),
    ("stores", "fk_stores_tenant", "(tenant_id) REFERENCES public.tenants(id)"),
    ("stores", "fk_stores_tenant_region", "(tenant_id,region_id) REFERENCES public.regions(tenant_id,id)"),
    ("stores", "fk_stores_tenant_distributor", "(tenant_id,distributor_id) REFERENCES public.distributors(tenant_id,id)"),
    ("account_channel_scopes", "fk_account_channel_scopes_tenant", "(tenant_id) REFERENCES public.tenants(id)"),
    ("account_channel_scopes", "fk_account_channel_scopes_tenant_account", "(tenant_id,account_id) REFERENCES public.accounts(tenant_id,id)"),
    ("account_channel_scopes", "fk_account_channel_scopes_tenant_distributor", "(tenant_id,distributor_id) REFERENCES public.distributors(tenant_id,id)"),
    ("account_channel_scopes", "fk_account_channel_scopes_tenant_region", "(tenant_id,region_id) REFERENCES public.regions(tenant_id,id)"),
    ("account_channel_scopes", "fk_account_channel_scopes_tenant_store", "(tenant_id,store_id) REFERENCES public.stores(tenant_id,id)"),
    ("code_allocations", "fk_code_allocations_tenant", "(tenant_id) REFERENCES public.tenants(id)"),
    ("code_allocations", "fk_code_allocations_tenant_batch", "(tenant_id,batch_id) REFERENCES public.code_batches(tenant_id,id)"),
    ("code_allocations", "fk_code_allocations_tenant_store", "(tenant_id,store_id) REFERENCES public.stores(tenant_id,id)"),
    ("code_allocations", "fk_code_allocations_tenant_region", "(tenant_id,region_id) REFERENCES public.regions(tenant_id,id)"),
    ("code_allocations", "fk_code_allocations_tenant_distributor", "(tenant_id,distributor_id) REFERENCES public.distributors(tenant_id,id)"),
    ("code_allocations", "fk_code_allocations_actor", "(actor_tenant_id,actor_id) REFERENCES public.accounts(tenant_id,id)"),
    ("channel_action_receipts", "fk_channel_action_receipts_tenant", "(tenant_id) REFERENCES public.tenants(id)"),
    ("channel_action_receipts", "fk_channel_action_receipts_actor", "(actor_tenant_id,actor_id) REFERENCES public.accounts(tenant_id,id)"),
)


def _drop_legacy_single_fks() -> None:
    op.execute(
        r"""
        DO $drop$
        DECLARE row record;
        BEGIN
          FOR row IN
            SELECT conrelid::regclass AS relation, quote_ident(conname) AS constraint_name
            FROM pg_constraint
            WHERE contype='f' AND connamespace='public'::regnamespace
              AND conrelid IN (
                'public.regions'::regclass,'public.stores'::regclass,
                'public.code_allocations'::regclass,'public.account_channel_scopes'::regclass
              )
              AND array_length(conkey,1)=1
              AND conname NOT LIKE 'fk_%_tenant'
          LOOP
            EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %s',row.relation,row.constraint_name);
          END LOOP;
        END
        $drop$
        """
    )


def upgrade() -> None:
    op.execute(
        r"""
        DO $preflight$
        BEGIN
          IF EXISTS (SELECT 1 FROM public.account_channel_scopes
                     WHERE scope_type NOT IN ('distributor','region','store')
                        OR CASE scope_type WHEN 'distributor' THEN distributor_id
                             WHEN 'region' THEN region_id WHEN 'store' THEN store_id END IS NULL) THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='account channel scope has no exact target';
          END IF;
          IF EXISTS (SELECT 1 FROM public.code_allocations
                     WHERE quantity<=0 OR COALESCE(store_id,region_id,distributor_id) IS NULL) THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='legacy code allocation is not recoverable as an active target';
          END IF;
          IF EXISTS (SELECT 1 FROM public.stores s JOIN public.regions r
                     ON r.tenant_id=s.tenant_id AND r.id=s.region_id
                     WHERE r.distributor_id IS NOT NULL
                       AND s.distributor_id IS DISTINCT FROM r.distributor_id) THEN
            RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='store hierarchy conflicts with region distributor';
          END IF;
        END
        $preflight$
        """
    )
    op.execute(
        "UPDATE public.account_channel_scopes SET "
        "target_id=CASE scope_type WHEN 'distributor' THEN distributor_id WHEN 'region' THEN region_id ELSE store_id END, "
        "distributor_id=CASE WHEN scope_type='distributor' THEN distributor_id END, "
        "region_id=CASE WHEN scope_type='region' THEN region_id END, "
        "store_id=CASE WHEN scope_type='store' THEN store_id END WHERE target_id IS NULL"
    )
    op.execute(
        "UPDATE public.code_allocations SET allocation_root_id=id, "
        "target_type=CASE WHEN store_id IS NOT NULL THEN 'store' WHEN region_id IS NOT NULL THEN 'region' ELSE 'distributor' END, "
        "target_id=COALESCE(store_id,region_id,distributor_id), effective_from=COALESCE(effective_from,created_at), "
        "action='allocate',status='active' WHERE allocation_root_id IS NULL"
    )

    for table, name in _ATTACHED:
        op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} UNIQUE USING INDEX {name}")
    for table, name, definition in _FKS:
        op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} FOREIGN KEY {definition} NOT VALID")
    for table, name, _definition in _FKS:
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")
    _drop_legacy_single_fks()

    op.alter_column("account_channel_scopes", "target_id", nullable=False)
    op.alter_column("code_allocations", "allocation_root_id", nullable=False)
    op.alter_column("code_allocations", "effective_from", nullable=False)

    checks = (
        ("distributors", "ck_distributors_phone_recovery_state", "contact_phone_recovery_state IN ('absent','encrypted','recovered','legacy_unknown')"),
        ("distributors", "ck_distributors_status", "status IN ('active','inactive')"),
        ("distributors", "ck_distributors_version_positive", "version>0"),
        ("regions", "ck_regions_status", "status IN ('active','inactive')"),
        ("regions", "ck_regions_version_positive", "version>0"),
        ("stores", "ck_stores_status", "status IN ('active','inactive')"),
        ("stores", "ck_stores_version_positive", "version>0"),
        ("account_channel_scopes", "ck_account_channel_scopes_type", "scope_type IN ('distributor','region','store')"),
        ("account_channel_scopes", "ck_account_channel_scopes_version_positive", "version>0"),
        ("account_channel_scopes", "ck_account_channel_scopes_exact_target", "(scope_type='distributor' AND target_id=distributor_id AND distributor_id IS NOT NULL AND region_id IS NULL AND store_id IS NULL) OR (scope_type='region' AND target_id=region_id AND distributor_id IS NULL AND region_id IS NOT NULL AND store_id IS NULL) OR (scope_type='store' AND target_id=store_id AND distributor_id IS NULL AND region_id IS NULL AND store_id IS NOT NULL)"),
        ("code_allocations", "ck_code_allocations_version_positive", "version>0"),
        ("code_allocations", "ck_code_allocations_quantity_nonnegative", "quantity>=0"),
        ("code_allocations", "ck_code_allocations_action", "action IN ('allocate','reassign','archive')"),
        ("code_allocations", "ck_code_allocations_status", "status IN ('active','archived')"),
        ("code_allocations", "ck_code_allocations_target_shape", "(action='archive' AND status='archived' AND target_type IS NULL AND target_id IS NULL AND quantity=0) OR (action<>'archive' AND status='active' AND target_type IN ('distributor','region','store') AND target_id IS NOT NULL AND quantity>0)"),
        ("channel_action_receipts", "ck_channel_action_receipts_version_positive", "resource_version>0"),
    )
    for table, name, expression in checks:
        op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} CHECK ({expression}) NOT VALID")
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")


def downgrade() -> None:
    facts = op.get_bind().execute(sa.text("SELECT count(*) FROM public.channel_action_receipts")).scalar_one()
    if facts:
        raise RuntimeError("u7a2 downgrade blocked: channel action receipts are immutable facts")
    for table, name in (
        ("channel_action_receipts", "ck_channel_action_receipts_version_positive"),
        ("code_allocations", "ck_code_allocations_target_shape"),
        ("code_allocations", "ck_code_allocations_status"),
        ("code_allocations", "ck_code_allocations_action"),
        ("code_allocations", "ck_code_allocations_quantity_nonnegative"),
        ("code_allocations", "ck_code_allocations_version_positive"),
        ("account_channel_scopes", "ck_account_channel_scopes_exact_target"),
        ("account_channel_scopes", "ck_account_channel_scopes_version_positive"),
        ("account_channel_scopes", "ck_account_channel_scopes_type"),
        ("stores", "ck_stores_version_positive"),
        ("stores", "ck_stores_status"),
        ("regions", "ck_regions_version_positive"),
        ("regions", "ck_regions_status"),
        ("distributors", "ck_distributors_version_positive"),
        ("distributors", "ck_distributors_status"),
        ("distributors", "ck_distributors_phone_recovery_state"),
    ):
        op.drop_constraint(name, table, type_="check")
    op.alter_column("code_allocations", "effective_from", nullable=True)
    op.alter_column("code_allocations", "allocation_root_id", nullable=True)
    op.alter_column("account_channel_scopes", "target_id", nullable=True)
    for table, name, _definition in reversed(_FKS):
        op.drop_constraint(name, table, type_="foreignkey")
    # Restore the exact pre-u7a2 single-column ownership constraints which
    # were replaced by tenant-composite FKs during upgrade.
    for table, name, column, parent in (
        ("regions", "regions_distributor_id_fkey", "distributor_id", "distributors"),
        ("stores", "stores_distributor_id_fkey", "distributor_id", "distributors"),
        ("stores", "stores_region_id_fkey", "region_id", "regions"),
        ("code_allocations", "code_allocations_batch_id_fkey", "batch_id", "code_batches"),
        ("code_allocations", "code_allocations_distributor_id_fkey", "distributor_id", "distributors"),
        ("code_allocations", "code_allocations_store_id_fkey", "store_id", "stores"),
        ("code_allocations", "fk_code_allocations_region_id_regions", "region_id", "regions"),
        ("account_channel_scopes", "account_channel_scopes_account_id_fkey", "account_id", "accounts"),
        ("account_channel_scopes", "account_channel_scopes_distributor_id_fkey", "distributor_id", "distributors"),
        ("account_channel_scopes", "account_channel_scopes_store_id_fkey", "store_id", "stores"),
        ("account_channel_scopes", "fk_account_channel_scopes_region_id_regions", "region_id", "regions"),
    ):
        op.create_foreign_key(name, table, parent, [column], ["id"])
    for table, name in reversed(_ATTACHED):
        op.drop_constraint(name, table, type_="unique")
    # Restore the exact u7a1 target catalog after USING INDEX consumed the
    # online-built indexes.  This short operation is downgrade-only and is
    # guarded above against live U07 facts.
    for name, table, columns in (
        ("uq_distributors_tenant_id_id_u7a", "distributors", "tenant_id,id"),
        ("uq_regions_tenant_id_id_u7a", "regions", "tenant_id,id"),
        ("uq_stores_tenant_id_id_u7a", "stores", "tenant_id,id"),
        ("uq_channel_receipts_idem_u7a", "channel_action_receipts", "tenant_id,action,idempotency_key"),
        ("uq_channel_receipts_audit_u7a", "channel_action_receipts", "tenant_id,audit_id"),
        ("uq_code_alloc_tenant_id_u7a", "code_allocations", "tenant_id,id"),
        ("uq_code_alloc_root_version_u7a", "code_allocations", "tenant_id,allocation_root_id,version"),
    ):
        op.execute(f"CREATE UNIQUE INDEX {name} ON public.{table} ({columns})")
