"use client";

import { useScanEvent } from "@/lib/useScanEvent";
import { VerifyStatus } from "@/components/VerifyStatus";
import { TestReportSection } from "@/components/TestReportSection";
import { BenefitClaimCard } from "@/components/BenefitClaimCard";
import { PrivateDomainButtons } from "@/components/PrivateDomainButtons";
import { CampaignRules } from "@/components/CampaignRules";
import { PrivacyPolicy } from "@/components/PrivacyPolicy";
import { ShopRedirect } from "@/components/ShopRedirect";
import { MemberCard } from "@/components/MemberCard";
import { PointsBalance } from "@/components/PointsBalance";
import { PointsExchange } from "@/components/PointsExchange";
import { OuterCodeGuide } from "@/components/OuterCodeGuide";
import { RiskAlert } from "@/components/RiskAlert";
import { DualCodeVerify } from "@/components/DualCodeVerify";
import { PointsHistory } from "@/components/PointsHistory";
import { ErrorPage } from "@/components/ErrorPage";
import { BrandHeader } from "@/components/BrandHeader";
import { ProductCard } from "@/components/ProductCard";
import { TraceabilitySection } from "@/components/TraceabilitySection";
import { LeadForm } from "@/components/LeadForm";
import { FooterSection } from "@/components/FooterSection";
import { FallbackError } from "@/components/FallbackError";
import { sanitizeHtml } from "@/lib/sanitize";

interface ResolveContentProps {
  mode: "json" | "html";
  publicId: string;
  jsonPayload: Record<string, unknown> | null;
  htmlContent: string | null;
}

type ModuleConfig = {
  id: string;
  type: string;
  enabled?: boolean;
  config?: Record<string, unknown>;
};

export function ResolveContent({
  mode,
  publicId,
  jsonPayload,
  htmlContent,
}: ResolveContentProps) {
  const scanToken = (jsonPayload?.scan_token as string) || undefined;

  useScanEvent({
    publicId,
    scanToken,
    pageVersionId: undefined,
  });

  if (mode === "html") {
    if (!htmlContent) return <FallbackError />;
    return (
      <div
        className="mx-auto max-w-md min-h-screen"
        dangerouslySetInnerHTML={{ __html: sanitizeHtml(htmlContent) }}
      />
    );
  }

  if (!jsonPayload) return <FallbackError />;

  const codeData = jsonPayload.code_data as Record<string, unknown> | undefined;
  const codeStatus = codeData?.status as string | undefined;

  if (codeStatus === "revoked" || codeStatus === "frozen") {
    return (
      <div className="mx-auto max-w-md min-h-screen bg-gray-50">
        <ErrorPage
          errorCode={codeStatus === "frozen" ? "frozen" : "revoked"}
          publicId={publicId}
        />
      </div>
    );
  }

  const tenantBranding = jsonPayload.tenant_branding as {
    name: string;
    logo_url?: string;
    primary_color?: string;
  } | undefined;
  const pageConfig = jsonPayload.page_config as Record<string, unknown> | undefined;

  const modules = (pageConfig?.modules as ModuleConfig[] | undefined) || [];
  const enabledModules = modules.filter((m) => m.enabled !== false);

  const product = codeData?.product as Record<string, unknown> | undefined;
  const brand = codeData?.brand as Record<string, unknown> | undefined;
  const batch = codeData?.batch as Record<string, unknown> | undefined;
  const campaign = jsonPayload.campaign as Record<string, unknown> | undefined;
  const scanInfo = jsonPayload.scan_info as Record<string, unknown> | undefined;

  const brandName = (brand?.name as string) || (tenantBranding?.name) || "";
  const productName = (product?.name as string) || "";
  const productDesc = (product?.description as string) || "";
  const productImages = product?.images as string[] | undefined;

  if (enabledModules.length === 0) {
    return (
      <DefaultRender
        publicId={publicId}
        scanToken={scanToken}
        brandName={brandName}
        brandLogo={tenantBranding?.logo_url || (brand?.logo_url as string) || ""}
        primaryColor={tenantBranding?.primary_color}
        productName={productName}
        productDesc={productDesc}
        productImage={productImages?.[0]}
        codeData={codeData || {}}
      />
    );
  }

  return (
    <div className="mx-auto max-w-md min-h-screen bg-gray-50">
      <BrandHeader
        name={brandName || tenantBranding?.name || ""}
        logoUrl={tenantBranding?.logo_url || ""}
        primaryColor={tenantBranding?.primary_color}
      />

      {enabledModules.map((mod) => (
        <ModuleRenderer
          key={mod.id}
          module={mod}
          publicId={publicId}
          scanToken={scanToken}
          codeData={codeData || {}}
          product={product || {}}
          brand={brand || {}}
          batch={batch || {}}
          campaign={campaign || {}}
          scanInfo={scanInfo || {}}
          tenantBranding={tenantBranding}
        />
      ))}

      <FooterSection branding={tenantBranding} />
    </div>
  );
}

