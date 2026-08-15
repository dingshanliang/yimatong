"""backfill pilot fact authority

Revision ID: u9a1c2d3e4f5
Revises: u9a0b1c2d3e4
"""

from collections.abc import Sequence

from alembic import op

revision: str = "u9a1c2d3e4f5"
down_revision: str | Sequence[str] | None = "u9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute(
        """UPDATE public.pilot_milestones SET
        fact_digest=encode(digest(convert_to(jsonb_build_array(
          tenant_id,milestone_type,achieved_at,source,created_at)::text,'UTF8'),'sha256'),'hex'),
        authority_version=0
        WHERE fact_digest IS NULL OR authority_version IS NULL"""
    )
    op.execute(
        """UPDATE public.pilot_milestone_corrections SET
        request_id=id,actor_type='legacy',actor_principal=COALESCE(corrected_by::text,'legacy-system'),
        idempotency_key='legacy:'||id::text,
        payload_digest=encode(digest(convert_to(jsonb_build_array(
          tenant_id,milestone_id,milestone_type,corrected_at,source,reason,corrected_by,created_at
        )::text,'UTF8'),'sha256'),'hex'),authority_version=0
        WHERE authority_version IS NULL"""
    )
    op.execute(
        """INSERT INTO public.pilot_authority_receipts(
          id,tenant_id,operation,idempotency_key,payload_digest,resource_id,resource_version,
          actor_type,actor_account_id,result,created_at)
        SELECT request_id,tenant_id,'append_milestone_correction',idempotency_key,payload_digest,id,NULL,
          'legacy',corrected_by,json_build_object('correction_id',id,'replayed',false),created_at
        FROM public.pilot_milestone_corrections
        ON CONFLICT DO NOTHING"""
    )
    op.execute(
        """UPDATE public.retrospectives SET version=1,
        snapshot_digest=encode(digest(convert_to(scorecard_snapshot::jsonb::text,'UTF8'),'sha256'),'hex'),
        authority_version=0 WHERE version IS NULL OR snapshot_digest IS NULL OR authority_version IS NULL"""
    )


def downgrade() -> None:
    op.execute("DELETE FROM public.pilot_authority_receipts WHERE actor_type='legacy'")
    op.execute(
        "UPDATE public.retrospectives SET version=NULL,snapshot_digest=NULL,authority_version=NULL,"
        "completed_actor_tenant_id=NULL,completed_auth_session_id=NULL,"
        "completed_agency_authorization_id=NULL,completion_request_id=NULL"
    )
    op.execute(
        "UPDATE public.pilot_milestone_corrections SET request_id=NULL,actor_type=NULL,actor_principal=NULL,"
        "platform_auth_session_id=NULL,idempotency_key=NULL,payload_digest=NULL,authority_version=NULL"
    )
    op.execute("UPDATE public.pilot_milestones SET fact_digest=NULL,authority_version=NULL")
