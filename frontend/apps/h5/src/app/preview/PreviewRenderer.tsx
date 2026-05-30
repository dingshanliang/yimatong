"use client";

import { useState, useEffect } from "react";
import { BrandHeader } from "@/components/BrandHeader";
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

type ModuleConfig = {
  id: string;
  type: string;
  enabled?: boolean;
  config?: Record<string, unknown>;
};

type PreviewConfig = {
  modules: ModuleConfig[];
  tenant_branding?: {
    name?: string;
    logo_url?: string;
    primary_color?: string;
  };
};

export function PreviewRenderer() {
  const [config, setConfig] = useState<PreviewConfig | null>(null);

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.data?.type === "preview-dsl") {
        setConfig(event.data.payload as PreviewConfig);
      }
    }
    window.addEventListener("message", handleMessage);
    window.parent.postMessage({ type: "preview-ready" }, "*");
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  if (!config) {
    return (
      <div className="flex min-h-screen items-center justify-center text-gray-400">
        等待编辑器数据...
      </div>
    );
  }

  const branding = config.tenant_branding || {};
  const enabledModules = config.modules.filter((m) => m.enabled !== false);

  return (
    <div className="mx-auto max-w-md min-h-screen bg-gray-50">
      <BrandHeader
        name={branding.name || "品牌预览"}
        logoUrl={branding.logo_url || ""}
        primaryColor={branding.primary_color}
      />
      {enabledModules.map((mod) => (
        <PreviewModule key={mod.id} module={mod} />
      ))}
      <FooterSection branding={{ name: branding.name, logo_url: branding.logo_url }} />
    </div>
  );
}

function PreviewModule({ module }: { module: ModuleConfig }) {
  const config = module.config || {};

  switch (module.type) {
    case "product_hero":
      return (
        <ProductCard
          productName={(config.title_template as string) || "产品名称预览"}
          description="产品描述预览"
          imageUrl={config.image_url as string}
          showBadge={config.show_verify_badge as boolean}
        />
      );
    case "verification_status":
      return (
        <div className="px-4 mt-3">
          <VerifyStatus status="first_scan" scanCount={1} />
        </div>
      );
    case "light_traceability":
      return (
        <TraceabilitySection
          codeData={{
            batch: { origin: "产地预览", production_date: "2026-01-01", batch_no: "BATCH001" },
            product: { name: "产品预览" },
          }}
          config={config as { fields?: string[] }}
        />
      );
    case "test_reports":
      return <TestReportSection reports={[]} />;
    case "certificates":
      return (
        <div className="mx-4 mt-3 rounded-2xl bg-white p-4 shadow-sm">
          <h2 className="text-base font-semibold text-gray-900">资质证书</h2>
          <p className="mt-2 text-sm text-gray-400">预览模式下显示示例证书</p>
        </div>
      );
    case "benefit_card":
      return (
        <div className="px-4 mt-3">
          <BenefitClaimCard
            benefitId={(config.benefit_id as string) || "preview"}
            benefitType={(config.benefit_type as "coupon" | "points" | "lottery" | "gift") || "coupon"}
            title={(config.title as string) || "领取权益"}
            description={config.description as string}
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
        <div className="mx-4 mt-3 rounded-2xl bg-white p-4 shadow-sm">
          <h2 className="text-base font-semibold text-gray-900">视频/图文</h2>
          <p className="mt-2 text-sm text-gray-400">预览模式下显示占位内容</p>
        </div>
      );
    case "legal_terms":
      return (
        <div className="px-4 mt-3 mb-4">
          {Boolean(config.show_privacy_policy) && <PrivacyPolicy publicId="preview" />}
          {Boolean(config.show_campaign_rules) && (
            <CampaignRules campaignName="示例活动" rules={{}} />
          )}
        </div>
      );
    case "custom_html":
      if (config.html) {
        return <div className="px-4 mt-3" dangerouslySetInnerHTML={{ __html: config.html as string }} />;
      }
      return null;
    case "member_card":
      return (
        <div className="px-4 mt-3">
          <MemberCard memberLevel={(config.member_level as string) || "bronze"} totalPoints={0} />
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
          <RiskAlert alertType={(config.alert_type as string) || "frequency"} detail={config.detail as string} />
        </div>
      );
    case "dual_code_verify":
      return (
        <div className="px-4 mt-3">
          <DualCodeVerify publicId="preview" codeType="standard" isFirstScan scanCount={1} />
        </div>
      );
    case "points_history":
      return (
        <div className="px-4 mt-3">
          <div className="rounded-2xl bg-white p-4 shadow-sm">
            <h3 className="text-sm font-semibold text-gray-900">积分明细</h3>
            <p className="mt-2 text-sm text-gray-400">预览模式下显示占位内容</p>
          </div>
        </div>
      );
    default:
      return (
        <div className="mx-4 mt-3 rounded-2xl bg-white p-4 shadow-sm text-center text-gray-400">
          未知模块类型: {module.type}
        </div>
      );
  }
}