/* ─── 模块渲染器 ──────────────────────────────── */

function ModuleRenderer({
  module,
  publicId,
  scanToken,
  codeData,
  product,
  campaign,
  scanInfo,
  batch,
}: {
  module: ModuleConfig;
  publicId: string;
  scanToken?: string;
  codeData: Record<string, unknown>;
  product: Record<string, unknown>;
  campaign: Record<string, unknown>;
  scanInfo: Record<string, unknown>;
  batch: Record<string, unknown>;
  [key: string]: unknown;
}) {
  const config = module.config || {};

  switch (module.type) {
    case "product_hero":
      return (
        <ProductCard
          productName={(product.name as string) || ""}
          description={(product.description as string) || ""}
          images={product.images as string[] | undefined}
          imageUrl={((product.images as string[])?.[0])}
          showBadge={config.show_verify_badge as boolean}
        />
      );

    case "verification_status":
      return (
        <div className="px-4 mt-3">
          <VerifyStatus
            status={
              (scanInfo.is_first_scan as boolean)
                ? "first_scan"
                : "repeat_scan"
            }
            scanCount={scanInfo.scan_count as number}
            firstScanTime={scanInfo.first_scan_time as string}
          />
        </div>
      );

    case "light_traceability":
      return (
        <TraceabilitySection
          codeData={{ ...codeData, batch, product }}
          config={config as { fields?: string[] }}
        />
      );

    case "test_reports":
      return (
        <TestReportSection
          reports={
            (codeData.test_reports as Array<{
              id: string;
              title: string;
              summary?: string;
              image_url?: string;
              file_url?: string;
              date?: string;
            }>) || []
          }
        />
      );

    case "certificates":
      return (
        <CertificateRenderer codeData={codeData} />
      );

    case "benefit_card":
      return (
        <div className="px-4 mt-3">
          <BenefitClaimCard
            benefitId={(config.benefit_id as string) || ""}
            benefitType={(config.benefit_type as "coupon" | "points" | "lottery" | "gift" | "cash_red_packet") || "coupon"}
            title={(config.title as string) || "领取权益"}
            description={config.description as string}
            scanToken={scanToken}
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
                action: "wecom_link" | "mini_program" | "external_shop" | "wechat_official";
                config_id?: string;
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
            publicId={publicId}
            scanToken={scanToken}
            title={config.title as string}
            subtitle={config.subtitle as string}
            submitLabel={config.submit_label as string}
            fields={config.fields as string[]}
          />
        </div>
      );

    case "media_section":
      return <MediaRenderer codeData={codeData} config={config} />;

    case "legal_terms":
      return (
        <div className="px-4 mt-3 mb-4">
          {Boolean(config.show_campaign_rules) && String(campaign.name || "") && (
            <CampaignRules
              campaignName={campaign.name as string}
              rules={campaign.rules as Record<string, unknown>}
            />
          )}
          {Boolean(config.show_privacy_policy) && (
            <PrivacyPolicy
              content={config.privacy_content as string}
              publicId={publicId}
            />
          )}
        </div>
      );

    case "custom_html":
      if (config.html) {
        return (
          <div
            className="px-4 mt-3"
            dangerouslySetInnerHTML={{ __html: sanitizeHtml(config.html as string) }}
          />
        );
      }
      return null;

    case "member_card":
      return (
        <div className="px-4 mt-3">
          <MemberCard
            consumerId={(config.consumer_id as string) || ""}
            memberLevel={config.member_level as string}
            totalPoints={config.total_points as number}
          />
        </div>
      );

    case "points_balance":
      return (
        <div className="px-4 mt-3">
          <PointsBalance
            points={(config.points as number) || 0}
            consumerId={config.consumer_id as string}
          />
        </div>
      );

    case "points_exchange":
      return (
        <div className="px-4 mt-3">
          <PointsExchange
            benefitId={(config.benefit_id as string) || ""}
            title={(config.title as string) || "积分兑换"}
            pointsCost={(config.points_cost as number) || 0}
            description={config.description as string}
            scanToken={scanToken}
          />
        </div>
      );

    case "outer_code_guide":
      return (
        <div className="px-4 mt-3">
          <OuterCodeGuide
            brandName={(config.brand_name as string) || ""}
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
            alertType={(config.alert_type as string) || "frequency"}
            detail={config.detail as string}
            scanCount={config.scan_count as number}
            detectedCity={config.detected_city as string}
          />
        </div>
      );

    case "dual_code_verify":
      return (
        <div className="px-4 mt-3">
          <DualCodeVerify
            publicId={publicId}
            codeType={(codeData.code_type as string) || "standard"}
            isFirstScan={(scanInfo.is_first_scan as boolean) ?? true}
            scanCount={scanInfo.scan_count as number}
            firstScanTime={scanInfo.first_scan_time as string}
            productVerified={config.product_verified as boolean}
          />
        </div>
      );

    case "points_history":
      if (!scanToken) return null;
      return (
        <div className="px-4 mt-3">
          <PointsHistory
            consumerId={(config.consumer_id as string) || ""}
            scanToken={scanToken}
          />
        </div>
      );

    default:
      return null;
  }
}

