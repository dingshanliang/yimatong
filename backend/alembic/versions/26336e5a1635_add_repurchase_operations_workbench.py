"""Add repurchase operations workbench authority.

Revision ID: 26336e5a1635
Revises: c36405c62488
"""

from typing import Sequence, Union

from alembic import op

revision: str = "26336e5a1635"
down_revision: Union[str, None] = "c36405c62488"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("repurchase_work_items", "repurchase_work_item_events")


def _plain(sql: str) -> None:
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _plain(
        r"""
        CREATE TABLE public.repurchase_work_items(
          id uuid PRIMARY KEY,tenant_id uuid NOT NULL,category varchar(40) NOT NULL,business_ref varchar(160) NOT NULL,
          title varchar(160) NOT NULL,impact_summary varchar(500) NOT NULL,priority varchar(20) NOT NULL,
          status varchar(30) NOT NULL DEFAULT 'pending',owner_account_id uuid NOT NULL,due_at timestamptz NOT NULL,
          conclusion varchar(1000),evidence jsonb NOT NULL DEFAULT '{}'::jsonb,resolved_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_repurchase_work_items_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_repurchase_work_items_business UNIQUE(tenant_id,category,business_ref),
          CONSTRAINT fk_repurchase_work_items_tenant FOREIGN KEY(tenant_id) REFERENCES public.tenants(id),
          CONSTRAINT fk_repurchase_work_items_owner FOREIGN KEY(tenant_id,owner_account_id) REFERENCES public.accounts(tenant_id,id),
          CONSTRAINT ck_repurchase_work_items_category CHECK(category IN ('coupon_issue_failure','coupon_expiry_unreached',
            'notification_failure','payment_redemption_conflict','refund_unsynced','membership_mapping_conflict',
            'unattributed_order','source_coverage')),
          CONSTRAINT ck_repurchase_work_items_priority CHECK(priority IN ('urgent','high','normal')),
          CONSTRAINT ck_repurchase_work_items_status CHECK(status IN ('pending','in_progress','waiting_external','resolved','no_action')),
          CONSTRAINT ck_repurchase_work_items_resolution CHECK((status IN ('resolved','no_action') AND resolved_at IS NOT NULL
            AND conclusion IS NOT NULL) OR (status NOT IN ('resolved','no_action') AND resolved_at IS NULL))
        );
        CREATE INDEX ix_repurchase_work_items_queue ON public.repurchase_work_items(tenant_id,status,priority,due_at);
        CREATE TABLE public.repurchase_work_item_events(
          id uuid PRIMARY KEY,tenant_id uuid NOT NULL,work_item_id uuid NOT NULL,action varchar(40) NOT NULL,
          from_status varchar(30),to_status varchar(30),before_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
          after_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,reason varchar(1000) NOT NULL,evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
          actor_account_id uuid NOT NULL,occurred_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_repurchase_work_item_events_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT fk_repurchase_work_item_events_item FOREIGN KEY(tenant_id,work_item_id)
            REFERENCES public.repurchase_work_items(tenant_id,id),
          CONSTRAINT fk_repurchase_work_item_events_actor FOREIGN KEY(tenant_id,actor_account_id)
            REFERENCES public.accounts(tenant_id,id),
          CONSTRAINT ck_repurchase_work_item_events_action CHECK(action IN ('created','reopened','transitioned','reassigned',
            'deadline_changed','resync_requested','correction_submitted'))
        );
        CREATE INDEX ix_repurchase_work_item_events_item_time
          ON public.repurchase_work_item_events(tenant_id,work_item_id,occurred_at);
        """
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON public.{table} TO yimatong_app "
            "USING(tenant_id=public.current_tenant_id())"
        )
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC,yimatong_app,yimatong_callback")
        op.execute(f"GRANT SELECT ON public.{table} TO yimatong_app")

    op.execute(
        r"""
        CREATE FUNCTION public.mutate_repurchase_work_item_authority(requested_tenant_id uuid,payload jsonb)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE action_name text:=payload->>'action'; item public.repurchase_work_items%ROWTYPE;
          resolved_actor uuid; requested_session uuid:=NULLIF(payload->>'auth_session_id','')::uuid;
          target_id uuid:=NULLIF(payload->>'work_item_id','')::uuid; next_status text; event_action text;
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id
             OR requested_session IS NULL THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='repurchase workbench authority denied'; END IF;
          PERFORM pg_advisory_xact_lock_shared(hashtextextended('auth-session:'||requested_session::text,0));
          SELECT account.id INTO resolved_actor FROM public.auth_sessions auth_session
          JOIN public.accounts account ON account.tenant_id=auth_session.tenant_id AND account.id=auth_session.account_id
          JOIN public.tenants tenant ON tenant.id=auth_session.tenant_id
          WHERE auth_session.id=requested_session AND auth_session.tenant_id=requested_tenant_id
            AND auth_session.revoked_at IS NULL AND auth_session.expires_at>statement_timestamp()
            AND auth_session.auth_version=account.auth_version AND account.is_active AND tenant.status='active'
            AND EXISTS(SELECT 1 FROM public.account_roles account_role
              JOIN public.role_permissions role_permission ON role_permission.tenant_id=account_role.tenant_id
                AND role_permission.role_id=account_role.role_id
              JOIN public.permissions permission ON permission.tenant_id=role_permission.tenant_id
                AND permission.id=role_permission.permission_id
              WHERE account_role.tenant_id=requested_tenant_id AND account_role.account_id=account.id
                AND permission.code='campaign:write');
          IF resolved_actor IS NULL OR resolved_actor IS DISTINCT FROM NULLIF(payload->>'actor_account_id','')::uuid THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='repurchase operator session or permission denied'; END IF;
          IF action_name='create' THEN
            SELECT * INTO item FROM public.repurchase_work_items WHERE tenant_id=requested_tenant_id
              AND category=payload->>'category' AND business_ref=payload->>'business_ref' FOR UPDATE;
            IF FOUND THEN
              IF item.status IN ('resolved','no_action') THEN
                UPDATE public.repurchase_work_items SET status='pending',priority=payload->>'priority',
                  owner_account_id=(payload->>'owner_account_id')::uuid,due_at=(payload->>'due_at')::timestamptz,
                  conclusion=NULL,resolved_at=NULL,evidence=COALESCE(payload->'evidence','{}'::jsonb),updated_at=statement_timestamp()
                WHERE tenant_id=requested_tenant_id AND id=item.id RETURNING * INTO item; event_action:='reopened';
              ELSE RETURN item.id; END IF;
            ELSE
              target_id:=(payload->>'work_item_id')::uuid;
              INSERT INTO public.repurchase_work_items(id,tenant_id,category,business_ref,title,impact_summary,priority,
                status,owner_account_id,due_at,evidence) VALUES(target_id,requested_tenant_id,payload->>'category',
                payload->>'business_ref',payload->>'title',payload->>'impact_summary',payload->>'priority','pending',
                (payload->>'owner_account_id')::uuid,(payload->>'due_at')::timestamptz,COALESCE(payload->'evidence','{}'::jsonb))
              RETURNING * INTO item; event_action:='created';
            END IF;
          ELSE
            SELECT * INTO item FROM public.repurchase_work_items WHERE tenant_id=requested_tenant_id AND id=target_id FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23503',MESSAGE='repurchase work item not found'; END IF;
            IF action_name='transition' THEN
              next_status:=payload->>'status';
              IF NOT ((item.status='pending' AND next_status IN ('in_progress','waiting_external','resolved','no_action'))
                OR (item.status='in_progress' AND next_status IN ('waiting_external','resolved','no_action'))
                OR (item.status='waiting_external' AND next_status IN ('in_progress','resolved','no_action'))
                OR (item.status IN ('resolved','no_action') AND next_status='in_progress')) THEN
                RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='invalid repurchase work item transition'; END IF;
              UPDATE public.repurchase_work_items SET status=next_status,conclusion=NULLIF(payload->>'conclusion',''),
                evidence=evidence||COALESCE(payload->'evidence','{}'::jsonb),
                resolved_at=CASE WHEN next_status IN ('resolved','no_action') THEN statement_timestamp() ELSE NULL END,
                updated_at=statement_timestamp() WHERE tenant_id=requested_tenant_id AND id=item.id; event_action:='transitioned';
            ELSIF action_name='reassign' THEN
              UPDATE public.repurchase_work_items SET owner_account_id=(payload->>'owner_account_id')::uuid,
                due_at=(payload->>'due_at')::timestamptz,updated_at=statement_timestamp()
              WHERE tenant_id=requested_tenant_id AND id=item.id; event_action:='reassigned';
            ELSIF action_name IN ('resync','correction') THEN
              event_action:=CASE action_name WHEN 'resync' THEN 'resync_requested' ELSE 'correction_submitted' END;
            ELSE RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid repurchase workbench action'; END IF;
          END IF;
          INSERT INTO public.repurchase_work_item_events(id,tenant_id,work_item_id,action,from_status,to_status,
            before_snapshot,after_snapshot,reason,evidence,actor_account_id)
          VALUES((payload->>'event_id')::uuid,requested_tenant_id,item.id,event_action,item.status,
            CASE WHEN action_name='transition' THEN next_status ELSE item.status END,
            COALESCE(payload->'before','{}'::jsonb),COALESCE(payload->'after','{}'::jsonb),payload->>'reason',
            COALESCE(payload->'evidence','{}'::jsonb),resolved_actor);
          RETURN item.id;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.mutate_repurchase_work_item_authority(uuid,jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.mutate_repurchase_work_item_authority(uuid,jsonb) TO yimatong_app")


def downgrade() -> None:
    op.execute(
        r"""DO $f$ BEGIN IF EXISTS(SELECT 1 FROM public.repurchase_work_item_events LIMIT 1)
        THEN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='repurchase workbench facts exist; archive before downgrade'; END IF; END $f$;"""
    )
    op.execute("DROP FUNCTION public.mutate_repurchase_work_item_authority(uuid,jsonb)")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE public.{table}")
