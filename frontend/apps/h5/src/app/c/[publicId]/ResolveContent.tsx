"use client";

import { useScanEvent } from "@/lib/useScanEvent";
import { VerifyStatus } from "@/components/VerifyStatus";
import { TestReportSection } from "@/components/TestReportSection";
import { BenefitClaimCard } from "@/components/BenefitClaimCard";
import { PrivateDomainButtons } from "@/components/PrivateDomainButtons";
import { CampaignRules } from "@/components/CampaignRules";
import { PrivacyPolicy } from "@/components/PrivacyPolicy";
import { ShopRedirect } from "@/components/ShopRedirect";
import { ErrorPage } from "@/components/ErrorPage";

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

  // HTML 兼容模式
  if (mode === "html") {
    if (!htmlContent) return <FallbackError />;
    return (
      <div
        className="mx-auto max-w-md min-h-screen"
        dangerouslySetInnerHTML={{ __html: htmlContent }}
      />
    );
  }

  if (!jsonPayload) return <FallbackError />;

  // 检查码状态异常
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

  // 从 pageConfig 提取模块列表
  const modules = (pageConfig?.modules as ModuleConfig[] | undefined) || [];
  const enabledModules = modules.filter((m) => m.enabled !== false);

  // 从 codeData 提取产品信息
  const product = codeData?.product as Record<string, unknown> | undefined;
  const brand = codeData?.brand as Record<string, unknown> | undefined;
  const batch = codeData?.batch as Record<string, unknown> | undefined;
  const campaign = jsonPayload.campaign as Record<string, unknown> | undefined;
  const scanInfo = jsonPayload.scan_info as Record<string, unknown> | undefined;

  const brandName = (brand?.name as string) || (tenantBranding?.name) || "";
  const productName = (product?.name as string) || "";
  const productDesc = (product?.description as string) || "";
  const productImages = product?.images as string[] | undefined;

  // 如果没有 DSL 模块配置，使用默认渲染
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

  // DSL 驱动渲染
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
}: {
  module: ModuleConfig;
  publicId: string;
  scanToken?: string;
  codeData: Record<string, unknown>;
  product: Record<string, unknown>;
  campaign: Record<string, unknown>;
  scanInfo: Record<string, unknown>;
  [key: string]: unknown;
}) {
  const config = module.config || {};

  switch (module.type) {
    case "product_hero":
      return (
        <ProductCard
          productName={(product.name as string) || ""}
          description={(product.description as string) || ""}
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
      return <TraceabilitySection codeData={codeData} />;

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

    case "benefit_card":
      return (
        <div className="px-4 mt-3">
          <BenefitClaimCard
            benefitId={(config.benefit_id as string) || ""}
            benefitType={(config.benefit_type as "coupon" | "points" | "lottery" | "gift") || "coupon"}
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
            />
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

    default:
      return null;
  }
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

/* ─── 品牌头部 ──────────────────────────────────── */

function BrandHeader({ name, logoUrl, primaryColor }: { name: string; logoUrl: string; primaryColor?: string }) {
  const bgColor = primaryColor || "#2563eb";
  return (
    <div className="flex items-center gap-3 px-4 py-4 text-white" style={{ backgroundColor: bgColor }}>
      {logoUrl ? (
        <img src={logoUrl} alt={name} className="h-10 w-10 rounded-full border-2 border-white/30 object-cover" />
      ) : (
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-white/20 text-lg font-bold">
          {name.charAt(0) || "Y"}
        </div>
      )}
      <span className="text-lg font-semibold">{name || "一码通"}</span>
    </div>
  );
}

/* ─── 产品卡片 ──────────────────────────────────── */

function ProductCard({ productName, description, imageUrl, showBadge }: {
  productName: string; description: string; imageUrl?: string; showBadge?: boolean;
}) {
  return (
    <div className="mx-4 mt-4 rounded-2xl bg-white p-4 shadow-sm">
      {imageUrl && (
        <img src={imageUrl} alt={productName} className="mb-3 h-48 w-full rounded-xl object-cover" />
      )}
      <h1 className="text-xl font-bold text-gray-900">{productName || "产品信息"}</h1>
      {description && <p className="mt-1 text-sm text-gray-500 leading-relaxed">{description}</p>}
      {showBadge && (
        <div className="mt-2 flex items-center gap-2">
          <span className="inline-flex items-center rounded-full bg-green-50 px-2.5 py-0.5 text-xs font-medium text-green-700">
            正品保障
          </span>
          <span className="inline-flex items-center rounded-full bg-blue-50 px-2.5 py-0.5 text-xs font-medium text-blue-700">
            已验证
          </span>
        </div>
      )}
    </div>
  );
}

/* ─── 溯源信息 ──────────────────────────────────── */

function TraceabilitySection({ codeData }: { codeData: Record<string, unknown> }) {
  const batch = codeData?.batch as Record<string, unknown> | undefined;
  const batchNo = (batch?.batch_no as string) || "";
  const productionDate = (batch?.production_date as string) || "";
  if (!batchNo && !productionDate) return null;

  return (
    <div className="mx-4 mt-3 rounded-2xl bg-white p-4 shadow-sm">
      <h2 className="text-base font-semibold text-gray-900">溯源信息</h2>
      <div className="mt-3 space-y-2">
        {batchNo && <InfoRow label="生产批次" value={batchNo} />}
        {productionDate && <InfoRow label="生产日期" value={productionDate} />}
        <InfoRow label="码编号" value={codeData?.public_id as string || ""} />
      </div>
    </div>
  );
}

/* ─── 留资表单 ──────────────────────────────────── */

function LeadForm({ publicId, scanToken }: { publicId: string; scanToken?: string }) {
  return (
    <div className="mt-3 rounded-2xl bg-white p-4 shadow-sm">
      <h2 className="text-base font-semibold text-gray-900">留下联系方式</h2>
      <p className="mt-1 text-xs text-gray-400">品牌将通过此信息与您联系（选填）</p>
      <form
        className="mt-3 space-y-3"
        onSubmit={async (e) => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          const body = { name: fd.get("name"), phone: fd.get("phone"), public_id: publicId };
          try {
            const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
            await fetch(`${API_URL}/api/v1/consumers/lead-capture`, {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                ...(scanToken ? { Authorization: `Bearer ${scanToken}` } : {}),
              },
              body: JSON.stringify(body),
            });
          } catch { /* 留资失败不影响体验 */ }
        }}
      >
        <div>
          <label htmlFor="lead-name" className="block text-sm font-medium text-gray-700">姓名</label>
          <input id="lead-name" name="name" type="text" placeholder="请输入姓名"
            className="mt-1 block w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none" />
        </div>
        <div>
          <label htmlFor="lead-phone" className="block text-sm font-medium text-gray-700">手机号</label>
          <input id="lead-phone" name="phone" type="tel" placeholder="请输入手机号"
            className="mt-1 block w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none" />
        </div>
        <button type="submit"
          className="w-full rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 active:bg-blue-800 transition-colors">
          提交
        </button>
      </form>
    </div>
  );
}

/* ─── 底部 ──────────────────────────────────────── */

function FooterSection({ branding }: { branding: { name?: string; logo_url?: string } | undefined }) {
  return (
    <div className="mx-4 mb-6 mt-3 rounded-2xl bg-white p-4 shadow-sm">
      <div className="flex items-center gap-2">
        {branding?.logo_url ? (
          <img src={branding.logo_url} alt="" className="h-6 w-6 rounded object-cover" />
        ) : (
          <div className="flex h-6 w-6 items-center justify-center rounded bg-gray-100 text-xs font-bold text-gray-500">
            {(branding?.name || "Y").charAt(0)}
          </div>
        )}
        <span className="text-sm text-gray-500">由一码通提供技术支持</span>
      </div>
    </div>
  );
}

/* ─── 兜底错误 ──────────────────────────────────── */

function FallbackError() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-gray-50 px-4">
      <div className="rounded-2xl bg-white p-8 text-center shadow-sm">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-red-50">
          <svg className="h-8 w-8 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M12 3a9 9 0 100 18 9 9 0 000-18z" />
          </svg>
        </div>
        <h2 className="text-lg font-semibold text-gray-900">暂时无法加载</h2>
        <p className="mt-1 text-sm text-gray-500">网络异常或服务暂不可用，请稍后重试</p>
      </div>
    </div>
  );
}

/* ─── 工具 ──────────────────────────────────────── */

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-gray-500">{label}</span>
      <span className="font-medium text-gray-900">{value}</span>
    </div>
  );
}
