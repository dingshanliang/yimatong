"""Drop sample_code from the launch-readiness digest manifest.

Revision ID: b7e1f2a3c4d5
Revises: a34cfb8027f0
Create Date: 2026-09-20

消费者首扫/领取后，样本码（compute_launch_readiness 每次按
"当前可用码"重选，且其 status 会在 activated→bound 间迁移）会改变
manifest 内容，导致已上线 release 在 refresh 时被误判
invalidated（"上线版本已变化"）。样本身份与状态是运行时抽样，
不属于品牌方确认的内容，从 digest 输入中整体剔除；
readiness_code_item_id 与快照展示不受影响。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7e1f2a3c4d5"
down_revision: str | Sequence[str] | None = "a34cfb8027f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 基于现行定义（u6b5c6d7e8f9 引入）仅移除 manifest 的 'sample_code' 键。
_FUNCTION = """
CREATE OR REPLACE FUNCTION public.compute_launch_readiness(requested_tenant_id uuid, requested_page_version_id uuid, requested_campaign_id uuid, requested_code_batch_id uuid)
 RETURNS TABLE(manifest jsonb, content_digest text, ready boolean, page_template_id uuid, readiness_code_item_id uuid)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public'
AS $function$
DECLARE cb record; pb record; page_row record; campaign_row record;
DECLARE product_row record; brand_row record; tenant_row record;
DECLARE benefit_facts jsonb; asset_facts jsonb; takeover_facts jsonb; sample record;
BEGIN
    SELECT production_batch_id INTO cb FROM public.code_batches
    WHERE tenant_id=requested_tenant_id AND id=requested_code_batch_id;
    IF cb.production_batch_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch code batch is unavailable';
    END IF;
    SELECT * INTO pb FROM public.production_batches
    WHERE tenant_id=requested_tenant_id AND id=cb.production_batch_id FOR SHARE NOWAIT;
    SELECT * INTO cb FROM public.code_batches
    WHERE tenant_id=requested_tenant_id AND id=requested_code_batch_id FOR SHARE NOWAIT;
    IF cb.id IS NULL OR pb.id IS NULL OR cb.production_batch_id IS DISTINCT FROM pb.id
       OR cb.product_id IS DISTINCT FROM pb.product_id OR cb.sku_id IS DISTINCT FROM pb.sku_id THEN
        RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch batch dependency is unavailable';
    END IF;
    SELECT version.id AS version_id,version.page_template_id,version.status AS version_status,
           version.version AS version_number,version.config_json,version.published_at,
           template.product_id,template.status AS template_status
    INTO page_row FROM public.page_versions AS version
    JOIN public.page_templates AS template
      ON template.tenant_id=version.tenant_id AND template.id=version.page_template_id
    WHERE version.tenant_id=requested_tenant_id AND version.id=requested_page_version_id
    FOR SHARE OF template,version NOWAIT;
    IF page_row.version_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch page dependency is unavailable';
    END IF;
    SELECT * INTO campaign_row FROM public.campaigns
    WHERE tenant_id=requested_tenant_id AND id=requested_campaign_id FOR SHARE NOWAIT;
    IF campaign_row.id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch campaign dependency is unavailable';
    END IF;
    PERFORM benefit.id FROM public.benefits AS benefit
    WHERE benefit.tenant_id=requested_tenant_id AND benefit.campaign_id=requested_campaign_id
    ORDER BY benefit.id::text FOR SHARE NOWAIT;
    PERFORM connector.id FROM public.connectors AS connector
    WHERE connector.tenant_id=requested_tenant_id AND connector.id IN (
      SELECT benefit.connector_id FROM public.benefits AS benefit
      WHERE benefit.tenant_id=requested_tenant_id AND benefit.campaign_id=requested_campaign_id
        AND benefit.connector_id IS NOT NULL
    ) ORDER BY connector.id::text FOR SHARE NOWAIT;
    SELECT product.id,product.brand_id,product.name,product.description,product.image_url,product.origin,
           product.status INTO product_row
    FROM public.products AS product
    WHERE product.tenant_id=requested_tenant_id AND product.id=cb.product_id FOR SHARE NOWAIT;
    SELECT brand.id,brand.name,brand.logo_url,brand.status INTO brand_row
    FROM public.brands AS brand
    WHERE brand.tenant_id=requested_tenant_id AND brand.id=product_row.brand_id FOR SHARE NOWAIT;
    SELECT tenant.brand_profile,tenant.enabled_features INTO tenant_row
    FROM public.tenants AS tenant WHERE tenant.id=requested_tenant_id FOR SHARE NOWAIT;
    PERFORM asset.id FROM public.product_assets AS asset
    WHERE asset.tenant_id=requested_tenant_id AND asset.product_id=cb.product_id
      AND asset.status='active' AND asset.asset_type IN ('test_report','certificate')
      AND (asset.valid_until IS NULL OR asset.valid_until>=timezone('Asia/Shanghai',CURRENT_TIMESTAMP)::date)
    ORDER BY asset.id::text FOR SHARE NOWAIT;
    -- Canonical brand-controlled public and claim-delivery facts.  Deliberately
    -- exclude per-code scan lifecycle, scan_events, risk_alerts, stock_used,
    -- claimed_budget, claim/outbox and delivery state: those runtime facts
    -- remain transaction-authoritative at scan/claim time and must not
    -- invalidate a confirmed release merely because consumers use it.
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'id',asset.id,'type',asset.asset_type,'name',asset.name,'description',asset.description,
      'issuer',asset.issuer,'valid_until',asset.valid_until,'file_url',asset.file_url,
      'image_url',asset.image_url
    ) ORDER BY asset.id::text),'[]'::jsonb) INTO asset_facts
    FROM public.product_assets AS asset
    WHERE asset.tenant_id=requested_tenant_id AND asset.product_id=cb.product_id
      AND asset.status='active' AND asset.asset_type IN ('test_report','certificate')
      AND (asset.valid_until IS NULL OR asset.valid_until>=timezone('Asia/Shanghai',CURRENT_TIMESTAMP)::date);
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'id',benefit.id,'name',benefit.name,'type',benefit.benefit_type,'status',benefit.status,
      'config',benefit.config_json::jsonb-'claimed_budget','stock_total',benefit.stock_total,
      'per_person_limit',benefit.per_person_limit,'connector_id',benefit.connector_id,
      'connector_type',connector.connector_type,'connector_enabled',connector.enabled,
      'connector_config',connector.config,
      'connector_secret_sha256',CASE WHEN connector.secrets_encrypted IS NULL THEN NULL
        ELSE encode(public.digest(connector.secrets_encrypted,'sha256'),'hex') END
    ) ORDER BY benefit.id::text),'[]'::jsonb) INTO benefit_facts
    FROM public.benefits AS benefit LEFT JOIN public.connectors AS connector
      ON connector.tenant_id=benefit.tenant_id AND connector.id=benefit.connector_id
    WHERE benefit.tenant_id=requested_tenant_id AND benefit.campaign_id=requested_campaign_id;
    SELECT item.id,item.public_id,item.status,item.code_type,item.code_batch_id INTO sample
    FROM public.code_items AS item
    WHERE item.tenant_id=requested_tenant_id AND item.code_batch_id=requested_code_batch_id
      AND item.status IN ('activated','bound')
    ORDER BY item.id::text LIMIT 1 FOR SHARE NOWAIT;
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'id',project.id,'mode',project.mode,'status',project.status,
      'configuration_version',project.configuration_version,
      'active_route_version_id',project.active_route_version_id
    ) ORDER BY project.id::text),'[]'::jsonb) INTO takeover_facts
    FROM public.takeover_projects AS project WHERE project.tenant_id=requested_tenant_id;
    page_template_id:=page_row.page_template_id;
    readiness_code_item_id:=sample.id;
    ready:=page_row.version_status='published' AND page_row.template_status='active'
      AND page_row.product_id=cb.product_id
      AND campaign_row.status='active' AND campaign_row.product_id=cb.product_id
      AND campaign_row.start_at<=CURRENT_TIMESTAMP AND campaign_row.end_at>CURRENT_TIMESTAMP
      AND cb.status='activated' AND pb.status='active'
      AND pb.production_date<=timezone('Asia/Shanghai',CURRENT_TIMESTAMP)::date
      AND pb.expiry_date>=timezone('Asia/Shanghai',CURRENT_TIMESTAMP)::date
      AND sample.id IS NOT NULL
      AND (takeover_facts='[]'::jsonb OR (
        takeover_facts @> '[{"mode":"legacy_redirect","status":"completed"}]'::jsonb
        AND takeover_facts @> '[{"mode":"cname","status":"completed"}]'::jsonb
        AND NOT jsonb_path_exists(takeover_facts,'$[*] ? (@.status != "completed" && @.status != "rolled_back")')
      ));
    manifest:=jsonb_build_object(
      'version',3,'tenant_id',requested_tenant_id,
      'page',jsonb_build_object('template_id',page_row.page_template_id,'template_status',page_row.template_status,
        'product_id',page_row.product_id,'version_id',page_row.version_id,'version_number',page_row.version_number,
        'version_status',page_row.version_status,'config',page_row.config_json,'published_at',page_row.published_at),
      'campaign',jsonb_build_object('id',campaign_row.id,'name',campaign_row.name,
        'product_id',campaign_row.product_id,
        'status',campaign_row.status,'start_at',campaign_row.start_at,'end_at',campaign_row.end_at,
        'rules',campaign_row.rules_json),
      'benefits',benefit_facts,
      'product',jsonb_build_object('id',product_row.id,'brand_id',product_row.brand_id,
        'name',product_row.name,'description',product_row.description,'image_url',product_row.image_url,
        'origin',product_row.origin,'status',product_row.status),
      'brand',jsonb_build_object('id',brand_row.id,'name',brand_row.name,
        'logo_url',brand_row.logo_url,'status',brand_row.status),
      'tenant_branding',jsonb_build_object('brand_profile',tenant_row.brand_profile,
        'enabled_features',tenant_row.enabled_features),
      'public_assets',asset_facts,
      'code_batch',jsonb_build_object('id',cb.id,'product_id',cb.product_id,'sku_id',cb.sku_id,
        'production_batch_id',cb.production_batch_id,'status',cb.status),
      'production_batch',jsonb_build_object('id',pb.id,'product_id',pb.product_id,'sku_id',pb.sku_id,
        'batch_code',pb.batch_code,'origin',pb.origin,'status',pb.status,
        'production_date',pb.production_date,'expiry_date',pb.expiry_date,
        'recall_reason',pb.recall_reason,'recalled_at',pb.recalled_at),
      'takeover',takeover_facts,'ready',ready
    );
    content_digest:=encode(public.digest(convert_to(manifest::text,'UTF8'),'sha256'),'hex');
    RETURN NEXT;
EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='launch dependency is concurrently changing';
END;
$function$
"""


def upgrade() -> None:
    op.execute(_FUNCTION)
    # 存量未失效 release 的 digest 按新口径重算映射：底层内容事实未变，
    # 不能因口径调整把已上线/已确认的版本重新打入 invalidated。
    op.execute(
        """
        UPDATE public.launch_releases AS rel
        SET content_digest = r.content_digest,
            brand_confirmation_digest =
              CASE WHEN rel.brand_confirmation_digest IS DISTINCT FROM NULL
                   THEN r.content_digest
                   ELSE rel.brand_confirmation_digest END
        FROM (
          SELECT lr.id, cr.content_digest
          FROM public.launch_releases lr,
               LATERAL public.compute_launch_readiness(
                 lr.tenant_id, lr.page_version_id, lr.campaign_id, lr.code_batch_id) cr
          WHERE lr.status IN ('pending_confirmation', 'confirmed', 'live')
        ) AS r
        WHERE rel.id = r.id
        """
    )


def downgrade() -> None:
    op.execute(
        _FUNCTION.replace(
            "'takeover',takeover_facts,'ready',ready",
            "'sample_code',jsonb_build_object('id',sample.id,'public_id',sample.public_id,\n"
            "        'status',sample.status,'code_type',sample.code_type,'code_batch_id',sample.code_batch_id),\n"
            "      'takeover',takeover_facts,'ready',ready",
        )
    )
