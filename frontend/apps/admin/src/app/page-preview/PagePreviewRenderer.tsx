"use client";

/* eslint-disable @next/next/no-img-element */

import { useEffect, useMemo, useState } from "react";
import type { PageDSL, ModuleConfig, PagePreviewContext, PreviewAsset } from "@/lib/page-dsl";

type PreviewConfig = PageDSL & {
  tenant_branding?: {
    name?: string;
    logo_url?: string;
    primary_color?: string;
  };
};

const surface: React.CSSProperties = {
  minHeight: "100vh",
  background: "#f8fafc",
  color: "#111827",
  fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
};

const card: React.CSSProperties = {
  margin: "12px 16px 0",
  borderRadius: 16,
  background: "#ffffff",
  padding: 16,
  boxShadow: "0 8px 20px rgb(15 23 42 / 0.08)",
};

export function PagePreviewRenderer() {
  const [config, setConfig] = useState<PreviewConfig | null>(null);
  const [previewContext, setPreviewContext] = useState<PagePreviewContext>({});
  const [previewMode, setPreviewMode] = useState<"example" | "bound">("example");

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
    window.parent.postMessage({ type: "preview-ready" }, window.location.origin);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  const enabledModules = useMemo(
    () => (config?.modules || []).filter((module) => module.enabled !== false),
    [config?.modules],
  );

  if (!config) {
    return (
      <main style={{ ...surface, display: "grid", placeItems: "center", color: "#94a3b8" }}>
        等待编辑器数据...
      </main>
    );
  }

  const branding = config.tenant_branding || {};
  const brandName = branding.name || "一码通预览";

  return (
    <main style={surface}>
      <BrandHeader
        name={brandName}
        logoUrl={branding.logo_url}
        primaryColor={branding.primary_color}
      />
      <section style={{ margin: "10px 16px 0", fontSize: 12, color: previewMode === "bound" ? "#15803d" : "#2563eb" }}>
        {previewMode === "bound" ? "草稿预览 · 已绑定真实产品" : "草稿预览 · 示例数据"}
      </section>
      {enabledModules.length > 0 ? (
        enabledModules.map((module) => (
          <PreviewModule
            key={module.id}
            module={module}
            previewContext={previewContext}
            previewMode={previewMode}
          />
        ))
      ) : (
        <section style={{ ...card, textAlign: "center", color: "#94a3b8" }}>
          暂无模块，点击左侧「添加模块」开始配置。
        </section>
      )}
      <footer style={{ padding: "20px 16px 28px", textAlign: "center", color: "#94a3b8", fontSize: 12 }}>
        {brandName} · 扫码溯源服务
      </footer>
    </main>
  );
}

function BrandHeader({
  name,
  logoUrl,
  primaryColor,
}: {
  name: string;
  logoUrl?: string;
  primaryColor?: string;
}) {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: 16,
        background: primaryColor || "#2563eb",
        color: "#ffffff",
      }}
    >
      {logoUrl ? (
        <img
          src={logoUrl}
          alt={name}
          style={{ width: 40, height: 40, borderRadius: 999, objectFit: "cover", border: "2px solid rgb(255 255 255 / 0.35)" }}
        />
      ) : (
        <div
          style={{
            display: "grid",
            placeItems: "center",
            width: 40,
            height: 40,
            borderRadius: 999,
            background: "rgb(255 255 255 / 0.2)",
            fontWeight: 700,
            fontSize: 18,
          }}
        >
          {name.charAt(0) || "Y"}
        </div>
      )}
      <strong style={{ fontSize: 18 }}>{name}</strong>
    </header>
  );
}

