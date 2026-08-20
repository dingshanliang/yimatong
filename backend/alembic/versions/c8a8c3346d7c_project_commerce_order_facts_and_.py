"""Project authoritative commerce order facts and repurchase attribution.

Revision ID: c8a8c3346d7c
Revises: c04d5e6f7a8b
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c8a8c3346d7c"
down_revision: Union[str, None] = "c04d5e6f7a8b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FACT_TABLES = (
    "commerce_product_mappings",
    "commerce_order_facts",
    "commerce_order_line_facts",
    "commerce_refund_facts",
    "commerce_repurchase_attributions",
)


def _execute_statements(sql: str) -> None:
    """Execute plain DDL individually because asyncpg rejects multi-command prepares."""

    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    op.execute("ALTER TABLE public.commerce_integration_messages ADD COLUMN projected_at timestamptz")
    _execute_statements(
        r"""
        CREATE TABLE public.commerce_product_mappings(
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL, connection_id uuid NOT NULL,
          external_product_ref varchar(160) NOT NULL, product_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT fk_commerce_product_mappings_connection FOREIGN KEY(tenant_id,connection_id)
            REFERENCES public.commerce_connections(tenant_id,id),
          CONSTRAINT fk_commerce_product_mappings_product FOREIGN KEY(tenant_id,product_id)
            REFERENCES public.products(tenant_id,id),
          CONSTRAINT uq_commerce_product_mappings_external UNIQUE(tenant_id,connection_id,external_product_ref),
          CONSTRAINT uq_commerce_product_mappings_tenant_id UNIQUE(tenant_id,id)
        );
        CREATE TABLE public.commerce_order_facts(
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL, connection_id uuid NOT NULL,
          external_order_ref varchar(160) NOT NULL, membership_id uuid, member_ref varchar(64),
          source_system varchar(40) NOT NULL, status varchar(30) NOT NULL, currency varchar(3) NOT NULL,
          order_original_amount_fen bigint NOT NULL, order_refunded_amount_fen bigint NOT NULL,
          order_net_amount_fen bigint NOT NULL, product_original_amount_fen bigint NOT NULL,
          product_refunded_amount_fen bigint NOT NULL, product_net_amount_fen bigint NOT NULL,
          coverage_status varchar(20) NOT NULL, paid_at timestamptz, fulfilled_at timestamptz,
          completed_at timestamptz, cancelled_at timestamptz, last_event_occurred_at timestamptz NOT NULL,
          last_event_version integer NOT NULL, last_message_row_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_commerce_order_facts_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_commerce_order_facts_external UNIQUE(tenant_id,connection_id,external_order_ref),
          CONSTRAINT fk_commerce_order_facts_connection FOREIGN KEY(tenant_id,connection_id)
            REFERENCES public.commerce_connections(tenant_id,id),
          CONSTRAINT fk_commerce_order_facts_membership FOREIGN KEY(tenant_id,membership_id)
            REFERENCES public.brand_memberships(tenant_id,id),
          CONSTRAINT fk_commerce_order_facts_message FOREIGN KEY(tenant_id,last_message_row_id)
            REFERENCES public.commerce_integration_messages(tenant_id,id),
          CONSTRAINT ck_commerce_order_facts_status CHECK(status IN
            ('placed','paid','fulfilled','completed','cancelled','partially_refunded','refunded')),
          CONSTRAINT ck_commerce_order_facts_currency CHECK(currency='CNY'),
          CONSTRAINT ck_commerce_order_facts_coverage CHECK(coverage_status IN ('complete','partial','missing')),
          CONSTRAINT ck_commerce_order_facts_order_amounts CHECK(order_original_amount_fen>=0
            AND order_refunded_amount_fen>=0 AND order_net_amount_fen=order_original_amount_fen-order_refunded_amount_fen
            AND order_net_amount_fen>=0),
          CONSTRAINT ck_commerce_order_facts_product_amounts CHECK(product_original_amount_fen>=0
            AND product_refunded_amount_fen>=0 AND product_net_amount_fen=product_original_amount_fen-product_refunded_amount_fen
            AND product_net_amount_fen>=0),
          CONSTRAINT ck_commerce_order_facts_member_pair CHECK((membership_id IS NULL AND member_ref IS NULL)
            OR (membership_id IS NOT NULL AND member_ref IS NOT NULL))
        );
        CREATE INDEX ix_commerce_order_facts_membership_paid
          ON public.commerce_order_facts(tenant_id,membership_id,paid_at);
        CREATE TABLE public.commerce_order_line_facts(
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL, order_fact_id uuid NOT NULL,
          external_line_ref varchar(160) NOT NULL, external_product_ref varchar(160) NOT NULL,
          sku_ref varchar(160), product_id uuid, quantity integer NOT NULL,
          original_amount_fen bigint NOT NULL, refunded_amount_fen bigint NOT NULL,
          net_amount_fen bigint NOT NULL, mapping_status varchar(20) NOT NULL,
          CONSTRAINT uq_commerce_order_lines_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_commerce_order_lines_external UNIQUE(tenant_id,order_fact_id,external_line_ref),
          CONSTRAINT fk_commerce_order_lines_order FOREIGN KEY(tenant_id,order_fact_id)
            REFERENCES public.commerce_order_facts(tenant_id,id) ON DELETE CASCADE,
          CONSTRAINT fk_commerce_order_lines_product FOREIGN KEY(tenant_id,product_id)
            REFERENCES public.products(tenant_id,id),
          CONSTRAINT ck_commerce_order_lines_quantity CHECK(quantity>0),
          CONSTRAINT ck_commerce_order_lines_amounts CHECK(original_amount_fen>=0 AND refunded_amount_fen>=0
            AND net_amount_fen=original_amount_fen-refunded_amount_fen AND net_amount_fen>=0),
          CONSTRAINT ck_commerce_order_lines_mapping CHECK(mapping_status IN ('mapped','unmapped')),
          CONSTRAINT ck_commerce_order_lines_mapping_pair CHECK((mapping_status='mapped' AND product_id IS NOT NULL)
            OR (mapping_status='unmapped' AND product_id IS NULL))
        );
        CREATE TABLE public.commerce_refund_facts(
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL, order_fact_id uuid NOT NULL,
          external_refund_ref varchar(160) NOT NULL, order_amount_fen bigint NOT NULL,
          product_amount_fen bigint NOT NULL, line_refunds jsonb NOT NULL, occurred_at timestamptz NOT NULL,
          message_row_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_commerce_refund_facts_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_commerce_refund_facts_external UNIQUE(tenant_id,order_fact_id,external_refund_ref),
          CONSTRAINT fk_commerce_refund_facts_order FOREIGN KEY(tenant_id,order_fact_id)
            REFERENCES public.commerce_order_facts(tenant_id,id) ON DELETE CASCADE,
          CONSTRAINT fk_commerce_refund_facts_message FOREIGN KEY(tenant_id,message_row_id)
            REFERENCES public.commerce_integration_messages(tenant_id,id),
          CONSTRAINT ck_commerce_refund_facts_amounts CHECK(order_amount_fen>=0 AND product_amount_fen>=0
            AND product_amount_fen<=order_amount_fen)
        );
        CREATE TABLE public.commerce_repurchase_attributions(
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL, order_fact_id uuid NOT NULL,
          membership_id uuid, scan_event_id uuid, public_id varchar(20), coupon_id uuid,
          is_member_order boolean NOT NULL, is_packaging_repurchase boolean NOT NULL,
          entry_attributed boolean NOT NULL, coupon_attributed boolean NOT NULL,
          occurrence_at timestamptz, member_cohort_at timestamptz, scan_at timestamptz,
          product_original_amount_fen bigint NOT NULL, product_refunded_amount_fen bigint NOT NULL,
          net_product_sales_fen bigint NOT NULL, coverage_status varchar(20) NOT NULL,
          trust_level varchar(20) NOT NULL, snapshot jsonb NOT NULL,
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT uq_commerce_repurchase_attr_tenant_id UNIQUE(tenant_id,id),
          CONSTRAINT uq_commerce_repurchase_attr_order UNIQUE(tenant_id,order_fact_id),
          CONSTRAINT fk_commerce_repurchase_attr_order FOREIGN KEY(tenant_id,order_fact_id)
            REFERENCES public.commerce_order_facts(tenant_id,id) ON DELETE CASCADE,
          CONSTRAINT fk_commerce_repurchase_attr_membership FOREIGN KEY(tenant_id,membership_id)
            REFERENCES public.brand_memberships(tenant_id,id),
          CONSTRAINT fk_commerce_repurchase_attr_coupon FOREIGN KEY(tenant_id,coupon_id)
            REFERENCES public.member_coupons(tenant_id,id),
          CONSTRAINT fk_commerce_repurchase_attr_public_id FOREIGN KEY(tenant_id,public_id)
            REFERENCES public.code_items(tenant_id,public_id),
          CONSTRAINT ck_commerce_repurchase_attr_coverage CHECK(coverage_status IN ('complete','partial','missing')),
          CONSTRAINT ck_commerce_repurchase_attr_trust CHECK(trust_level IN ('authoritative','partial','unattributed')),
          CONSTRAINT ck_commerce_repurchase_attr_amounts CHECK(net_product_sales_fen=
            product_original_amount_fen-product_refunded_amount_fen AND net_product_sales_fen>=0),
          CONSTRAINT ck_commerce_repurchase_attr_qualification CHECK(NOT is_packaging_repurchase
            OR (is_member_order AND entry_attributed AND net_product_sales_fen>0))
        );
        CREATE INDEX ix_commerce_repurchase_attr_occurrence
          ON public.commerce_repurchase_attributions(tenant_id,occurrence_at);
        CREATE INDEX ix_commerce_repurchase_attr_cohort
          ON public.commerce_repurchase_attributions(tenant_id,member_cohort_at);
        """
    )
    for table in FACT_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON public.{table} TO yimatong_app "
            "USING (tenant_id=public.current_tenant_id())"
        )
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC,yimatong_app,yimatong_callback")
        op.execute(f"GRANT SELECT ON public.{table} TO yimatong_app")

    op.execute(
        r"""
        CREATE FUNCTION public.create_commerce_product_mapping_authority(requested_tenant_id uuid,payload jsonb)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE mapping_id uuid; existing_product_id uuid;
        BEGIN
          IF session_user<>'yimatong_app' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce product mapping authority denied';
          END IF;
          IF NOT EXISTS(
            SELECT 1 FROM public.auth_sessions session
            JOIN public.accounts account ON account.tenant_id=session.tenant_id AND account.id=session.account_id
            JOIN public.tenants tenant ON tenant.id=session.tenant_id
            WHERE session.id=(payload->>'auth_session_id')::uuid AND session.tenant_id=requested_tenant_id
              AND session.account_id=(payload->>'actor_id')::uuid AND session.revoked_at IS NULL
              AND session.expires_at>statement_timestamp() AND session.auth_version=account.auth_version
              AND account.is_active AND tenant.status='active' AND EXISTS(
                SELECT 1 FROM public.account_roles account_role
                JOIN public.role_permissions role_permission ON role_permission.tenant_id=account_role.tenant_id
                  AND role_permission.role_id=account_role.role_id
                JOIN public.permissions permission ON permission.tenant_id=role_permission.tenant_id
                  AND permission.id=role_permission.permission_id
                WHERE account_role.tenant_id=requested_tenant_id AND account_role.account_id=account.id
                  AND permission.code='webhook:manage'
              )
          ) OR NOT EXISTS(SELECT 1 FROM public.commerce_connections WHERE tenant_id=requested_tenant_id
            AND id=(payload->>'connection_id')::uuid AND status='active')
             OR NOT EXISTS(SELECT 1 FROM public.products WHERE tenant_id=requested_tenant_id
            AND id=(payload->>'product_id')::uuid)
             OR NULLIF(payload->>'external_product_ref','') IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce product mapping scope invalid';
          END IF;
          SELECT id,product_id INTO mapping_id,existing_product_id FROM public.commerce_product_mappings
          WHERE tenant_id=requested_tenant_id AND connection_id=(payload->>'connection_id')::uuid
            AND external_product_ref=payload->>'external_product_ref' FOR UPDATE;
          IF FOUND THEN
            IF existing_product_id<>(payload->>'product_id')::uuid THEN
              RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='commerce product mapping conflict';
            END IF;
            RETURN mapping_id;
          END IF;
          mapping_id := (payload->>'mapping_id')::uuid;
          INSERT INTO public.commerce_product_mappings(id,tenant_id,connection_id,external_product_ref,product_id)
          VALUES(mapping_id,requested_tenant_id,(payload->>'connection_id')::uuid,
            payload->>'external_product_ref',(payload->>'product_id')::uuid);
          RETURN mapping_id;
        END $f$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.create_commerce_product_mapping_authority(uuid,jsonb) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.create_commerce_product_mapping_authority(uuid,jsonb) TO yimatong_app"
    )

    op.execute(
        r"""
        CREATE FUNCTION public.project_commerce_order_event_authority(requested_tenant_id uuid,message_row_id uuid)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $f$
        DECLARE
          message_row public.commerce_integration_messages%ROWTYPE;
          event_data jsonb; item jsonb; refund jsonb; existing_refund record;
          order_id uuid; membership_id_value uuid; coupon_id_value uuid; product_id_value uuid;
          scan_id_value uuid; scan_public_id text; scan_time_value timestamptz; cohort_value timestamptz;
          coverage_value text; trust_value text; is_newer boolean; member_order boolean; entry_value boolean;
          order_original bigint; order_refunded bigint; product_original bigint; product_refunded bigint;
          refund_order_total bigint; refund_product_total bigint; line_original_total bigint; line_refunded_total bigint;
        BEGIN
          IF session_user<>'yimatong_callback' OR public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce order projection authority denied';
          END IF;
          SELECT * INTO message_row FROM public.commerce_integration_messages
          WHERE tenant_id=requested_tenant_id AND id=message_row_id AND direction='inbox' AND status='accepted'
          FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='accepted commerce message required'; END IF;
          IF message_row.projected_at IS NOT NULL THEN
            SELECT id INTO order_id FROM public.commerce_order_facts WHERE tenant_id=requested_tenant_id
              AND connection_id=message_row.connection_id
              AND external_order_ref=message_row.payload->'data'->>'order_ref';
            RETURN order_id;
          END IF;
          IF message_row.message_type NOT IN ('commerce.order.placed','commerce.order.paid','commerce.order.fulfilled',
            'commerce.order.completed','commerce.order.cancelled','commerce.order.refunded') THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='unsupported commerce order event';
          END IF;
          event_data := message_row.payload->'data';
          IF event_data IS NULL OR event_data->>'source_system'<>'medusa_v2' OR event_data->>'currency'<>'CNY'
             OR NULLIF(event_data->>'order_ref','') IS NULL OR jsonb_typeof(event_data->'line_items')<>'array' THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid commerce order snapshot';
          END IF;
          IF (message_row.message_type='commerce.order.placed' AND event_data->>'status'<>'placed')
             OR (message_row.message_type='commerce.order.paid' AND event_data->>'status'<>'paid')
             OR (message_row.message_type='commerce.order.fulfilled' AND event_data->>'status'<>'fulfilled')
             OR (message_row.message_type='commerce.order.completed' AND event_data->>'status'<>'completed')
             OR (message_row.message_type='commerce.order.cancelled' AND event_data->>'status'<>'cancelled')
             OR (message_row.message_type='commerce.order.refunded'
                 AND event_data->>'status' NOT IN ('partially_refunded','refunded')) THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='commerce event type and status mismatch';
          END IF;
          order_original := (event_data->>'order_original_amount_fen')::bigint;
          order_refunded := (event_data->>'order_refunded_amount_fen')::bigint;
          product_original := (event_data->>'product_original_amount_fen')::bigint;
          product_refunded := (event_data->>'product_refunded_amount_fen')::bigint;
          IF order_original<0 OR order_refunded<0 OR order_refunded>order_original OR product_original<0
             OR product_refunded<0 OR product_refunded>product_original
             OR event_data->>'coverage_status' NOT IN ('complete','partial','missing') THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid commerce order amounts';
          END IF;
          SELECT COALESCE(sum((value->>'order_amount_fen')::bigint),0),
            COALESCE(sum((value->>'product_amount_fen')::bigint),0)
            INTO refund_order_total,refund_product_total
          FROM jsonb_array_elements(COALESCE(event_data->'refunds','[]'::jsonb));
          SELECT COALESCE(sum((value->>'original_amount_fen')::bigint),0),
            COALESCE(sum((value->>'refunded_amount_fen')::bigint),0)
            INTO line_original_total,line_refunded_total FROM jsonb_array_elements(event_data->'line_items');
          IF refund_order_total<>order_refunded OR refund_product_total<>product_refunded
             OR (event_data->>'coverage_status'='complete'
                 AND (line_original_total<>product_original OR line_refunded_total<>product_refunded)) THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='commerce order snapshot totals mismatch';
          END IF;
          IF NULLIF(message_row.payload->>'member_ref','') IS NOT NULL THEN
            SELECT ref.membership_id,membership.joined_at INTO membership_id_value,cohort_value
            FROM public.commerce_member_references ref JOIN public.brand_memberships membership
              ON membership.tenant_id=ref.tenant_id AND membership.id=ref.membership_id
            WHERE ref.tenant_id=requested_tenant_id AND ref.connection_id=message_row.connection_id
              AND ref.member_ref=message_row.payload->>'member_ref' AND membership.status='active';
            IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce member reference scope mismatch'; END IF;
          END IF;
          IF NULLIF(event_data->>'coupon_ref','') IS NOT NULL THEN
            coupon_id_value := (event_data->>'coupon_ref')::uuid;
            IF membership_id_value IS NULL OR NOT EXISTS(SELECT 1 FROM public.member_coupons
              WHERE tenant_id=requested_tenant_id AND id=coupon_id_value AND membership_id=membership_id_value
                AND status='used' AND used_order_ref=event_data->>'order_ref') THEN
              RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='commerce coupon attribution scope mismatch';
            END IF;
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(requested_tenant_id::text||':'||message_row.connection_id::text
            ||':'||(event_data->>'order_ref'),0));
          SELECT id,(message_row.occurred_at>last_event_occurred_at OR
            (message_row.occurred_at=last_event_occurred_at AND message_row.message_version>last_event_version))
            INTO order_id,is_newer FROM public.commerce_order_facts WHERE tenant_id=requested_tenant_id
            AND connection_id=message_row.connection_id AND external_order_ref=event_data->>'order_ref' FOR UPDATE;
          IF NOT FOUND THEN
            order_id := (md5(requested_tenant_id::text||message_row.connection_id::text||(event_data->>'order_ref')))::uuid;
            is_newer := true;
            INSERT INTO public.commerce_order_facts(id,tenant_id,connection_id,external_order_ref,membership_id,member_ref,
              source_system,status,currency,order_original_amount_fen,order_refunded_amount_fen,order_net_amount_fen,
              product_original_amount_fen,product_refunded_amount_fen,product_net_amount_fen,coverage_status,
              paid_at,fulfilled_at,completed_at,cancelled_at,last_event_occurred_at,last_event_version,last_message_row_id)
            VALUES(order_id,requested_tenant_id,message_row.connection_id,event_data->>'order_ref',membership_id_value,
              message_row.payload->>'member_ref',event_data->>'source_system',event_data->>'status',event_data->>'currency',
              order_original,order_refunded,order_original-order_refunded,product_original,product_refunded,
              product_original-product_refunded,event_data->>'coverage_status',NULLIF(event_data->>'paid_at','')::timestamptz,
              NULLIF(event_data->>'fulfilled_at','')::timestamptz,NULLIF(event_data->>'completed_at','')::timestamptz,
              NULLIF(event_data->>'cancelled_at','')::timestamptz,message_row.occurred_at,message_row.message_version,message_row.id);
          ELSIF is_newer THEN
            UPDATE public.commerce_order_facts SET membership_id=membership_id_value,
              member_ref=CASE WHEN membership_id_value IS NULL THEN NULL ELSE message_row.payload->>'member_ref' END,
              status=event_data->>'status',order_original_amount_fen=order_original,
              order_refunded_amount_fen=order_refunded,order_net_amount_fen=order_original-order_refunded,
              product_original_amount_fen=product_original,product_refunded_amount_fen=product_refunded,
              product_net_amount_fen=product_original-product_refunded,coverage_status=event_data->>'coverage_status',
              paid_at=NULLIF(event_data->>'paid_at','')::timestamptz,
              fulfilled_at=NULLIF(event_data->>'fulfilled_at','')::timestamptz,
              completed_at=NULLIF(event_data->>'completed_at','')::timestamptz,
              cancelled_at=NULLIF(event_data->>'cancelled_at','')::timestamptz,
              last_event_occurred_at=message_row.occurred_at,last_event_version=message_row.message_version,
              last_message_row_id=message_row.id,updated_at=statement_timestamp()
            WHERE tenant_id=requested_tenant_id AND id=order_id;
          END IF;
          FOR refund IN SELECT value FROM jsonb_array_elements(COALESCE(event_data->'refunds','[]'::jsonb)) LOOP
            SELECT order_amount_fen,product_amount_fen,line_refunds,occurred_at INTO existing_refund
            FROM public.commerce_refund_facts WHERE tenant_id=requested_tenant_id AND order_fact_id=order_id
              AND external_refund_ref=refund->>'refund_ref';
            IF FOUND THEN
              IF existing_refund.order_amount_fen<>(refund->>'order_amount_fen')::bigint
                 OR existing_refund.product_amount_fen<>(refund->>'product_amount_fen')::bigint
                 OR existing_refund.line_refunds<>COALESCE(refund->'line_refunds','[]'::jsonb)
                 OR existing_refund.occurred_at<>(refund->>'occurred_at')::timestamptz THEN
                RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='commerce refund fact conflict';
              END IF;
            ELSE
              INSERT INTO public.commerce_refund_facts(id,tenant_id,order_fact_id,external_refund_ref,
                order_amount_fen,product_amount_fen,line_refunds,occurred_at,message_row_id)
              VALUES((md5(requested_tenant_id::text||order_id::text||(refund->>'refund_ref')))::uuid,
                requested_tenant_id,order_id,refund->>'refund_ref',(refund->>'order_amount_fen')::bigint,
                (refund->>'product_amount_fen')::bigint,COALESCE(refund->'line_refunds','[]'::jsonb),
                (refund->>'occurred_at')::timestamptz,message_row.id);
            END IF;
          END LOOP;
          IF is_newer THEN
            DELETE FROM public.commerce_order_line_facts WHERE tenant_id=requested_tenant_id AND order_fact_id=order_id;
            coverage_value := event_data->>'coverage_status';
            FOR item IN SELECT value FROM jsonb_array_elements(event_data->'line_items') LOOP
              IF (item->>'quantity')::integer<=0 OR (item->>'original_amount_fen')::bigint<0
                 OR (item->>'refunded_amount_fen')::bigint<0
                 OR (item->>'refunded_amount_fen')::bigint>(item->>'original_amount_fen')::bigint THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid commerce line snapshot';
              END IF;
              SELECT product_id INTO product_id_value FROM public.commerce_product_mappings
              WHERE tenant_id=requested_tenant_id AND connection_id=message_row.connection_id
                AND external_product_ref=item->>'product_ref';
              IF product_id_value IS NULL AND coverage_value='complete' THEN coverage_value := 'partial'; END IF;
              INSERT INTO public.commerce_order_line_facts(id,tenant_id,order_fact_id,external_line_ref,
                external_product_ref,sku_ref,product_id,quantity,original_amount_fen,refunded_amount_fen,net_amount_fen,mapping_status)
              VALUES((md5(requested_tenant_id::text||order_id::text||(item->>'line_ref')))::uuid,requested_tenant_id,
                order_id,item->>'line_ref',item->>'product_ref',item->>'sku_ref',product_id_value,(item->>'quantity')::integer,
                (item->>'original_amount_fen')::bigint,(item->>'refunded_amount_fen')::bigint,
                (item->>'original_amount_fen')::bigint-(item->>'refunded_amount_fen')::bigint,
                CASE WHEN product_id_value IS NULL THEN 'unmapped' ELSE 'mapped' END);
            END LOOP;
            UPDATE public.commerce_order_facts SET coverage_status=coverage_value WHERE tenant_id=requested_tenant_id AND id=order_id;
            SELECT scan.id,scan.public_id,scan.scan_time INTO scan_id_value,scan_public_id,scan_time_value
            FROM public.brand_membership_profile_links link
            JOIN public.anonymous_visitors visitor ON visitor.tenant_id=link.tenant_id
              AND visitor.consumer_id=link.consumer_profile_id
            JOIN public.scan_events scan ON scan.tenant_id=visitor.tenant_id AND scan.visitor_id=visitor.visitor_id
            WHERE link.tenant_id=requested_tenant_id AND link.membership_id=membership_id_value AND scan.is_valid_visit
              AND scan.scan_time<=NULLIF(event_data->>'paid_at','')::timestamptz
              AND scan.scan_time>=NULLIF(event_data->>'paid_at','')::timestamptz-interval '30 days'
            ORDER BY scan.scan_time DESC LIMIT 1;
            member_order := membership_id_value IS NOT NULL AND NULLIF(event_data->>'paid_at','') IS NOT NULL
              AND event_data->>'status'<>'cancelled' AND product_original-product_refunded>0;
            entry_value := scan_id_value IS NOT NULL;
            trust_value := CASE WHEN member_order AND entry_value AND coverage_value='complete' THEN 'authoritative'
              WHEN membership_id_value IS NOT NULL OR entry_value THEN 'partial' ELSE 'unattributed' END;
            INSERT INTO public.commerce_repurchase_attributions(id,tenant_id,order_fact_id,membership_id,scan_event_id,
              public_id,coupon_id,is_member_order,is_packaging_repurchase,entry_attributed,coupon_attributed,occurrence_at,
              member_cohort_at,scan_at,product_original_amount_fen,product_refunded_amount_fen,net_product_sales_fen,
              coverage_status,trust_level,snapshot)
            VALUES((md5(requested_tenant_id::text||order_id::text||'attribution'))::uuid,requested_tenant_id,order_id,
              membership_id_value,scan_id_value,scan_public_id,coupon_id_value,member_order,
              member_order AND entry_value AND coverage_value='complete',entry_value,coupon_id_value IS NOT NULL,
              NULLIF(event_data->>'paid_at','')::timestamptz,cohort_value,scan_time_value,product_original,product_refunded,
              product_original-product_refunded,coverage_value,trust_value,
              jsonb_build_object('message_id',message_row.message_id,'message_version',message_row.message_version,
                'source_system',event_data->>'source_system','order_ref',event_data->>'order_ref'))
            ON CONFLICT(tenant_id,order_fact_id) DO UPDATE SET membership_id=EXCLUDED.membership_id,
              scan_event_id=EXCLUDED.scan_event_id,public_id=EXCLUDED.public_id,coupon_id=EXCLUDED.coupon_id,
              is_member_order=EXCLUDED.is_member_order,is_packaging_repurchase=EXCLUDED.is_packaging_repurchase,
              entry_attributed=EXCLUDED.entry_attributed,coupon_attributed=EXCLUDED.coupon_attributed,
              occurrence_at=EXCLUDED.occurrence_at,member_cohort_at=EXCLUDED.member_cohort_at,scan_at=EXCLUDED.scan_at,
              product_original_amount_fen=EXCLUDED.product_original_amount_fen,
              product_refunded_amount_fen=EXCLUDED.product_refunded_amount_fen,
              net_product_sales_fen=EXCLUDED.net_product_sales_fen,coverage_status=EXCLUDED.coverage_status,
              trust_level=EXCLUDED.trust_level,snapshot=EXCLUDED.snapshot,updated_at=statement_timestamp();
          END IF;
          UPDATE public.commerce_integration_messages SET projected_at=statement_timestamp()
          WHERE tenant_id=requested_tenant_id AND id=message_row.id;
          RETURN order_id;
        END $f$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.project_commerce_order_event_authority(uuid,uuid) FROM PUBLIC,yimatong_app")
    op.execute("GRANT EXECUTE ON FUNCTION public.project_commerce_order_event_authority(uuid,uuid) TO yimatong_callback")


def downgrade() -> None:
    op.execute(
        r"""
        DO $f$ BEGIN
          IF EXISTS(SELECT 1 FROM public.commerce_order_facts LIMIT 1)
             OR EXISTS(SELECT 1 FROM public.commerce_refund_facts LIMIT 1)
             OR EXISTS(SELECT 1 FROM public.commerce_product_mappings LIMIT 1) THEN
            RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='commerce order facts exist; archive before downgrade';
          END IF;
        END $f$;
        """
    )
    op.execute("DROP FUNCTION public.project_commerce_order_event_authority(uuid,uuid)")
    op.execute("DROP FUNCTION public.create_commerce_product_mapping_authority(uuid,jsonb)")
    for table in reversed(FACT_TABLES):
        op.execute(f"DROP TABLE public.{table}")
    op.execute("ALTER TABLE public.commerce_integration_messages DROP COLUMN projected_at")
