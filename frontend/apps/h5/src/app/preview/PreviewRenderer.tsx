"use client";

import { useState, useEffect } from "react";
import { BrandHeader } from "@/components/BrandHeader";
import { BrandStyle } from "@/components/BrandStyle";
import { FooterSection } from "@/components/FooterSection";
import { ProductCard } from "@/components/ProductCard";
import { VerifyStatus } from "@/components/VerifyStatus";
import { TraceabilitySection } from "@/components/TraceabilitySection";
import { BenefitClaimCard } from "@/components/BenefitClaimCard";
import { PrivateDomainButtons } from "@/components/PrivateDomainButtons";
import { ShopRedirect } from "@/components/ShopRedirect";
import { LeadForm } from "@/components/LeadForm";
import { PrivacyPolicy } from "@/components/PrivacyPolicy";
import { CampaignRules } from "@/components/CampaignRules";
import { MemberCard } from "@/components/MemberCard";
import { PointsBalance } from "@/components/PointsBalance";
import { PointsExchange } from "@/components/PointsExchange";
import { OuterCodeGuide } from "@/components/OuterCodeGuide";
import { RiskAlert } from "@/components/RiskAlert";
import { DualCodeVerify } from "@/components/DualCodeVerify";
import { TestReportSection } from "@/components/TestReportSection";
import { resolveBrandSlots, type TenantBranding } from "@/lib/brand-theme";

type ModuleConfig = {
  id: string;
  type: string;
  enabled?: boolean;
  config?: Record<string, unknown>;
};

type PreviewConfig = {
  modules: ModuleConfig[];
  tenant_branding?: TenantBranding;
};

type PreviewProduct = {
  id: string;
  name: string;
  origin?: string;
  image_url?: string;
  description?: string;
  story_content?: string;
};

type PreviewBatch = {
  id: string;
  batch_code: string;
  production_date?: string;
  expiry_date?: string;
  origin?: string;
};

type PreviewAsset = {
  id: string;
  asset_type: string;
  name: string;
  issuer?: string;
  description?: string;
  valid_until?: string;
  image_url?: string;
  file_url?: string;
};

type PreviewContext = {
  product?: PreviewProduct | null;
  batches?: PreviewBatch[];
  assets?: PreviewAsset[];
};

export function PreviewRenderer() {
  const [config, setConfig] = useState<PreviewConfig | null>(null);
  const [previewContext, setPreviewContext] = useState<PreviewContext>({});
  const [previewMode, setPreviewMode] = useState<"example" | "bound">(
    "example"
  );

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.data?.type === "preview-dsl") {
        const payload = event.data.payload;
        if (payload?.dsl) {
          setConfig(payload.dsl as PreviewConfig);
          setPreviewContext(payload.previewContext || {});
          setPreviewMode(payload.previewMode === "bound" ? "bound" : "example");
        } else {
          setConfig(payload as PreviewConfig);
          setPreviewContext({});
          setPreviewMode("example");
        }
      }
    }
    window.addEventListener("message", handleMessage);
    window.parent.postMessage({ type: "preview-ready" }, "*");
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  if (!config) {
    return (
      <div className="flex min-h-screen items-center justify-center text-foreground-tertiary">
        等待编辑器数据...
      </div>
    );
  }

  const branding = config.tenant_branding || {};
  const enabledModules = config.modules.filter((m) => m.enabled !== false);
  // 消费五槽位（决策3：预览与真实扫码页同机制，brand-theme.ts 三层回退）
  const brandSlots = resolveBrandSlots(
    branding,
    config as Record<string, unknown>
  );

  return (
    <BrandStyle slots={brandSlots}>
      <div className="mx-auto max-w-md min-h-screen">
        <BrandHeader
          name={branding.name || "品牌预览"}
          logoUrl={branding.logo_url || ""}
          primaryColor={brandSlots.primaryColor}
        />
        <div
          className={`mx-4 mt-3 rounded-full px-3 py-1 text-xs ${previewMode === "bound" ? "bg-success-bg text-success" : "bg-info-bg text-info"}`}
        >
          {previewMode === "bound"
            ? "草稿预览 · 已绑定真实产品"
            : "草稿预览 · 示例数据"}
        </div>
        {enabledModules.map((mod) => (
          <PreviewModule
            key={mod.id}
            module={mod}
            previewContext={previewContext}
            previewMode={previewMode}
          />
        ))}
        <FooterSection
          branding={{ name: branding.name, logo_url: branding.logo_url }}
          hideEndorsement={brandSlots.hideYimatongBrand}
        />
      </div>
    </BrandStyle>
  );
}