/* ─── 证书渲染（简化） ──────────────────────────── */

function CertificateRenderer({ codeData }: { codeData: Record<string, unknown> }) {
  const certs = codeData.certificates as Array<{
    name: string;
    issuer?: string;
    valid_until?: string;
    image_url?: string;
    file_url?: string;
  }> | undefined;

  if (!certs?.length) return null;

  return (
    <div className="mx-4 mt-3 rounded-2xl bg-white p-4 shadow-sm">
      <h2 className="text-base font-semibold text-gray-900">资质证书</h2>
      <div className="mt-3 space-y-3">
        {certs.map((cert, i) => (
          <div key={i} className="flex items-start gap-3 rounded-xl border border-gray-100 p-3">
            {cert.image_url && (
              <img src={cert.image_url} alt={cert.name} className="h-16 w-16 rounded-lg object-cover shrink-0" />
            )}
            <div className="min-w-0">
              <p className="text-sm font-medium text-gray-900">{cert.name}</p>
              {cert.issuer && <p className="text-xs text-gray-500">颁发机构：{cert.issuer}</p>}
              {cert.valid_until && <p className="text-xs text-gray-500">有效期至：{cert.valid_until}</p>}
              {cert.file_url && (
                <a href={cert.file_url} target="_blank" rel="noopener noreferrer"
                  className="mt-1 inline-block text-xs text-blue-600 hover:underline">
                  查看详情
                </a>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─── 视频/图文渲染 ──────────────────────────── */

function MediaRenderer({
  codeData,
  config,
}: {
  codeData: Record<string, unknown>;
  config: Record<string, unknown>;
}) {
  const items = (codeData.media_items || config.items) as Array<{
    type: "video" | "image";
    url: string;
    poster_url?: string;
    caption?: string;
  }> | undefined;

  if (!items?.length) return null;

  return (
    <div className="mx-4 mt-3 rounded-2xl bg-white p-4 shadow-sm">
      <h2 className="text-base font-semibold text-gray-900">品牌故事</h2>
      <div className="mt-3 space-y-3">
        {items.map((item, i) =>
          item.type === "video" ? (
            <div key={i}>
              <video
                src={item.url}
                poster={item.poster_url}
                controls
                playsInline
                muted
                preload="metadata"
                className="w-full rounded-xl"
              />
              {item.caption && <p className="mt-1 text-xs text-gray-500">{item.caption}</p>}
            </div>
          ) : (
            <div key={i}>
              <img src={item.url} alt={item.caption || ""} className="w-full rounded-xl object-cover" />
              {item.caption && <p className="mt-1 text-xs text-gray-500">{item.caption}</p>}
            </div>
          ),
        )}
      </div>
    </div>
  );
}

/* ─── 默认渲染（无 DSL 时） ────────────────────── */

function DefaultRender({
  publicId,
  scanToken,
  brandName,
  brandLogo,
  primaryColor,
  productName,
  productDesc,
  productImage,
  codeData,
}: {
  publicId: string;
  scanToken?: string;
  brandName: string;
  brandLogo: string;
  primaryColor?: string;
  productName: string;
  productDesc: string;
  productImage?: string;
  codeData: Record<string, unknown>;
}) {
  return (
    <div className="mx-auto max-w-md min-h-screen bg-gray-50">
      <BrandHeader name={brandName} logoUrl={brandLogo} primaryColor={primaryColor} />
      <ProductCard productName={productName} description={productDesc} imageUrl={productImage} showBadge />
      <TraceabilitySection codeData={codeData} />
      <div className="px-4 pb-6">
        <LeadForm publicId={publicId} scanToken={scanToken} />
      </div>
      <FooterSection branding={{ name: brandName, logo_url: brandLogo }} />
    </div>
  );
}
