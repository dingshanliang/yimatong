"""bind verified WeCom receipts to tenant contact projection

Revision ID: u6l2e3f4a5b6
Revises: u6l1d2e3f4a5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

from alembic import context, op

revision: str = "u6l2e3f4a5b6"
down_revision: str | Sequence[str] | None = "u6l1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTACT_KEY = "uq_wecom_external_contacts_tenant_id_id_u6l"
_CONTACT_FK = "fk_wecom_callback_receipts_tenant_contact"
_NOT_NULL_CHECK = "ck_wecom_callback_receipts_contact_id_nn_u6l"
_DOWNGRADE_INDEX = "uq_wecom_external_contacts_tenant_id_id_u6l_downgrade"
_DOWNGRADE_EXPECTED = (
    "CREATE UNIQUE INDEX uq_wecom_external_contacts_tenant_id_id_u6l_downgrade "
    "ON public.wecom_external_contacts USING btree (tenant_id, id)"
)


def _destination_is_below(owning_revision: str) -> bool:
    destination = context.get_revision_argument()
    if destination is None:
        return True
    if isinstance(destination, tuple):
        raise RuntimeError("WeCom receipt authority downgrade requires a single linear destination")
    try:
        return bool(tuple(ScriptDirectory.from_config(context.config).iterate_revisions(owning_revision, destination)))
    except RangeNotAncestorError:
        return False


def _downstream_fact_preflight() -> None:
    """Block deep immutable-fact crossings before this head changes any bytes."""

    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM public.wecom_callback_receipts")).scalar_one():
        raise RuntimeError("cannot downgrade while immutable verified WeCom callback receipts exist")
    if (
        _destination_is_below("u6k1d2e3f4a5")
        and bind.execute(sa.text("SELECT count(*) FROM public.campaign_delivery_callback_attempts")).scalar_one()
    ):
        raise RuntimeError("u6l2 downgrade blocked: campaign delivery callback attempts are immutable facts")
    if (
        _destination_is_below("u6b5c6d7e8f9")
        and bind.execute(
            sa.text("SELECT count(*) FROM public.benefit_claims WHERE request_digest IS NOT NULL")
        ).scalar_one()
    ):
        raise RuntimeError("u6l2 downgrade blocked: bound benefit claims are immutable facts")
    if (
        _destination_is_below("u7c0e1f2a3b4")
        and bind.execute(sa.text("SELECT count(*) FROM public.risk_action_receipts")).scalar_one()
    ):
        raise RuntimeError("u6l2 downgrade blocked: risk action receipts are immutable facts")
    if (
        _destination_is_below("u7b0c1d2e3f4")
        and bind.execute(
            sa.text(
                "SELECT (SELECT count(*) FROM public.diversion_observations)+"
                "(SELECT count(*) FROM public.diversion_action_receipts)+"
                "(SELECT count(*) FROM public.diversion_evidence)+"
                "(SELECT count(*) FROM public.diversion_investigation_history)"
            )
        ).scalar_one()
    ):
        raise RuntimeError("u6l2 downgrade blocked: immutable diversion investigation facts exist")
    if (
        _destination_is_below("u7a0c1d2e3f4")
        and bind.execute(sa.text("SELECT count(*) FROM public.channel_action_receipts")).scalar_one()
    ):
        raise RuntimeError("u6l2 downgrade blocked: channel action receipts are immutable facts")


def _downgrade_index_facts() -> tuple[bool, bool, str | None]:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT i.indisvalid,i.indisunique,pg_get_indexdef(i.indexrelid) FROM pg_index i "
                "JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname=:name"
            ),
            {"name": _DOWNGRADE_INDEX},
        )
        .one_or_none()
    )
    return (False, False, None) if row is None else (bool(row[0]), bool(row[1]), str(row[2]))


def _prepare_downgrade_index() -> None:
    valid, unique, definition = _downgrade_index_facts()
    if valid and unique and definition == _DOWNGRADE_EXPECTED:
        return
    if definition is not None and (not unique or definition != _DOWNGRADE_EXPECTED):
        raise RuntimeError(f"refusing unexpected WeCom downgrade index public.{_DOWNGRADE_INDEX}")
    with op.get_context().autocommit_block():
        if definition is not None:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_DOWNGRADE_INDEX}")
        try:
            op.execute(
                f"CREATE UNIQUE INDEX CONCURRENTLY {_DOWNGRADE_INDEX} ON public.wecom_external_contacts (tenant_id,id)"
            )
        except BaseException:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_DOWNGRADE_INDEX}")
            raise
    valid, unique, definition = _downgrade_index_facts()
    if not valid or not unique or definition != _DOWNGRADE_EXPECTED:
        raise RuntimeError(f"WeCom downgrade index public.{_DOWNGRADE_INDEX} is not exact and valid")


def _backfill_and_validate() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("ALTER TABLE public.wecom_callback_receipts DISABLE TRIGGER trg_guard_wecom_callback_receipts")
    )
    bind.execute(
        sa.text(
            "UPDATE public.wecom_callback_receipts r SET contact_id=(r.result->>'contact_id')::uuid "
            "FROM public.wecom_external_contacts c WHERE r.contact_id IS NULL "
            "AND jsonb_typeof(r.result::jsonb->'contact_id')='string' "
            "AND (r.result->>'contact_id') ~* "
            "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' "
            "AND c.tenant_id=r.tenant_id AND c.id=(r.result->>'contact_id')::uuid"
        )
    )
    bind.execute(sa.text("ALTER TABLE public.wecom_callback_receipts ENABLE TRIGGER trg_guard_wecom_callback_receipts"))
    invalid = bind.execute(
        sa.text(
            "SELECT count(*) FROM public.wecom_callback_receipts r LEFT JOIN public.wecom_external_contacts c "
            "ON c.tenant_id=r.tenant_id AND c.id=r.contact_id "
            "WHERE r.contact_id IS NULL OR c.id IS NULL"
        )
    ).scalar_one()
    if invalid:
        raise RuntimeError(
            "cannot bind verified WeCom receipts without an authoritative same-tenant contact projection"
        )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    _backfill_and_validate()
    op.execute(
        f"ALTER TABLE public.wecom_external_contacts ADD CONSTRAINT {_CONTACT_KEY} UNIQUE USING INDEX {_CONTACT_KEY}"
    )
    op.execute(
        f"ALTER TABLE public.wecom_callback_receipts ADD CONSTRAINT {_NOT_NULL_CHECK} "
        "CHECK (contact_id IS NOT NULL) NOT VALID"
    )
    op.execute(f"ALTER TABLE public.wecom_callback_receipts VALIDATE CONSTRAINT {_NOT_NULL_CHECK}")
    op.execute("ALTER TABLE public.wecom_callback_receipts ALTER COLUMN contact_id SET NOT NULL")
    op.execute(f"ALTER TABLE public.wecom_callback_receipts DROP CONSTRAINT {_NOT_NULL_CHECK}")
    op.execute(
        f"ALTER TABLE public.wecom_callback_receipts ADD CONSTRAINT {_CONTACT_FK} "
        "FOREIGN KEY (tenant_id,contact_id) REFERENCES public.wecom_external_contacts(tenant_id,id) "
        "ON DELETE RESTRICT NOT VALID"
    )
    op.execute(f"ALTER TABLE public.wecom_callback_receipts VALIDATE CONSTRAINT {_CONTACT_FK}")
    op.create_table(
        "wecom_member_recovery_markers",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("observed_user_id", sa.String(120), nullable=True),
        sa.Column("acknowledged_user_id", sa.String(120), nullable=True),
        sa.Column("resolution_state", sa.String(30), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "contact_id", name="pk_wecom_member_recovery_markers"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            ["wecom_external_contacts.tenant_id", "wecom_external_contacts.id"],
            name="fk_wecom_member_recovery_marker_contact",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "resolution_state IN ('exact_bound','operator_acknowledged','unresolved')",
            name="ck_wecom_member_recovery_resolution",
        ),
    )
    op.execute("ALTER TABLE public.wecom_member_recovery_markers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.wecom_member_recovery_markers FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON public.wecom_member_recovery_markers "
        "USING (tenant_id=public.current_tenant_id()) WITH CHECK (tenant_id=public.current_tenant_id())"
    )
    op.execute("REVOKE ALL ON public.wecom_member_recovery_markers FROM PUBLIC")
    op.execute(
        "INSERT INTO public.wecom_member_recovery_markers "
        "(tenant_id,contact_id,observed_user_id,resolution_state,note) "
        "SELECT c.tenant_id,c.id,c.user_id,CASE WHEN c.user_id IS NOT NULL AND NULLIF(trim(c.user_id),'') IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM public.wecom_contact_ways w WHERE w.tenant_id=c.tenant_id "
        "AND w.connector_id=c.connector_id AND w.state=c.state AND w.status='active' "
        "AND jsonb_typeof(w.user_ids::jsonb)='array' AND w.user_ids::jsonb @> jsonb_build_array(c.user_id)) "
        "AND NOT EXISTS (SELECT 1 FROM public.wecom_callback_receipts r WHERE r.tenant_id=c.tenant_id "
        "AND r.contact_id=c.id AND r.user_id IS NOT NULL AND trim(r.user_id)<>trim(c.user_id)) "
        "THEN 'exact_bound' ELSE 'unresolved' END,"
        "'staged historical official WeCom projection member reconciliation; source projection preserved' "
        "FROM public.wecom_external_contacts c WHERE c.verification_source "
        "IN ('confirmed_callback','pending_callback','termination_callback')"
    )
    op.execute("ALTER TABLE public.wecom_callback_receipts DISABLE TRIGGER trg_guard_wecom_callback_receipts")
    op.execute(
        "UPDATE public.wecom_callback_receipts r SET user_id=c.user_id FROM public.wecom_external_contacts c "
        "JOIN public.wecom_member_recovery_markers m ON m.tenant_id=c.tenant_id AND m.contact_id=c.id "
        "WHERE r.tenant_id=c.tenant_id AND r.contact_id=c.id AND m.resolution_state='exact_bound' "
        "AND r.user_id IS DISTINCT FROM c.user_id"
    )
    op.execute("ALTER TABLE public.wecom_callback_receipts ENABLE TRIGGER trg_guard_wecom_callback_receipts")
    op.execute(r"""
      CREATE FUNCTION public.acknowledge_wecom_member_recovery(
        requested_tenant_id uuid,requested_contact_id uuid,requested_user_id text,requested_note text
      ) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fn$
      DECLARE target public.wecom_external_contacts%ROWTYPE;
      BEGIN
        IF session_user IN ('yimatong_app','yimatong_callback')
          OR NULLIF(trim(requested_user_id),'') IS NULL OR length(trim(requested_user_id))>120
          OR NULLIF(trim(requested_note),'') IS NULL OR length(requested_note)>1000 THEN
          RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='WeCom member recovery acknowledgement invalid';
        END IF;
        SELECT * INTO target FROM public.wecom_external_contacts c
          WHERE c.tenant_id=requested_tenant_id AND c.id=requested_contact_id FOR UPDATE;
        IF NOT FOUND OR NOT EXISTS (
          SELECT 1 FROM public.wecom_contact_ways w WHERE w.tenant_id=target.tenant_id
          AND w.connector_id=target.connector_id AND w.state=target.state AND w.status='active'
          AND jsonb_typeof(w.user_ids::jsonb)='array'
          AND w.user_ids::jsonb @> jsonb_build_array(trim(requested_user_id))
        ) THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='acknowledged WeCom member is not authorized'; END IF;
        IF NOT EXISTS (SELECT 1 FROM public.wecom_member_recovery_markers m
          WHERE m.tenant_id=requested_tenant_id AND m.contact_id=requested_contact_id
          AND m.resolution_state='unresolved' FOR UPDATE) THEN
          RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='WeCom member recovery marker is not unresolved'; END IF;
        UPDATE public.wecom_external_contacts SET user_id=trim(requested_user_id)
          WHERE tenant_id=requested_tenant_id AND id=requested_contact_id;
        ALTER TABLE public.wecom_callback_receipts DISABLE TRIGGER trg_guard_wecom_callback_receipts;
        UPDATE public.wecom_callback_receipts SET user_id=trim(requested_user_id)
          WHERE tenant_id=requested_tenant_id AND contact_id=requested_contact_id;
        ALTER TABLE public.wecom_callback_receipts ENABLE TRIGGER trg_guard_wecom_callback_receipts;
        UPDATE public.wecom_member_recovery_markers SET resolution_state='operator_acknowledged',
          acknowledged_user_id=trim(requested_user_id),note=requested_note,acknowledged_at=statement_timestamp()
          WHERE tenant_id=requested_tenant_id AND contact_id=requested_contact_id;
      EXCEPTION WHEN OTHERS THEN
        ALTER TABLE public.wecom_callback_receipts ENABLE TRIGGER trg_guard_wecom_callback_receipts;
        RAISE;
      END $fn$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.acknowledge_wecom_member_recovery(uuid,uuid,text,text) FROM PUBLIC")


def downgrade() -> None:
    _downstream_fact_preflight()
    _prepare_downgrade_index()
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("DROP FUNCTION public.acknowledge_wecom_member_recovery(uuid,uuid,text,text)")
    op.execute("DROP TABLE public.wecom_member_recovery_markers")
    op.execute(f"ALTER TABLE public.wecom_callback_receipts DROP CONSTRAINT {_CONTACT_FK}")
    op.execute("ALTER TABLE public.wecom_callback_receipts ALTER COLUMN contact_id DROP NOT NULL")
    op.execute(f"ALTER TABLE public.wecom_external_contacts DROP CONSTRAINT {_CONTACT_KEY}")
    op.execute(f"ALTER INDEX public.{_DOWNGRADE_INDEX} RENAME TO {_CONTACT_KEY}")
