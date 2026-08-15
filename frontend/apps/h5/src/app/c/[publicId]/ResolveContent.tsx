"use client";

import { useEffect, useState } from "react";

import dayjs from "dayjs";
import { TriangleAlert } from "lucide-react";

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
import { PointsShop } from "@/components/PointsShop";
import { safePublicUrl } from "@/lib/public-url";
import { readLatestClaimRef } from "@/lib/claim-revisit";
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
import { BrandStyle } from "@/components/BrandStyle";
import { resolveBrandSlots, type TenantBranding } from "@/lib/brand-theme";
import { sanitizeHtml } from "@/lib/sanitize";

interface ResolveContentProps {
  mode: "json" | "html";
  publicId: string;
  jsonPayload: Record<string, unknown> | null;
  htmlContent: string | null;
  /** 瞬时加载失败的人工重试入口（传入后 FallbackError 显示"重新查验"） */
  onRetry?: () => void;
  retrying?: boolean;
}

type ModuleConfig = {
  id: string;
  type: string;
  enabled?: boolean;
  config?: Record<string, unknown>;
};

type ProductionBatchData = Record<string, unknown> & {
  origin?: string | null;
  status?: "active" | "recalled" | "expired";
  recall_reason?: string | null;
  recalled_at?: string | null;
};

const BATCH_BLOCKED_MODULE_TYPES = new Set([
  "benefit_card",
  "cta_group",
  "shop_redirect",
  "lead_form",
  "member_card",
  "points_balance",
  "points_shop",
  "points_exchange",
  "points_history",
  "dual_code_verify",
]);

function safeLogoUrl(value: unknown): string {
  return typeof value === "string" ? safePublicUrl(value) || "" : "";
}