function PreviewModule({
  module,
  previewContext,
  previewMode,
}: {
  module: ModuleConfig;
  previewContext: PreviewContext;
  previewMode: "example" | "bound";
}) {
  const config = module.config || {};
  const product = previewContext.product || null;
  const latestBatch = previewContext.batches?.[0];
  const assets = previewContext.assets || [];

  switch (module.type) {
    case "product_hero":
      return (
        <ProductCard
          productName={
            (config.title_template as string) || product?.name || "产品名称预览"
          }
          description={
            (config.description as string) ||
            product?.description ||
            product?.story_content ||
            "产品描述预览"
          }
          imageUrl={(config.image_url as string) || product?.image_url}
          showBadge={config.show_verify_badge as boolean}
        />
      );
    case "verification_status":
      return (
        <div className="px-4 mt-3">
          {/* yimatong-zgb1.4/1.5：mock 含 firstScanTime + lastScanTime */}
          <VerifyStatus
            status="first_scan"
            scanCount={1}
            firstScanTime="2026-07-27T10:00:00+08:00"
            lastScanTime="2026-07-27T10:00:00+08:00"
          />
        </div>
      );
    case "light_traceability":
      return (
        <TraceabilitySection
          codeData={{
            batch: {
              // 权威字段：缺失时传空串，由 TraceabilitySection 的明确空态接管（yimatong-zgb1.2）
              origin: latestBatch?.origin || "",
              production_date: latestBatch?.production_date || "",
              expiry_date: latestBatch?.expiry_date || "",
              batch_code: latestBatch?.batch_code || "",
            },
            product: { name: product?.name || "" },
          }}
          config={config as { fields?: string[] }}
        />
      );
    case "test_reports":
      return (
        <TestReportSection
          reports={filterAssets(assets, "test_report", config.report_ids).map(
            (asset) => ({
              id: asset.id,
              title: asset.name,
              summary: asset.description || asset.issuer,
              file_url: asset.file_url,
              image_url: asset.image_url,
            })
          )}
        />
      );
    case "certificates":
      return (
        <div className="mx-4 mt-3 rounded-2xl bg-surface p-4 shadow-sm">
          <h2 className="text-base font-semibold text-foreground">资质证书</h2>
          {filterAssets(assets, "certificate", config.certificate_ids).length >
          0 ? (
            <div className="mt-2 space-y-2">
              {filterAssets(assets, "certificate", config.certificate_ids)
                .slice(0, 3)
                .map((asset) => (
                  <div
                    key={asset.id}
                    className="rounded-xl bg-muted p-3 text-sm text-foreground-secondary"
                  >
                    {asset.name}
                  </div>
                ))}
            </div>
          ) : (
            <p className="mt-2 text-sm text-foreground-tertiary">
              预览模式下显示示例证书
            </p>
          )}
        </div>
      );
    case "benefit_card":
      return (
        <div className="px-4 mt-3">
          <BenefitClaimCard
            benefitId={(config.benefit_id as string) || "preview"}
            benefitType={(config.benefit_type as string) || "platform_coupon"}
            title={(config.title as string) || "领取权益"}
            description={config.description as string}
            configJson={(config.config_json as Record<string, unknown>) || {}}
          />
        </div>
      );
    case "cta_group":
      return (
        <div className="px-4 mt-3">
          <PrivateDomainButtons
            buttons={
              (config.buttons as Array<{
                label: string;
                action:
                  | "wecom_link"
                  | "mini_program"
                  | "external_shop"
                  | "wechat_official";
                url?: string;
              }>) || []
            }
          />
        </div>
      );
    case "shop_redirect":
      return (
        <div className="px-4 mt-3">
          <ShopRedirect
            shops={
              (config.shops as Array<{
                platform: "taobao" | "jd" | "douyin" | "pdd" | "other";
                name: string;
                url: string;
              }>) || []
            }
          />
        </div>
      );
    case "lead_form":
      return (
        <div className="px-4 mt-3">
          <LeadForm
            publicId="preview"
            title={config.title as string}
            subtitle={config.subtitle as string}
            submitLabel={config.submit_label as string}
            fields={config.fields as string[]}
          />
        </div>
      );
    case "media_section":
      return (
        <div className="mx-4 mt-3 rounded-2xl bg-surface p-4 shadow-sm">
          <h2 className="text-base font-semibold text-foreground">视频/图文</h2>
          {filterAssets(assets, ["image", "video", "story"], config.asset_ids)
            .length > 0 ? (
            <div className="mt-2 space-y-2">
              {filterAssets(
                assets,
                ["image", "video", "story"],
                config.asset_ids
              )
                .slice(0, 3)
                .map((asset) => (
                  <div
                    key={asset.id}
                    className="rounded-xl bg-muted p-3 text-sm text-foreground-secondary"
                  >
                    {asset.name}
                  </div>
                ))}
            </div>
          ) : (
            <p className="mt-2 text-sm text-foreground-tertiary">
              {previewMode === "bound"
                ? "未关联素材"
                : "预览模式下显示占位内容"}
            </p>
          )}
        </div>
      );
    case "legal_terms":
      return (
        <div className="px-4 mt-3 mb-4">
          {Boolean(config.show_privacy_policy) && (
            <PrivacyPolicy publicId="preview" />
          )}
          {Boolean(config.show_campaign_rules) && (
            <CampaignRules campaignName="示例活动" rules={{}} />
          )}
        </div>
      );
    case "custom_html":
      if (config.html) {
        return (
          <div
            className="px-4 mt-3"
            dangerouslySetInnerHTML={{ __html: config.html as string }}
          />
        );
      }
      return null;
    case "member_card":
      return (
        <div className="px-4 mt-3">
          <MemberCard
            memberLevel={(config.member_level as string) || "bronze"}
            totalPoints={0}
          />
        </div>
      );
    case "points_balance":
      return (
        <div className="px-4 mt-3">
          <PointsBalance points={(config.points as number) || 0} />
        </div>
      );
    case "points_exchange":
      return (
        <div className="px-4 mt-3">
          <PointsExchange
            benefitId={(config.benefit_id as string) || "preview"}
            title={(config.title as string) || "积分兑换"}
            pointsCost={(config.points_cost as number) || 100}
            description={config.description as string}
          />
        </div>
      );
    case "points_shop":
      return (
        <div className="px-4 mt-3">
          <div className="rounded-2xl bg-surface p-4 shadow-sm">
            <h3 className="text-sm font-semibold text-foreground">积分商城</h3>
            <p className="mt-2 text-sm text-foreground-tertiary">
              预览模式下显示积分商品列表占位内容
            </p>
          </div>
        </div>
      );
    case "outer_code_guide":
      return (
        <div className="px-4 mt-3">
          <OuterCodeGuide
            brandName={(config.brand_name as string) || "品牌"}
            productName={config.product_name as string}
            productImage={config.product_image as string}
            innerCodeHint={config.inner_code_hint as string}
          />
        </div>
      );
    case "risk_alert":
      return (
        <div className="px-4 mt-3">
          <RiskAlert
            alertType="frequency"
            detail="示例预警：正式页面仅展示风控系统生成的真实异常信号。"
          />
        </div>
      );
    case "dual_code_verify":
      return (
        <div className="px-4 mt-3">
          <DualCodeVerify
            publicId="preview"
            codeType="standard"
            isFirstScan
            scanCount={1}
            firstScanTime="2026-07-27T10:00:00+08:00"
          />
        </div>
      );
    case "points_history":
      return (
        <div className="px-4 mt-3">
          <div className="rounded-2xl bg-surface p-4 shadow-sm">
            <h3 className="text-sm font-semibold text-foreground">积分明细</h3>
            <p className="mt-2 text-sm text-foreground-tertiary">
              预览模式下显示占位内容
            </p>
          </div>
        </div>
      );
    default:
      return (
        <div className="mx-4 mt-3 rounded-2xl bg-surface p-4 shadow-sm text-center text-foreground-tertiary">
          未知模块类型: {module.type}
        </div>
      );
  }
}

function filterAssets(
  assets: PreviewAsset[],
  assetType: string | string[],
  selectedIds: unknown
) {
  const types = Array.isArray(assetType) ? assetType : [assetType];
  const ids = Array.isArray(selectedIds) ? selectedIds.map(String) : [];
  return assets.filter(
    (asset) =>
      types.includes(asset.asset_type) &&
      (ids.length === 0 || ids.includes(asset.id))
  );
}
