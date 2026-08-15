"""expand diversion investigation authority

Revision ID: u7b0c1d2e3f4
Revises: u7a3f4a5b6c7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u7b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "u7a3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_policy(table: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON public.{table}")
    op.execute(
        f"CREATE POLICY tenant_isolation ON public.{table} "
        "USING (tenant_id=public.current_tenant_id()) "
        "WITH CHECK (tenant_id=public.current_tenant_id())"
    )


def _restore_u7a3_tenant_control_policy(table: str) -> None:
    """Restore the guarded tenant/control policy that existed at the U7A3 boundary."""

    setting_guard = (
        "current_setting('app.bypass_rls', true) = 'true' "
        "AND has_parameter_privilege(session_user, 'app.bypass_rls', 'SET')"
    )
    # k629 hardened the older diversion_clues policy by replacing its bypass
    # marker as a grouped subtree. The later evidence/history policies were
    # created with the privilege guard already inline. Preserve that exact
    # U7A3 catalog shape rather than merely recreating equivalent predicates.
    if table == "diversion_clues":
        guarded_control = f"(public.current_tenant_id() IS NULL AND ({setting_guard}))"
    else:
        guarded_control = f"(public.current_tenant_id() IS NULL AND {setting_guard})"
    expression = f"({table}.tenant_id = public.current_tenant_id() OR {guarded_control})"
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON public.{table}")
    op.execute(
        f"CREATE POLICY tenant_isolation ON public.{table} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )


def upgrade() -> None:
    # Existing conclusions and provenance cannot be guessed. Stop before DDL
    # unless every legacy row has a shape that can be migrated exactly.
    op.execute(
        r"""
        DO $preflight$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM public.diversion_clues
            WHERE (resolved AND investigation_status NOT IN
                    ('confirmed_diversion','false_positive','normal_transfer'))
               OR (NOT resolved AND investigation_status NOT IN ('open','pending_evidence'))
               OR observation_count<=0
          ) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
              MESSAGE='legacy diversion conclusion requires operator classification';
          END IF;
          IF EXISTS (
            SELECT 1 FROM public.diversion_clues
            WHERE resolved AND (resolved_at IS NULL OR resolved_by_account_id IS NULL)
          ) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
              MESSAGE='legacy diversion conclusion lacks actor provenance';
          END IF;
          IF EXISTS (
            SELECT 1 FROM public.diversion_evidence evidence
            WHERE evidence.uploaded_by IS NULL
               OR evidence.uploaded_by !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
               OR NOT EXISTS (
                 SELECT 1 FROM public.accounts account
                 WHERE account.tenant_id=evidence.tenant_id
                   AND account.id=evidence.uploaded_by::uuid
               )
          ) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
              MESSAGE='legacy diversion evidence lacks tenant-bound actor provenance';
          END IF;
          IF EXISTS (
            SELECT 1 FROM public.diversion_investigation_history history
            WHERE history.changed_by IS NOT NULL
              AND (history.changed_by !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                OR NOT EXISTS (
                  SELECT 1 FROM public.accounts account
                  WHERE account.tenant_id=history.tenant_id
                    AND account.id=history.changed_by::uuid
                ))
          ) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
              MESSAGE='legacy diversion history has invalid actor provenance';
          END IF;
          IF EXISTS (
            SELECT 1 FROM public.diversion_clues
            WHERE NOT resolved GROUP BY tenant_id,public_id,rule_name HAVING count(*)>1
          ) THEN
            RAISE EXCEPTION USING ERRCODE='23514',
              MESSAGE='duplicate open diversion clues require operator reconciliation';
          END IF;
        END
        $preflight$
        """
    )

    op.add_column("diversion_clues", sa.Column("version", sa.BigInteger(), server_default="1", nullable=False))
    op.add_column("diversion_evidence", sa.Column("uploaded_by_account_id", sa.Uuid(), nullable=True))
    op.add_column("diversion_evidence", sa.Column("evidence_digest", sa.String(64), nullable=True))
    op.add_column("diversion_investigation_history", sa.Column("changed_by_account_id", sa.Uuid(), nullable=True))
    op.execute("UPDATE public.diversion_evidence SET uploaded_by_account_id=uploaded_by::uuid")
    op.execute(
        "UPDATE public.diversion_evidence SET evidence_digest=encode(digest(convert_to(" 
        "jsonb_build_array(evidence_type,source,file_url,description)::text,'UTF8'),'sha256'),'hex')"
    )
    op.execute(
        "UPDATE public.diversion_investigation_history "
        "SET changed_by_account_id=changed_by::uuid WHERE changed_by IS NOT NULL"
    )
    op.alter_column("diversion_evidence", "uploaded_by_account_id", nullable=False)
    op.alter_column("diversion_evidence", "evidence_digest", nullable=False)

    op.create_unique_constraint("uq_diversion_clues_tenant_id_id", "diversion_clues", ["tenant_id", "id"])
    op.create_unique_constraint("uq_diversion_evidence_tenant_id_id", "diversion_evidence", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_diversion_history_tenant_id_id", "diversion_investigation_history", ["tenant_id", "id"]
    )
    op.create_index(
        "uq_diversion_clues_open_subject_rule",
        "diversion_clues",
        ["tenant_id", "public_id", "rule_name"],
        unique=True,
        postgresql_where=sa.text("resolved=false"),
    )
    op.create_index(
        "ix_diversion_evidence_tenant_clue_time",
        "diversion_evidence",
        ["tenant_id", "clue_id", "uploaded_at"],
    )
    op.create_index(
        "ix_diversion_history_tenant_clue_time",
        "diversion_investigation_history",
        ["tenant_id", "clue_id", "changed_at"],
    )

    foreign_keys = (
        ("diversion_clues", "fk_diversion_clues_tenant", "(tenant_id) REFERENCES public.tenants(id)"),
        ("diversion_clues", "fk_diversion_clues_tenant_code_item", "(tenant_id,code_item_id) REFERENCES public.code_items(tenant_id,id)"),
        ("diversion_clues", "fk_diversion_clues_tenant_distributor", "(tenant_id,distributor_id) REFERENCES public.distributors(tenant_id,id)"),
        ("diversion_clues", "fk_diversion_clues_tenant_region", "(tenant_id,region_id) REFERENCES public.regions(tenant_id,id)"),
        ("diversion_clues", "fk_diversion_clues_tenant_assignee", "(tenant_id,assigned_to) REFERENCES public.accounts(tenant_id,id)"),
        ("diversion_clues", "fk_diversion_clues_tenant_resolver", "(tenant_id,resolved_by_account_id) REFERENCES public.accounts(tenant_id,id)"),
        ("diversion_evidence", "fk_diversion_evidence_tenant", "(tenant_id) REFERENCES public.tenants(id)"),
        ("diversion_evidence", "fk_diversion_evidence_tenant_clue", "(tenant_id,clue_id) REFERENCES public.diversion_clues(tenant_id,id)"),
        ("diversion_evidence", "fk_diversion_evidence_tenant_actor", "(tenant_id,uploaded_by_account_id) REFERENCES public.accounts(tenant_id,id)"),
        ("diversion_investigation_history", "fk_diversion_history_tenant", "(tenant_id) REFERENCES public.tenants(id)"),
        ("diversion_investigation_history", "fk_diversion_history_tenant_clue", "(tenant_id,clue_id) REFERENCES public.diversion_clues(tenant_id,id)"),
        ("diversion_investigation_history", "fk_diversion_history_tenant_actor", "(tenant_id,changed_by_account_id) REFERENCES public.accounts(tenant_id,id)"),
    )
    for table, name, definition in foreign_keys:
        op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} FOREIGN KEY {definition} NOT VALID")
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")
    # Composite ownership is now authoritative. Remove only the exact legacy
    # objects installed by n5/o6 after their replacements validate.
    op.drop_constraint("diversion_evidence_clue_id_fkey", "diversion_evidence", type_="foreignkey")
    op.drop_constraint(
        "diversion_investigation_history_clue_id_fkey",
        "diversion_investigation_history",
        type_="foreignkey",
    )
    op.drop_index("ix_diversion_history_clue_id", table_name="diversion_investigation_history")

    checks = (
        ("diversion_clues", "ck_diversion_clues_investigation_status", "investigation_status IN ('open','pending_evidence','confirmed_diversion','false_positive','normal_transfer')"),
        ("diversion_clues", "ck_diversion_clues_observation_count", "observation_count>0"),
        ("diversion_clues", "ck_diversion_clues_version_positive", "version>0"),
        ("diversion_clues", "ck_diversion_clues_resolution_state", "(NOT resolved AND investigation_status IN ('open','pending_evidence') AND resolved_at IS NULL AND resolved_by_account_id IS NULL) OR (resolved AND investigation_status IN ('confirmed_diversion','false_positive','normal_transfer') AND resolved_at IS NOT NULL AND resolved_by_account_id IS NOT NULL)"),
        ("diversion_evidence", "ck_diversion_evidence_type", "evidence_type IN ('transfer','order','logistics','explanation','other')"),
        ("diversion_evidence", "ck_diversion_evidence_content", "NULLIF(trim(file_url),'') IS NOT NULL OR NULLIF(trim(description),'') IS NOT NULL"),
        ("diversion_evidence", "ck_diversion_evidence_digest", "evidence_digest ~ '^[0-9a-f]{64}$'"),
        ("diversion_investigation_history", "ck_diversion_history_status", "to_status IN ('open','pending_evidence','confirmed_diversion','false_positive','normal_transfer') AND (from_status IS NULL OR from_status IN ('open','pending_evidence','confirmed_diversion','false_positive','normal_transfer'))"),
    )
    for table, name, expression in checks:
        op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} CHECK ({expression}) NOT VALID")
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")

    op.create_table(
        "diversion_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("scan_event_id", sa.Uuid(), nullable=False),
        sa.Column("clue_id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(20), nullable=False),
        sa.Column("code_item_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("detected_city", sa.String(200), nullable=False),
        sa.Column("expected_region", sa.String(200), nullable=False),
        sa.Column("location_source", sa.String(30), nullable=False),
        sa.Column("location_accuracy", sa.String(20), nullable=False),
        sa.Column("location_authorized", sa.Boolean(), nullable=True),
        sa.Column("distributor_id", sa.Uuid(), nullable=True),
        sa.Column("region_id", sa.Uuid(), nullable=True),
        sa.Column("rule_name", sa.String(50), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("clue_version", sa.BigInteger(), nullable=False),
        sa.Column("investigation_status", sa.String(30), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("observation_count", sa.BigInteger(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_diversion_observations"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_diversion_observations_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "scan_event_id", "rule_name", name="uq_diversion_observation_scan_rule"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_diversion_observation_idem"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_diversion_observations_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "clue_id"], ["diversion_clues.tenant_id", "diversion_clues.id"], name="fk_diversion_observations_tenant_clue"),
        sa.ForeignKeyConstraint(["tenant_id", "code_item_id"], ["code_items.tenant_id", "code_items.id"], name="fk_diversion_observations_tenant_code_item"),
        sa.CheckConstraint("rule_name IN ('cross_region_ip','cross_region_browser')", name="ck_diversion_observations_rule"),
        sa.CheckConstraint("confidence IN ('high','medium','low')", name="ck_diversion_observations_confidence"),
        sa.CheckConstraint("location_source IN ('ip_inference','browser_geolocation','manual')", name="ck_diversion_observations_location_source"),
        sa.CheckConstraint("location_accuracy IN ('high','medium','low','unknown')", name="ck_diversion_observations_location_accuracy"),
        sa.CheckConstraint("payload_digest ~ '^[0-9a-f]{64}$'", name="ck_diversion_observations_digest"),
        sa.CheckConstraint("clue_version>0", name="ck_diversion_observations_version"),
        sa.CheckConstraint("observation_count>0", name="ck_diversion_observations_count"),
    )
    op.create_index("ix_diversion_observations_tenant_clue_time", "diversion_observations", ["tenant_id", "clue_id", "observed_at"])

    op.create_table(
        "diversion_action_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("clue_id", sa.Uuid(), nullable=False),
        sa.Column("clue_version", sa.BigInteger(), nullable=False),
        sa.Column("investigation_status", sa.String(30), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("observation_count", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("audit_id", sa.Uuid(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_diversion_action_receipts"),
        sa.UniqueConstraint("tenant_id", "action", "idempotency_key", name="uq_diversion_receipts_idem"),
        sa.UniqueConstraint("tenant_id", "audit_id", name="uq_diversion_receipts_audit"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_diversion_receipts_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "clue_id"], ["diversion_clues.tenant_id", "diversion_clues.id"], name="fk_diversion_receipts_tenant_clue"),
        sa.ForeignKeyConstraint(["tenant_id", "actor_id"], ["accounts.tenant_id", "accounts.id"], name="fk_diversion_receipts_tenant_actor"),
        sa.CheckConstraint("clue_version>0", name="ck_diversion_receipts_version"),
        sa.CheckConstraint("observation_count>0", name="ck_diversion_receipts_observation_count"),
        sa.CheckConstraint("payload_digest ~ '^[0-9a-f]{64}$'", name="ck_diversion_receipts_digest"),
    )
    op.create_index("ix_diversion_receipts_tenant_clue", "diversion_action_receipts", ["tenant_id", "clue_id"])

    for table in ("diversion_clues", "diversion_evidence", "diversion_investigation_history", "diversion_observations", "diversion_action_receipts"):
        _tenant_policy(table)


def downgrade() -> None:
    facts = op.get_bind().execute(
        sa.text(
            "SELECT (SELECT count(*) FROM diversion_observations)+"
            "(SELECT count(*) FROM diversion_action_receipts)+"
            "(SELECT count(*) FROM diversion_evidence)+"
            "(SELECT count(*) FROM diversion_investigation_history)"
        )
    ).scalar_one()
    if facts:
        raise RuntimeError("u7b0 downgrade blocked: immutable diversion investigation facts exist")
    for table in ("diversion_action_receipts", "diversion_observations"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON public.{table}")
    op.drop_table("diversion_action_receipts")
    op.drop_table("diversion_observations")
    for table, name in (
        ("diversion_investigation_history", "ck_diversion_history_status"),
        ("diversion_evidence", "ck_diversion_evidence_digest"),
        ("diversion_evidence", "ck_diversion_evidence_content"),
        ("diversion_evidence", "ck_diversion_evidence_type"),
        ("diversion_clues", "ck_diversion_clues_resolution_state"),
        ("diversion_clues", "ck_diversion_clues_version_positive"),
        ("diversion_clues", "ck_diversion_clues_observation_count"),
        ("diversion_clues", "ck_diversion_clues_investigation_status"),
    ):
        op.drop_constraint(name, table, type_="check")
    op.drop_index("ix_diversion_history_tenant_clue_time", table_name="diversion_investigation_history")
    op.drop_index("ix_diversion_evidence_tenant_clue_time", table_name="diversion_evidence")
    op.drop_index("uq_diversion_clues_open_subject_rule", table_name="diversion_clues")
    for table, name in (
        ("diversion_investigation_history", "fk_diversion_history_tenant_actor"),
        ("diversion_investigation_history", "fk_diversion_history_tenant_clue"),
        ("diversion_investigation_history", "fk_diversion_history_tenant"),
        ("diversion_evidence", "fk_diversion_evidence_tenant_actor"),
        ("diversion_evidence", "fk_diversion_evidence_tenant_clue"),
        ("diversion_evidence", "fk_diversion_evidence_tenant"),
        ("diversion_clues", "fk_diversion_clues_tenant_resolver"),
        ("diversion_clues", "fk_diversion_clues_tenant_assignee"),
        ("diversion_clues", "fk_diversion_clues_tenant_region"),
        ("diversion_clues", "fk_diversion_clues_tenant_distributor"),
        ("diversion_clues", "fk_diversion_clues_tenant_code_item"),
        ("diversion_clues", "fk_diversion_clues_tenant"),
    ):
        op.drop_constraint(name, table, type_="foreignkey")
    op.create_foreign_key(
        "diversion_evidence_clue_id_fkey",
        "diversion_evidence",
        "diversion_clues",
        ["clue_id"],
        ["id"],
    )
    op.create_foreign_key(
        "diversion_investigation_history_clue_id_fkey",
        "diversion_investigation_history",
        "diversion_clues",
        ["clue_id"],
        ["id"],
    )
    op.create_index(
        "ix_diversion_history_clue_id",
        "diversion_investigation_history",
        ["clue_id"],
    )
    op.drop_constraint("uq_diversion_history_tenant_id_id", "diversion_investigation_history", type_="unique")
    op.drop_constraint("uq_diversion_evidence_tenant_id_id", "diversion_evidence", type_="unique")
    op.drop_constraint("uq_diversion_clues_tenant_id_id", "diversion_clues", type_="unique")
    op.drop_column("diversion_investigation_history", "changed_by_account_id")
    op.drop_column("diversion_evidence", "evidence_digest")
    op.drop_column("diversion_evidence", "uploaded_by_account_id")
    op.drop_column("diversion_clues", "version")
    for table in ("diversion_clues", "diversion_evidence", "diversion_investigation_history"):
        _restore_u7a3_tenant_control_policy(table)