export function ResolveContent({
  mode,
  publicId,
  jsonPayload,
  htmlContent,
  onRetry,
  retrying,
}: ResolveContentProps) {
  const scanToken = (jsonPayload?.scan_token as string) || undefined;
  const codeData = jsonPayload?.code_data as
    Record<string, unknown> | undefined;
  const batch = codeData?.batch as ProductionBatchData | undefined;
  const scanInfo = jsonPayload?.scan_info as
    Record<string, unknown> | undefined;
  const batchStatus = batch?.status as string | undefined;
  const codeStatus = codeData?.status as string | undefined;
  // yimatong-zgb1.6：用权威 lifecycle 判断状态（互不混淆）
  const lifecycle = codeData?.lifecycle as string | undefined;
  const resultCode = codeData?.result as string | undefined;
  const batchBlocksBenefits =
    batchStatus === "recalled" || batchStatus === "expired";
  const launchPaused =
    scanInfo?.benefit_paused === true &&
    scanInfo?.paused_reason === "launch_not_live";
  const benefitsBlocked =
    batchBlocksBenefits || lifecycle === "frozen" || launchPaused;

  useScanEvent({
    publicId,
    scanToken,
    pageVersionId: undefined,
    enabled:
      Boolean(jsonPayload) && !benefitsBlocked && resultCode !== "unavailable",
  });

  const tenantBranding = jsonPayload?.tenant_branding as
    TenantBranding | undefined;
  const pageConfig = jsonPayload?.page_config as
    Record<string, unknown> | undefined;

  // 回访恢复入口（kc6d.7）：本机该码有过领取时提示"查看我的红包"。
  // localStorage 只能在 effect 里读：render 期读取会导致 SSR/hydration 标记
  // 不一致（服务端无 window）。初始渲染统一不显示，挂载后再补入口。
  const [latestClaimId, setLatestClaimId] = useState<string | null>(null);
  useEffect(() => {
    setLatestClaimId(readLatestClaimRef(publicId));
  }, [publicId]);

  // 租户品牌槽位三层回退：页面 DSL brand_theme → 租户 brand_profile → 默认主题（yimatong-z6i0.10）
  const brandSlots = resolveBrandSlots(tenantBranding, pageConfig);

  if (mode === "html") {
    if (!htmlContent)
      return <FallbackError onRetry={onRetry} retrying={retrying} />;
    return (
      <div
        className="mx-auto max-w-md min-h-screen"
        dangerouslySetInnerHTML={{ __html: sanitizeHtml(htmlContent) }}
      />
    );
  }

  if (!jsonPayload)
    return <FallbackError onRetry={onRetry} retrying={retrying} />;
  if (resultCode === "unavailable")
    return <FallbackError onRetry={onRetry} retrying={retrying} />;

  // yimatong-zgb1.6 AC3：frozen 码保留溯源（不再跳错误页），只在顶部显示审核中提示。
  // voided（revoked/expired）仍跳错误页（终止性，不返回溯源）。
  // unactivated 跳错误页（提示尚未激活）。
  if (
    resultCode === "voided" ||
    codeStatus === "revoked" ||
    codeStatus === "expired"
  ) {
    return (
      <div className="mx-auto max-w-md min-h-screen bg-canvas">
        <ErrorPage
          errorCode="revoked"
          publicId={publicId}
          showRetry={false}
          supportPhone={brandSlots.supportPhone}
          supportWecomUrl={brandSlots.supportWecomUrl}
        />
      </div>
    );
  }
  if (
    lifecycle === "unactivated" ||
    codeStatus === "created" ||
    resultCode === "unactivated"
  ) {
    return (
      <div className="mx-auto max-w-md min-h-screen bg-canvas">
        <ErrorPage
          errorCode="not_activated"
          publicId={publicId}
          showRetry={false}
          supportPhone={brandSlots.supportPhone}
          supportWecomUrl={brandSlots.supportWecomUrl}
        />
      </div>
    );
  }

  const modules = (pageConfig?.modules as ModuleConfig[] | undefined) || [];
  const enabledModules = modules.filter((m) => m.enabled !== false);
  const visibleModules = benefitsBlocked
    ? enabledModules.filter(
        (module) => !BATCH_BLOCKED_MODULE_TYPES.has(module.type)
      )
    : enabledModules;

  const product = codeData?.product as Record<string, unknown> | undefined;
  const brand = codeData?.brand as Record<string, unknown> | undefined;
  const campaign = jsonPayload.campaign as Record<string, unknown> | undefined;

  const brandName = (brand?.name as string) || tenantBranding?.name || "";
  const tenantBrandLogo = safeLogoUrl(tenantBranding?.logo_url);
  const resolvedBrandLogo = tenantBrandLogo || safeLogoUrl(brand?.logo_url);
  const productName = (product?.name as string) || "";
  const productDesc = (product?.description as string) || "";
  const productImages = product?.images as string[] | undefined;

  if (enabledModules.length === 0) {
    return (
      <BrandStyle slots={brandSlots}>
        <DefaultRender
          publicId={publicId}
          scanToken={scanToken}
          brandName={brandName}
          brandLogo={resolvedBrandLogo}
          primaryColor={brandSlots.primaryColor}
          productName={productName}
          productDesc={productDesc}
          productImage={productImages?.[0]}
          codeData={codeData || {}}
          batchStatus={batchStatus}
          benefitsBlocked={benefitsBlocked}
          recallWarning={scanInfo?.recall_warning}
        />
      </BrandStyle>
    );
  }

  return (
    <BrandStyle slots={brandSlots}>
      <div className="mx-auto max-w-md min-h-screen">
        <BatchStatusNotice
          status={batchStatus}
          batch={batch}
          recallWarning={scanInfo?.recall_warning}
        />
        <BrandHeader
          name={brandName || tenantBranding?.name || ""}
          logoUrl={tenantBrandLogo}
          primaryColor={brandSlots.primaryColor}
        />

        {/* 回访恢复入口（kc6d.7）：继续查看上一笔红包发放结果，不重复领取 */}
        {latestClaimId && (
          <a
            href={`/redpacket/result?claim_id=${encodeURIComponent(latestClaimId)}&public_id=${encodeURIComponent(publicId)}`}
            className="mb-3 flex items-center justify-between rounded-xl border border-warning bg-warning-bg px-4 py-3 text-sm font-medium text-warning"
          >
            <span>查看我的红包发放结果</span>
            <span aria-hidden="true">→</span>
          </a>
        )}

        {/* yimatong-zgb1.6 AC3：frozen 码保留溯源，但顶部提示审核中 + 权益暂停 */}
        {lifecycle === "frozen" && (
          <div
            className="mx-4 mt-3 rounded-xl border border-warning bg-warning-bg p-3 text-sm text-warning"
            role="status"
            aria-label="该码正在审核中，权益暂时暂停"
          >
            <div className="flex items-center gap-2">
              <TriangleAlert
                className="h-5 w-5 shrink-0 text-warning"
                aria-hidden="true"
              />
              <div>
                <p className="font-semibold">该码正在审核中</p>
                <p className="mt-0.5 text-xs text-warning">
                  溯源信息可正常查看，权益领取暂时暂停。如有疑问请联系品牌客服。
                </p>
              </div>
            </div>
          </div>
        )}

        {launchPaused && (
          <div
            className="mx-4 mt-3 rounded-xl border border-warning bg-warning-bg p-3 text-sm text-warning"
            role="status"
            aria-label="当前活动尚未开放，权益暂时暂停"
          >
            <div className="flex items-center gap-2">
              <TriangleAlert
                className="h-5 w-5 shrink-0 text-warning"
                aria-hidden="true"
              />
              <div>
                <p className="font-semibold">当前活动尚未开放</p>
                <p className="mt-0.5 text-xs text-warning">
                  溯源信息可正常查看，权益领取暂时暂停。
                </p>
              </div>
            </div>
          </div>
        )}

        {/* yimatong-zgb1.7 AC2：异常码风险提示（保留溯源，不宣告假货，Decision 16） */}
        {(scanInfo?.risk_warning as
          { level?: string; message?: string } | undefined) && (
          <div
            className="mx-4 mt-3 rounded-xl border border-warning bg-warning-bg p-3 text-sm text-warning"
            role="alert"
            aria-label="该码存在异常使用信号"
          >
            <div className="flex items-start gap-2">
              <TriangleAlert
                className="mt-0.5 h-5 w-5 shrink-0 text-warning"
                aria-hidden="true"
              />
              <div>
                <p className="font-semibold">该码存在异常使用信号</p>
                <p className="mt-0.5 text-xs text-warning">
                  {(scanInfo?.risk_warning as { message?: string }).message ||
                    "请审慎对待。如非本人操作，请联系品牌客服。"}
                </p>
              </div>
            </div>
          </div>
        )}

        {visibleModules.map((mod) => (
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

        <FooterSection
          branding={
            tenantBranding
              ? { ...tenantBranding, logo_url: tenantBrandLogo }
              : undefined
          }
          hideEndorsement={brandSlots.hideYimatongBrand}
        />
      </div>
    </BrandStyle>
  );
}

function BatchStatusNotice({
  status,
  batch,
  recallWarning,
}: {
  status?: string;
  batch?: Record<string, unknown>;
  recallWarning?: unknown;
}) {
  if (status === "recalled") {
    const warning = recallWarning as
      { reason?: string; recalled_at?: string } | undefined;
    const reason = warning?.reason || (batch?.recall_reason as string) || "";
    const recalledAt =
      warning?.recalled_at || (batch?.recalled_at as string) || "";
    const formattedTime = dayjs(recalledAt).isValid()
      ? dayjs(recalledAt).format("YYYY-MM-DD HH:mm")
      : "";

    return (
      <div
        className="mx-4 mt-3 rounded-xl border border-danger bg-danger-bg p-3 text-sm text-danger"
        role="alert"
        aria-label="该生产批次已召回"
      >
        <div className="flex items-start gap-2">
          <TriangleAlert
            className="mt-0.5 h-5 w-5 shrink-0"
            aria-hidden="true"
          />
          <div>
            <p className="font-semibold">该生产批次已召回</p>
            {reason && <p className="mt-1">召回原因：{reason}</p>}
            {formattedTime && (
              <p className="mt-1 text-xs">召回时间：{formattedTime}</p>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (status === "expired") {
    return (
      <div
        className="mx-4 mt-3 rounded-xl border border-warning bg-warning-bg p-3 text-sm text-warning"
        role="status"
        aria-label="该生产批次已过期"
      >
        <div className="flex items-start gap-2">
          <TriangleAlert
            className="mt-0.5 h-5 w-5 shrink-0"
            aria-hidden="true"
          />
          <div>
            <p className="font-semibold">该生产批次已过期</p>
            <p className="mt-1 text-xs">溯源信息保留展示，权益入口已关闭。</p>
          </div>
        </div>
      </div>
    );
  }

  return null;
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
          image_url={(product.image_url as string) || ""}
          images={product.images as string[] | undefined}
          imageUrl={(product.images as string[])?.[0]}
          showBadge={config.show_verify_badge as boolean}
        />
      );

    case "verification_status":
      return (
        <div className="px-4 mt-3">
          <VerifyStatus
            status={
              (scanInfo.is_first_scan as boolean) ? "first_scan" : "repeat_scan"
            }
            // yimatong-zgb1.4：优先用 post-insert 的 verification_count（首次=1，更直观），
            // 兜底旧 scan_count（兼容期）。
            scanCount={
              (scanInfo.verification_count as number) ??
              (scanInfo.scan_count as number)
            }
            firstScanTime={scanInfo.first_scan_time as string}
            // yimatong-zgb1.5 AC1：repeat 状态展示最近查验时间
            lastScanTime={scanInfo.last_scan_time as string}
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
      return <CertificateRenderer codeData={codeData} />;

    case "benefit_card": {
      const campaignBenefit = campaign.benefit as
        Record<string, unknown> | undefined;
      const campaignRules = campaign.rules as
        Record<string, unknown> | undefined;
      return (
        <div className="px-4 mt-3">
          <BenefitClaimCard
            benefitId={
              (config.benefit_id as string) ||
              (campaignBenefit?.id as string) ||
              ""
            }
            benefitType={
              (config.benefit_type as string) ||
              (campaignBenefit?.benefit_type as string) ||
              "platform_coupon"
            }
            title={
              (config.title as string) ||
              (campaignBenefit?.name as string) ||
              "领取权益"
            }
            description={
              (config.description as string) ||
              (campaignBenefit?.description as string)
            }
            configJson={
              (config.config_json as Record<string, unknown>) ||
              (campaignBenefit?.config_json as Record<string, unknown>) ||
              {}
            }
            scanToken={scanToken}
            publicId={publicId}
            wecomMode={
              (campaignRules?.wecom_mode as "none" | "guide" | "required") ||
              "none"
            }
          />
        </div>
      );
    }

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
          {Boolean(config.show_campaign_rules) &&
            String(campaign.name || "") && (
              <CampaignRules
                campaignName={campaign.name as string}
                rules={campaign.rules as Record<string, unknown>}
              />
            )}
          {Boolean(config.show_privacy_policy) && (
            <PrivacyPolicy
              content={config.privacy_content as string}
              publicId={publicId}
              scanToken={scanToken}
            />
          )}
        </div>
      );

    case "custom_html":
      if (config.html) {
        return (
          <div
            className="px-4 mt-3"
            dangerouslySetInnerHTML={{
              __html: sanitizeHtml(config.html as string),
            }}
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
            scanToken={scanToken}
          />
        </div>
      );

    case "points_shop":
      return (
        <div className="px-4 mt-3">
          <PointsShop
            consumerId={config.consumer_id as string}
            scanToken={scanToken}
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
      if (!scanInfo.risk_warning) return null;
      const riskWarning = scanInfo.risk_warning as Record<string, unknown>;
      return (
        <div className="px-4 mt-3">
          <RiskAlert
            alertType={(riskWarning.alert_type as string) || "frequency"}
            detail={riskWarning.message as string}
            scanCount={
              (scanInfo.verification_count as number) ??
              (scanInfo.scan_count as number)
            }
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
            // yimatong-zgb1.4：优先 verification_count，兜底 scan_count。
            scanCount={
              (scanInfo.verification_count as number) ??
              (scanInfo.scan_count as number)
            }
            firstScanTime={scanInfo.first_scan_time as string}
            productVerified={codeData.lifecycle === "active"}
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

function CertificateRenderer({
  codeData,
}: {
  codeData: Record<string, unknown>;
}) {
  const certs = codeData.certificates as
    | Array<{
        name: string;
        issuer?: string;
        valid_until?: string;
        image_url?: string;
        file_url?: string;
      }>
    | undefined;

  if (!certs?.length) return null;

  return (
    <div className="mx-4 mt-3 rounded-2xl bg-surface p-4 shadow-sm">
      <h2 className="text-base font-semibold text-foreground">资质证书</h2>
      <div className="mt-3 space-y-3">
        {certs.map((cert, i) => {
          const imageUrl = safePublicUrl(cert.image_url);
          const fileUrl = safePublicUrl(cert.file_url);
          return (
            <div
              key={i}
              className="flex items-start gap-3 rounded-xl border border-base p-3"
            >
              {imageUrl && (
                <img
                  src={imageUrl}
                  alt={cert.name}
                  className="h-16 w-16 rounded-lg object-cover shrink-0"
                />
              )}
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground">
                  {cert.name}
                </p>
                {cert.issuer && (
                  <p className="text-xs text-foreground-secondary">
                    颁发机构：{cert.issuer}
                  </p>
                )}
                {cert.valid_until && (
                  <p className="text-xs text-foreground-secondary">
                    有效期至：{cert.valid_until}
                  </p>
                )}
                {fileUrl && (
                  <a
                    href={fileUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-1 inline-block text-xs text-link hover:underline"
                  >
                    查看详情
                  </a>
                )}
              </div>
            </div>
          );
        })}
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
  const rawItems = (codeData.media_items || config.items) as
    | Array<{
        type: "video" | "image";
        url: string;
        poster_url?: string;
        caption?: string;
      }>
    | undefined;

  const items = rawItems
    ?.map((item) => ({
      ...item,
      url: safePublicUrl(item.url),
      poster_url: safePublicUrl(item.poster_url) || undefined,
    }))
    .filter((item): item is typeof item & { url: string } => Boolean(item.url));

  if (!items?.length) return null;

  return (
    <div className="mx-4 mt-3 rounded-2xl bg-surface p-4 shadow-sm">
      <h2 className="text-base font-semibold text-foreground">品牌故事</h2>
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
              {item.caption && (
                <p className="mt-1 text-xs text-foreground-secondary">
                  {item.caption}
                </p>
              )}
            </div>
          ) : (
            <div key={i}>
              <img
                src={item.url}
                alt={item.caption || ""}
                className="w-full rounded-xl object-cover"
              />
              {item.caption && (
                <p className="mt-1 text-xs text-foreground-secondary">
                  {item.caption}
                </p>
              )}
            </div>
          )
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
  batchStatus,
  benefitsBlocked,
  recallWarning,
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
  batchStatus?: string;
  benefitsBlocked: boolean;
  recallWarning?: unknown;
}) {
  const _product = codeData.product as Record<string, unknown> | undefined;
  const batch = codeData.batch as Record<string, unknown> | undefined;
  return (
    <div className="mx-auto max-w-md min-h-screen bg-canvas">
      <BatchStatusNotice
        status={batchStatus}
        batch={batch}
        recallWarning={recallWarning}
      />
      <BrandHeader
        name={brandName}
        logoUrl={brandLogo}
        primaryColor={primaryColor}
      />
      <ProductCard
        productName={productName}
        description={productDesc}
        imageUrl={productImage}
        image_url={(_product?.image_url as string) || ""}
        showBadge
      />
      <TraceabilitySection codeData={codeData} />
      {!benefitsBlocked && (
        <div className="px-4 pb-6">
          <LeadForm publicId={publicId} scanToken={scanToken} />
        </div>
      )}
      <FooterSection branding={{ name: brandName, logo_url: brandLogo }} />
    </div>
  );
}
