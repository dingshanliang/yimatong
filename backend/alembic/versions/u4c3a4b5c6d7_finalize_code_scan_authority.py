"""Finalize function-only, referentially valid public scan facts.

Revision ID: u4c3a4b5c6d7
Revises: u4b2f3a4b5c6
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u4c3a4b5c6d7"
down_revision: str | None = "u4b2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION = "record_public_code_scan(uuid,text,uuid,text,text,text,text)"
_OLD_FUNCTION = "mark_code_item_first_scanned(uuid,text)"


def _scan_partitions() -> tuple[str, ...]:
    rows = op.get_bind().execute(
        sa.text(
            "SELECT child.relname FROM pg_inherits inheritance "
            "JOIN pg_class parent ON parent.oid=inheritance.inhparent "
            "JOIN pg_namespace namespace ON namespace.oid=parent.relnamespace "
            "JOIN pg_class child ON child.oid=inheritance.inhrelid "
            "WHERE namespace.nspname='public' AND parent.relname='scan_events' "
            "ORDER BY child.relname"
        )
    )
    return tuple(str(row[0]) for row in rows)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    for partition in _scan_partitions():
        op.execute(
            f"ALTER TABLE public.{partition} VALIDATE CONSTRAINT fk_scan_events_tenant_public_id"
        )
    op.execute(
        "ALTER TABLE public.scan_events ADD CONSTRAINT fk_scan_events_tenant_public_id "
        "FOREIGN KEY (tenant_id, public_id) "
        "REFERENCES public.code_items (tenant_id, public_id)"
    )
    op.execute(
        r"""
        CREATE FUNCTION public.record_public_code_scan(
            requested_tenant_id uuid,
            requested_public_id text,
            requested_event_id uuid,
            requested_ip_hash text,
            requested_user_agent text,
            requested_environment text,
            requested_visitor_id text
        ) RETURNS TABLE (
            scan_event_id uuid,
            first_scan boolean,
            first_scanned_at timestamptz,
            scan_time timestamptz,
            is_valid_visit boolean
        )
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            current_scope uuid;
            item_probe record;
            tenant_row record;
            production_batch_row record;
            code_batch_row record;
            item_row record;
            now_at timestamptz := CURRENT_TIMESTAMP;
            first_scan_value boolean;
            first_scanned_at_value timestamptz;
            valid_visit_value boolean;
            normalized_user_agent text := left(COALESCE(requested_user_agent, ''), 500);
        BEGIN
            current_scope := public.current_tenant_id();
            IF current_scope IS DISTINCT FROM requested_tenant_id THEN
                RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='scan tenant context mismatch';
            END IF;
            IF requested_event_id IS NULL
               OR requested_public_id IS NULL
               OR length(btrim(requested_public_id)) NOT BETWEEN 1 AND 20
               OR requested_environment NOT IN ('browser','wechat','alipay')
               OR (requested_ip_hash IS NOT NULL AND (
                    length(requested_ip_hash) <> 64
                    OR requested_ip_hash <> lower(requested_ip_hash)
                    OR translate(requested_ip_hash, '0123456789abcdef', '') <> ''
               ))
               OR (requested_visitor_id IS NOT NULL AND length(requested_visitor_id) NOT BETWEEN 1 AND 64)
            THEN
                RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='scan evidence parameters are invalid';
            END IF;

            SELECT item.id AS item_id, item.code_batch_id, batch.production_batch_id,
                   batch.product_id, batch.sku_id
            INTO item_probe
            FROM public.code_items AS item
            JOIN public.code_batches AS batch
              ON batch.tenant_id=item.tenant_id AND batch.id=item.code_batch_id
            WHERE item.tenant_id=requested_tenant_id AND item.public_id=requested_public_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='scan code is unavailable';
            END IF;

            SELECT tenant.id INTO tenant_row
            FROM public.tenants AS tenant
            WHERE tenant.id=requested_tenant_id AND tenant.status='active'
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='scan tenant is unavailable';
            END IF;

            SELECT batch.id, batch.status, batch.expiry_date INTO production_batch_row
            FROM public.production_batches AS batch
            WHERE batch.tenant_id=requested_tenant_id
              AND batch.product_id=item_probe.product_id
              AND batch.sku_id=item_probe.sku_id
              AND batch.id=item_probe.production_batch_id
            FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='scan production batch is unavailable';
            END IF;

            SELECT batch.id, batch.status INTO code_batch_row
            FROM public.code_batches AS batch
            WHERE batch.tenant_id=requested_tenant_id
              AND batch.product_id=item_probe.product_id
              AND batch.sku_id=item_probe.sku_id
              AND batch.production_batch_id=item_probe.production_batch_id
              AND batch.id=item_probe.code_batch_id;
            IF NOT FOUND OR code_batch_row.status <> 'activated' THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='scan code batch is unavailable';
            END IF;

            SELECT item.id, item.status, item.first_scanned_at INTO item_row
            FROM public.code_items AS item
            WHERE item.tenant_id=requested_tenant_id
              AND item.code_batch_id=item_probe.code_batch_id
              AND item.id=item_probe.item_id
              AND item.public_id=requested_public_id
            FOR UPDATE;
            IF NOT FOUND OR item_row.status NOT IN ('activated','bound','frozen') THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='scan code is unavailable';
            END IF;

            IF requested_visitor_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM public.anonymous_visitors AS visitor
                WHERE visitor.tenant_id=requested_tenant_id
                  AND visitor.visitor_id=requested_visitor_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='scan visitor is unavailable';
            END IF;

            first_scan_value := item_row.first_scanned_at IS NULL;
            first_scanned_at_value := COALESCE(item_row.first_scanned_at, now_at);
            IF first_scan_value THEN
                UPDATE public.code_items
                SET first_scanned_at=now_at, updated_at=now_at
                WHERE tenant_id=requested_tenant_id AND id=item_row.id;
            END IF;

            valid_visit_value := production_batch_row.status='active'
                AND production_batch_row.expiry_date >= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')::date
                AND lower(normalized_user_agent) NOT LIKE '%bot%'
                AND lower(normalized_user_agent) NOT LIKE '%crawler%'
                AND lower(normalized_user_agent) NOT LIKE '%spider%'
                AND lower(normalized_user_agent) NOT LIKE '%curl%'
                AND lower(normalized_user_agent) NOT LIKE '%wget%'
                AND lower(normalized_user_agent) NOT LIKE '%python-requests%'
                AND lower(normalized_user_agent) NOT LIKE '%scrapy%';

            INSERT INTO public.scan_events (
                id, tenant_id, public_id, scan_time, ip_hash, user_agent,
                is_first_scan, environment, is_valid_visit, visitor_id,
                created_at, updated_at
            ) VALUES (
                requested_event_id, requested_tenant_id, requested_public_id, now_at,
                requested_ip_hash, NULLIF(normalized_user_agent, ''), first_scan_value,
                requested_environment, valid_visit_value, requested_visitor_id, now_at, now_at
            );

            RETURN QUERY SELECT requested_event_id, first_scan_value,
                first_scanned_at_value, now_at, valid_visit_value;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON FUNCTION public.{_OLD_FUNCTION} FROM PUBLIC")
    op.execute(
        "REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
        "ON TABLE public.scan_events FROM yimatong_app"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION} TO yimatong_app")
    op.execute(f"REVOKE EXECUTE ON FUNCTION public.{_OLD_FUNCTION} FROM yimatong_app")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION} FROM PUBLIC, yimatong_app")
    op.execute("DROP FUNCTION public.record_public_code_scan(uuid,text,uuid,text,text,text,text)")
    op.execute("GRANT INSERT, UPDATE, DELETE ON TABLE public.scan_events TO yimatong_app")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_OLD_FUNCTION} TO yimatong_app")
    op.execute(
        "ALTER TABLE public.scan_events DROP CONSTRAINT fk_scan_events_tenant_public_id"
    )
