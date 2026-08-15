"""register and backfill external order permissions

Revision ID: u8a3f4a5b6c7
Revises: u8a2e3f4a5b6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u8a3f4a5b6c7"
down_revision: str | Sequence[str] | None = "u8a2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MARKER = "external_order_permission_backfill"
_DOWNGRADE_BLOCKING_FACTS = """
SELECT
  (SELECT count(*) FROM external_order_ledger_recovery_markers)
  + (SELECT count(*) FROM external_order_value_events e
     WHERE e.provenance_type<>'backfill' OR e.actor_type<>'migration'
        OR e.actor_id IS NOT NULL OR e.provenance_verified
        OR e.event_type NOT IN ('order_confirmed','refund')
        OR e.sequence_no<>CASE e.event_type WHEN 'order_confirmed' THEN 1 ELSE 2 END
        OR NOT EXISTS (
          SELECT 1 FROM external_order_value_receipts r
          WHERE r.tenant_id=e.tenant_id AND r.id=e.receipt_id AND r.event_id=e.id
            AND r.order_id=e.order_id AND r.event_type=e.event_type
            AND r.source_system=e.source_system AND r.external_order_id=e.external_order_id
            AND e.provenance_digest=r.payload_digest
            AND r.idempotency_key=(CASE e.event_type
              WHEN 'order_confirmed' THEN 'backfill:confirmed:' ELSE 'backfill:refund:' END)||e.order_id::text))
  + (SELECT count(*) FROM external_order_value_receipts r
     WHERE NOT EXISTS (
       SELECT 1 FROM external_order_value_events e
       WHERE e.tenant_id=r.tenant_id AND e.id=r.event_id AND e.receipt_id=r.id
         AND e.order_id=r.order_id AND e.event_type=r.event_type
         AND e.provenance_type='backfill' AND e.actor_type='migration'
         AND e.actor_id IS NULL AND NOT e.provenance_verified
         AND e.provenance_digest=r.payload_digest
         AND r.idempotency_key=(CASE r.event_type
           WHEN 'order_confirmed' THEN 'backfill:confirmed:'
           WHEN 'refund' THEN 'backfill:refund:' ELSE '' END)||r.order_id::text))
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        _MARKER,
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.Column("permission_code", sa.String(100), nullable=False),
        sa.Column("created_permission", sa.Boolean(), nullable=False),
        sa.Column("created_grant", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "role_id", "permission_code", name="pk_external_order_permission_backfill"),
    )
    op.execute(f"REVOKE ALL ON public.{_MARKER} FROM PUBLIC")
    op.execute(
        r"""
        WITH tenant_codes AS (
          SELECT DISTINCT role.tenant_id,wanted.code
          FROM public.roles AS role
          CROSS JOIN LATERAL (VALUES ('order:read'),('order:manage')) wanted(code)
          WHERE role.name IN ('admin','operator')
        ), resolved_permissions AS (
          SELECT desired.tenant_id,desired.code,COALESCE(permission.id,gen_random_uuid()) AS permission_id,
                 permission.id IS NULL AS created_permission
          FROM tenant_codes desired
          LEFT JOIN public.permissions AS permission
            ON permission.tenant_id=desired.tenant_id AND permission.code=desired.code
        ), desired AS (
          SELECT role.tenant_id,role.id AS role_id,resolved.code,
                 resolved.permission_id,
                 resolved.created_permission,
                 role_permission.role_id IS NULL AS created_grant
          FROM public.roles AS role
          JOIN resolved_permissions resolved ON resolved.tenant_id=role.tenant_id
          LEFT JOIN public.role_permissions AS role_permission
            ON role_permission.tenant_id=role.tenant_id AND role_permission.role_id=role.id
           AND role_permission.permission_id=resolved.permission_id
          WHERE role.name IN ('admin','operator')
        )
        INSERT INTO public.external_order_permission_backfill
          (tenant_id,role_id,permission_id,permission_code,created_permission,created_grant)
        SELECT tenant_id,role_id,permission_id,code,created_permission,created_grant FROM desired
        ON CONFLICT DO NOTHING
        """
    )
    # Keep these as separate statements. The repository's endpoint-integrity
    # trigger must observe the newly inserted permission before accepting a
    # role_permissions row; sibling data-changing CTEs share one old snapshot.
    op.execute(
        "INSERT INTO public.permissions(id,tenant_id,code,description,created_at,updated_at) "
        "SELECT DISTINCT permission_id,tenant_id,permission_code,'外部订单账本权限',"
        "statement_timestamp(),statement_timestamp() FROM public.external_order_permission_backfill "
        "WHERE created_permission ON CONFLICT DO NOTHING"
    )
    op.execute(
        "INSERT INTO public.role_permissions(tenant_id,role_id,permission_id) "
        "SELECT tenant_id,role_id,permission_id FROM public.external_order_permission_backfill "
        "WHERE created_grant ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    blocking_facts = op.get_bind().execute(sa.text(_DOWNGRADE_BLOCKING_FACTS)).scalar_one()
    if blocking_facts:
        raise RuntimeError("u8a3 downgrade blocked: non-backfill ledger facts require durable permissions")
    external_uses = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM role_permissions rp JOIN external_order_permission_backfill marker "
            "ON marker.tenant_id=rp.tenant_id AND marker.permission_id=rp.permission_id "
            "WHERE marker.created_permission AND NOT EXISTS (SELECT 1 FROM external_order_permission_backfill own "
            "WHERE own.tenant_id=rp.tenant_id AND own.role_id=rp.role_id AND own.permission_id=rp.permission_id "
            "AND own.created_grant)"
        )
    ).scalar_one()
    if external_uses:
        raise RuntimeError("u8a3 downgrade blocked: migration-created order permissions have external grants")
    op.execute(
        "DELETE FROM role_permissions rp USING external_order_permission_backfill marker "
        "WHERE marker.created_grant AND rp.tenant_id=marker.tenant_id AND rp.role_id=marker.role_id "
        "AND rp.permission_id=marker.permission_id"
    )
    op.execute(
        "DELETE FROM permissions permission USING external_order_permission_backfill marker "
        "WHERE marker.created_permission AND permission.tenant_id=marker.tenant_id "
        "AND permission.id=marker.permission_id AND NOT EXISTS "
        "(SELECT 1 FROM role_permissions rp WHERE rp.tenant_id=permission.tenant_id AND rp.permission_id=permission.id)"
    )
    op.drop_table(_MARKER)