function PreviewModule({
  module,
  previewContext,
  previewMode,
}: {
  module: ModuleConfig;
  previewContext: PagePreviewContext;
  previewMode: "example" | "bound";
}) {
  const config = module.config || {};
  const product = previewContext.product || null;
  const latestBatch = previewContext.batches?.[0];
  const assets = previewContext.assets || [];
  const isExample = previewMode !== "bound";

  switch (module.type) {
    case "product_hero":
      return (
        <section style={card}>
          {(typeof config.image_url === "string" && config.image_url) || product?.image_url ? (
            <img
              src={(config.image_url as string) || product?.image_url}
              alt="产品展示"
              style={{ width: "100%", height: 192, objectFit: "cover", borderRadius: 12, marginBottom: 12 }}
            />
          ) : null}
          <h1 style={{ margin: 0, fontSize: 22, lineHeight: "30px", fontWeight: 800 }}>
            {(config.title_template as string) || product?.name || "产品名称预览"} {isExample ? <InlineFlag /> : null}
          </h1>
          <p style={{ margin: "6px 0 0", color: "#64748b", fontSize: 14, lineHeight: "22px" }}>
            {(config.description as string) || product?.description || product?.story_content || "这里展示产品介绍、产地和关键卖点。"}
          </p>
          {config.show_verify_badge ? (
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <Badge color="#16a34a" background="#dcfce7">正品保障</Badge>
              <Badge color="#2563eb" background="#dbeafe">已验证</Badge>
            </div>
          ) : null}
        </section>
      );
    case "verification_status":
      return (
        <section style={{ ...card, border: "1px solid #bbf7d0", background: "#f0fdf4" }}>
          <strong style={{ display: "block", color: "#166534", fontSize: 17 }}>验证通过</strong>
          <p style={{ margin: "6px 0 0", color: "#15803d", fontSize: 14 }}>
            {isExample ? "示例扫码结果：首次扫码验证，产品为正品。" : "首次扫码验证时，消费者将在这里看到验真结果。"}
          </p>
        </section>
      );
    case "light_traceability": {
      const fields = (config.fields as string[]) || ["origin", "production_date", "batch_no"];
      const values: Record<string, string> = {
        origin: latestBatch?.origin || product?.origin || "产地预览",
        production_date: latestBatch?.production_date || "2026-01-01",
        expiry_date: latestBatch?.expiry_date || "2027-01-01",
        batch_no: latestBatch?.batch_code || "BATCH001",
      };
      const labels: Record<string, string> = {
        origin: "产地",
        production_date: "生产日期",
        expiry_date: "保质期至",
        batch_no: "批次号",
      };
      return (
        <section style={card}>
          <h2 style={{ margin: 0, fontSize: 17 }}>溯源信息</h2>
          {isExample || !latestBatch ? <p style={{ margin: "6px 0 0", color: "#2563eb", fontSize: 12 }}>以下为示例批次数据</p> : null}
          <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
            {fields.map((field) => (
              <div key={field} style={{ display: "flex", justifyContent: "space-between", fontSize: 14 }}>
                <span style={{ color: "#64748b" }}>{labels[field] || field}</span>
                <span style={{ color: "#111827", fontWeight: 600 }}>{values[field] || "-"}</span>
              </div>
            ))}
          </div>
        </section>
      );
    }
    case "test_reports":
      return <AssetCard title="检测报告" assets={filterAssets(assets, "test_report", config.report_ids)} emptyBody="预览模式下显示关联检测报告入口。" />;
    case "certificates":
      return <AssetCard title="资质证书" assets={filterAssets(assets, "certificate", config.certificate_ids)} emptyBody="预览模式下显示关联证书入口。" />;
    case "benefit_card":
      return (
        <section style={{ ...card, background: "linear-gradient(135deg, #fff7ed, #ffffff)" }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>{(config.title as string) || "领取权益"}</h2>
          <p style={{ margin: "8px 0 12px", color: "#9a3412", fontSize: 14 }}>
            {(config.description as string) || "扫码后可领取优惠、积分或活动权益。"}
          </p>
          <button type="button" style={primaryButton}>立即领取</button>
        </section>
      );
    case "cta_group": {
      const buttons = (config.buttons as Array<{ label?: string }>) || [];
      return (
        <section style={card}>
          <div style={{ display: "grid", gap: 8 }}>
            {(buttons.length ? buttons : [{ label: "联系客服" }]).map((button, index) => (
              <button key={`${button.label || "button"}-${index}`} type="button" style={secondaryButton}>
                {button.label || "行动按钮"}
              </button>
            ))}
          </div>
        </section>
      );
    }
    case "shop_redirect":
      return <SimpleCard title="购买渠道" body="展示淘宝、京东、抖音等购买入口。" />;
    case "lead_form":
      return <SimpleCard title={(config.title as string) || "填写信息"} body="姓名、手机号等留资字段将在这里展示。" />;
    case "media_section":
      return <AssetCard title="视频/图文" assets={filterAssets(assets, ["image", "video", "story"], config.asset_ids)} emptyBody="展示品牌故事、产地环境或生产过程素材。" />;
    case "legal_terms":
      return <SimpleCard title="法律条款" body="隐私政策、活动规则和合规说明。" />;
    case "custom_html":
      return (
        <section
          style={card}
          dangerouslySetInnerHTML={{ __html: (config.html as string) || "<p>自定义 HTML 预览</p>" }}
        />
      );
    case "member_card":
      return <SimpleCard title="会员卡片" body="展示会员等级和会员权益。" />;
    case "points_balance":
      return <SimpleCard title="积分余额" body={`${(config.points as number) || 0} 积分`} />;
    case "points_exchange":
      return <SimpleCard title={(config.title as string) || "积分兑换"} body={`消耗 ${(config.points_cost as number) || 100} 积分兑换权益。`} />;
    case "points_shop":
      return <SimpleCard title="积分商城" body="展示可兑换商品、积分价格、库存和兑换状态。" />;
    case "outer_code_guide":
      return <SimpleCard title="外码引导" body="引导消费者开箱后继续验证内码。" />;
    case "risk_alert":
      return <SimpleCard title="风险预警" body={(config.detail as string) || "频繁扫码等异常情况将在这里提示。"} />;
    case "dual_code_verify":
      return <SimpleCard title="双码验真" body="外码与内码联合验证流程预览。" />;
    case "points_history":
      return <SimpleCard title="积分明细" body="展示积分获取与消耗记录。" />;
    default:
      return <SimpleCard title="未知模块" body={`模块类型：${module.type}`} />;
  }
}

function filterAssets(assets: PreviewAsset[], assetType: string | string[], selectedIds: unknown) {
  const types = Array.isArray(assetType) ? assetType : [assetType];
  const ids = Array.isArray(selectedIds) ? selectedIds.map(String) : [];
  return assets.filter((asset) => types.includes(asset.asset_type) && (ids.length === 0 || ids.includes(asset.id)));
}

function AssetCard({
  title,
  assets,
  emptyBody,
}: {
  title: string;
  assets: PreviewAsset[];
  emptyBody: string;
}) {
  if (assets.length === 0) {
    return <SimpleCard title={title} body={`${emptyBody}（示例）`} />;
  }
  return (
    <section style={card}>
      <h2 style={{ margin: 0, fontSize: 17 }}>{title}</h2>
      <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
        {assets.slice(0, 3).map((asset) => (
          <div key={asset.id} style={{ borderRadius: 10, background: "#f8fafc", padding: "8px 10px" }}>
            <strong style={{ display: "block", fontSize: 14 }}>{asset.name}</strong>
            <span style={{ color: "#64748b", fontSize: 12 }}>
              {asset.issuer || asset.description || asset.valid_until || "已关联产品资料"}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function InlineFlag() {
  return (
    <span style={{ color: "#2563eb", fontSize: 12, fontWeight: 600, verticalAlign: "middle" }}>
      示例
    </span>
  );
}

function SimpleCard({ title, body }: { title: string; body: string }) {
  return (
    <section style={card}>
      <h2 style={{ margin: 0, fontSize: 17 }}>{title}</h2>
      <p style={{ margin: "8px 0 0", color: "#64748b", fontSize: 14, lineHeight: "22px" }}>{body}</p>
    </section>
  );
}

function Badge({
  children,
  color,
  background,
}: {
  children: React.ReactNode;
  color: string;
  background: string;
}) {
  return (
    <span style={{ borderRadius: 999, background, color, padding: "2px 10px", fontSize: 12, fontWeight: 600 }}>
      {children}
    </span>
  );
}

const primaryButton: React.CSSProperties = {
  width: "100%",
  border: 0,
  borderRadius: 12,
  background: "#ea580c",
  color: "#ffffff",
  padding: "10px 12px",
  fontSize: 15,
  fontWeight: 700,
};

const secondaryButton: React.CSSProperties = {
  width: "100%",
  border: "1px solid #bfdbfe",
  borderRadius: 12,
  background: "#eff6ff",
  color: "#1d4ed8",
  padding: "10px 12px",
  fontSize: 15,
  fontWeight: 700,
};
