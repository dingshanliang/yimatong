# 页面 DSL 可视化编辑器 实施计划

> **Status:** ✅ Completed
> **Completed date:** 2026-06 (estimated)
> **Evidence:** `frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/` — full dnd-kit editor with preview panel

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 pages 模块从单文件 Drawer 编辑器重构为全屏拖拽编辑器 + iframe 实时预览的分屏布局

**Architecture:** 全屏编辑页 `/pages/[id]/edit`，左侧 dnd-kit 拖拽模块列表 + 属性配置，右侧 iframe 嵌入 H5 `/preview` 路由通过 postMessage 实时通信。模板列表和版本管理拆分为独立路由页。

**Tech Stack:** Next.js 16 App Router, Ant Design 6, dnd-kit, Tailwind CSS 4 (H5), postMessage API

---

## File Structure

### Admin 端（创建/修改）

```
apps/admin/src/app/(dashboard)/pages/
  page.tsx                                    # [修改] 精简为纯列表，添加"编辑"按钮
  [id]/
    page.tsx                                  # [新建] 版本管理独立页面
    edit/
      page.tsx                                # [新建] 全屏编辑器主容器
      components/
        EditorHeader.tsx                      # [新建] 顶部导航栏
        ModuleList.tsx                        # [新建] dnd-kit 拖拽模块列表
        ModuleItem.tsx                        # [新建] 单个模块（手柄+开关+属性面板）
        ModuleConfigForms.tsx                 # [新建] 各模块类型配置表单（从 page.tsx 迁移）
        RoutingConfig.tsx                     # [新建] 活动期路由配置（从 page.tsx 迁移）
        PreviewPanel.tsx                      # [新建] iframe 预览面板
```

### H5 端（新增）

```
apps/h5/src/app/preview/
  page.tsx                                    # [新建] 预览页面，监听 postMessage 接收 DSL
  PreviewRenderer.tsx                         # [新建] 复用现有组件渲染 DSL 模块
```

### 共享类型

```
apps/admin/src/lib/page-dsl.ts               # [不变] 已有完整类型定义
```

---

## Task 1: 安装 dnd-kit 依赖

**Files:**
- Modify: `frontend/apps/admin/package.json`

- [ ] **Step 1: 安装 @dnd-kit 依赖**

```bash
cd /Users/ericding/code/agriculture/yimatong/frontend/apps/admin && pnpm add @dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities
```

- [ ] **Step 2: 验证安装成功**

```bash
cd /Users/ericding/code/agriculture/yimatong/frontend && pnpm ls @dnd-kit/core --filter @yimatong/admin
```

Expected: 显示 `@dnd-kit/core` 版本号

- [ ] **Step 3: Commit**

```bash
git add apps/admin/package.json apps/admin/pnpm-lock.yaml
git commit -m "chore(admin): add @dnd-kit dependencies for page editor drag-and-drop"
```

---

## Task 2: 创建 H5 预览页面（`/preview`）

这个任务先做，因为后续 Admin 端编辑器需要 iframe 指向这个页面。

**Files:**
- Create: `frontend/apps/h5/src/app/preview/page.tsx`
- Create: `frontend/apps/h5/src/app/preview/PreviewRenderer.tsx`

- [ ] **Step 1: 创建 PreviewRenderer 组件**

这个组件复用现有 H5 消费者端组件，接收 DSL 配置渲染模块列表。核心逻辑从 `ResolveContent.tsx` 的 `ModuleRenderer` 和 `DefaultRender` 部分提取。

```tsx
// frontend/apps/h5/src/app/preview/PreviewRenderer.tsx
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
    // 通知父窗口已准备好接收数据
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
    default:
      return (
        <div className="mx-4 mt-3 rounded-2xl bg-white p-4 shadow-sm text-center text-gray-400">
          未知模块类型: {module.type}
        </div>
      );
  }
}
```

- [ ] **Step 2: 创建 preview 页面入口**

```tsx
// frontend/apps/h5/src/app/preview/page.tsx
import { PreviewRenderer } from "./PreviewRenderer";

export default function PreviewPage() {
  return <PreviewRenderer />;
}
```

- [ ] **Step 3: 验证 H5 预览页面可启动**

```bash
cd /Users/ericding/code/agriculture/yimatong/frontend && pnpm dev:h5
```

在浏览器打开 `http://localhost:3001/preview`，应显示"等待编辑器数据..."

- [ ] **Step 4: Commit**

```bash
git add apps/h5/src/app/preview/
git commit -m "feat(h5): add /preview page for admin editor live preview via postMessage"
```

---

## Task 3: 拆分现有 page.tsx — 模块配置表单（ModuleConfigForms）

将现有 `pages/page.tsx` 中的 `ModuleConfigForm` 函数提取为独立组件。这是纯代码搬迁，不改变行为。

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/components/ModuleConfigForms.tsx`
- Reference: `frontend/apps/admin/src/app/(dashboard)/pages/page.tsx:781-1086`

- [ ] **Step 1: 创建 ModuleConfigForms.tsx**

将 `page.tsx:781-1086` 的 `ModuleConfigForm` 组件原样复制到新文件，并补充 import。组件签名不变。

```tsx
// frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/components/ModuleConfigForms.tsx
"use client";

import { Input, InputNumber, Select, Space, Switch, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { Button, Popconfirm } from "antd";

const { Text } = Typography;
const { TextArea } = Input;

// 从 page.tsx:781-1086 原样复制 ModuleConfigForm 组件
// 组件签名：
// function ModuleConfigForm({
//   moduleType, config, onChange,
// }: {
//   moduleType: string;
//   config: Record<string, unknown>;
//   onChange: (config: Record<string, unknown>) => void;
// })

export function ModuleConfigForm({
  moduleType,
  config,
  onChange,
}: {
  moduleType: string;
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  const update = (key: string, value: unknown) => {
    onChange({ ...config, [key]: value });
  };

  switch (moduleType) {
    case "product_hero":
      return (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Switch size="small" checked={!!config.show_verify_badge} onChange={(v) => update("show_verify_badge", v)} />
            <Text type="secondary" className="text-xs">显示验真徽章</Text>
          </div>
          <Input size="small" placeholder="产品图片 URL（可选，留空使用产品数据）" value={String(config.image_url || "")} onChange={(e) => update("image_url", e.target.value)} />
          <Input size="small" placeholder="标题模板，如 {{product.name}}" value={String(config.title_template || "")} onChange={(e) => update("title_template", e.target.value)} />
        </div>
      );

    case "verification_status":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="首次扫码提示语" value={String(config.first_scan_text || "")} onChange={(e) => update("first_scan_text", e.target.value)} />
          <Input size="small" placeholder="重复扫码提示语" value={String(config.repeat_scan_text || "")} onChange={(e) => update("repeat_scan_text", e.target.value)} />
          <Input size="small" placeholder="无效码提示语" value={String(config.invalid_text || "")} onChange={(e) => update("invalid_text", e.target.value)} />
        </div>
      );

    case "light_traceability": {
      const allFields = [
        { value: "origin", label: "产地" },
        { value: "production_date", label: "生产日期" },
        { value: "expiry_date", label: "保质期至" },
        { value: "batch_no", label: "批次号" },
      ];
      return (
        <div className="space-y-2">
          <Text type="secondary" className="text-xs">显示字段</Text>
          <Select mode="multiple" size="small" placeholder="选择要显示的溯源字段"
            value={(config.fields as string[]) || []} onChange={(v) => update("fields", v)}
            options={allFields} style={{ width: "100%" }} />
        </div>
      );
    }

    case "test_reports":
      return (
        <div className="space-y-2">
          <Text type="secondary" className="text-xs">报告 ID 列表</Text>
          <Select mode="tags" size="small" placeholder="输入报告 ID 后回车添加"
            value={((config.report_ids as string[]) || []).map(String)}
            onChange={(v) => update("report_ids", v)} style={{ width: "100%" }} open={false} />
        </div>
      );

    case "certificates":
      return (
        <div className="space-y-2">
          <Text type="secondary" className="text-xs">证书配置（JSON 数组）</Text>
          <TextArea size="small" rows={3}
            value={JSON.stringify(config.certificates || [], null, 0)}
            onChange={(e) => { try { update("certificates", JSON.parse(e.target.value)); } catch { /* ignore */ } }} />
        </div>
      );

    case "benefit_card":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="权益 ID" value={String(config.benefit_id || "")} onChange={(e) => update("benefit_id", e.target.value)} />
          <Select size="small" placeholder="权益类型" value={config.benefit_type || undefined} onChange={(v) => update("benefit_type", v)}
            options={[{ value: "coupon", label: "优惠券" }, { value: "points", label: "积分" }, { value: "lottery", label: "抽奖" }, { value: "gift", label: "礼品" }]}
            style={{ width: 120 }} />
          <Input size="small" placeholder="标题" value={String(config.title || "")} onChange={(e) => update("title", e.target.value)} />
          <Input size="small" placeholder="描述" value={String(config.description || "")} onChange={(e) => update("description", e.target.value)} />
          <div className="flex items-center gap-2">
            <Switch size="small" checked={!!config.require_consent} onChange={(v) => update("require_consent", v)} />
            <Text type="secondary" className="text-xs">需要隐私授权</Text>
          </div>
        </div>
      );

    case "cta_group":
      return (
        <div className="space-y-1">
          <Text type="secondary" className="text-xs">按钮配置（JSON）</Text>
          <TextArea size="small" rows={3} value={JSON.stringify(config.buttons || [], null, 0)}
            onChange={(e) => { try { update("buttons", JSON.parse(e.target.value)); } catch { /* ignore */ } }} />
        </div>
      );

    case "shop_redirect": {
      const shops = (config.shops as Array<{ platform: string; name: string; url: string }>) || [];
      const addShop = () => update("shops", [{ platform: "taobao", name: "", url: "" }, ...shops]);
      const updateShop = (i: number, field: string, val: string) => {
        const updated = shops.map((s, idx) => idx === i ? { ...s, [field]: val } : s);
        update("shops", updated);
      };
      const removeShop = (i: number) => update("shops", shops.filter((_, idx) => idx !== i));
      return (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Text type="secondary" className="text-xs">购买渠道列表</Text>
            <Button size="small" icon={<PlusOutlined />} onClick={addShop}>添加渠道</Button>
          </div>
          {shops.map((shop, i) => (
            <div key={i} className="flex items-center gap-1">
              <Select size="small" value={shop.platform} onChange={(v) => updateShop(i, "platform", v)}
                options={[
                  { value: "taobao", label: "淘宝" }, { value: "jd", label: "京东" },
                  { value: "douyin", label: "抖音" }, { value: "pdd", label: "拼多多" },
                  { value: "other", label: "其他" },
                ]} style={{ width: 80 }} />
              <Input size="small" placeholder="渠道名称" value={shop.name} onChange={(e) => updateShop(i, "name", e.target.value)} style={{ flex: 1 }} />
              <Input size="small" placeholder="链接" value={shop.url} onChange={(e) => updateShop(i, "url", e.target.value)} style={{ flex: 1 }} />
              <Button size="small" danger onClick={() => removeShop(i)}>×</Button>
            </div>
          ))}
          {shops.length === 0 && <Text type="secondary" className="text-xs">暂未配置渠道</Text>}
        </div>
      );
    }

    case "lead_form": {
      const allFormFields = [
        { value: "name", label: "姓名" }, { value: "phone", label: "手机号" },
        { value: "address", label: "地址" }, { value: "email", label: "邮箱" },
        { value: "remark", label: "备注" },
      ];
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="表单标题" value={String(config.title || "")} onChange={(e) => update("title", e.target.value)} />
          <Input size="small" placeholder="副标题" value={String(config.subtitle || "")} onChange={(e) => update("subtitle", e.target.value)} />
          <Input size="small" placeholder="提交按钮文案" value={String(config.submit_label || "")} onChange={(e) => update("submit_label", e.target.value)} />
          <Text type="secondary" className="text-xs">表单字段</Text>
          <Select mode="multiple" size="small" placeholder="选择需要收集的字段"
            value={(config.fields as string[]) || []} onChange={(v) => update("fields", v)}
            options={allFormFields} style={{ width: "100%" }} />
        </div>
      );
    }

    case "media_section":
      return (
        <div className="space-y-2">
          <Text type="secondary" className="text-xs">媒体内容配置（JSON 数组）</Text>
          <TextArea size="small" rows={3} value={JSON.stringify(config.items || [], null, 0)}
            onChange={(e) => { try { update("items", JSON.parse(e.target.value)); } catch { /* ignore */ } }} />
        </div>
      );

    case "legal_terms":
      return (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Switch size="small" checked={!!config.show_privacy_policy} onChange={(v) => update("show_privacy_policy", v)} />
            <Text type="secondary" className="text-xs">显示隐私政策</Text>
          </div>
          <div className="flex items-center gap-2">
            <Switch size="small" checked={!!config.show_campaign_rules} onChange={(v) => update("show_campaign_rules", v)} />
            <Text type="secondary" className="text-xs">显示活动规则</Text>
          </div>
          <Input size="small" placeholder="隐私政策内容（可选）" value={String(config.privacy_content || "")} onChange={(e) => update("privacy_content", e.target.value)} />
        </div>
      );

    case "custom_html":
      return (
        <div className="space-y-2">
          <Text type="secondary" className="text-xs">自定义 HTML 内容</Text>
          <TextArea size="small" rows={5} value={String(config.html || "")}
            onChange={(e) => update("html", e.target.value)} placeholder="<div>...</div>" className="font-mono text-xs" />
        </div>
      );

    case "member_card":
      return (
        <div className="space-y-2">
          <Select size="small" placeholder="会员等级" value={config.member_level || undefined} onChange={(v) => update("member_level", v)}
            options={[{ value: "bronze", label: "青铜" }, { value: "silver", label: "白银" }, { value: "gold", label: "黄金" }, { value: "diamond", label: "钻石" }]}
            style={{ width: 120 }} />
        </div>
      );

    case "points_balance":
      return (
        <div className="space-y-2">
          <InputNumber size="small" placeholder="初始积分" min={0} value={Number(config.points) || undefined} onChange={(v) => update("points", v)} />
        </div>
      );

    case "points_exchange":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="权益 ID" value={String(config.benefit_id || "")} onChange={(e) => update("benefit_id", e.target.value)} />
          <Input size="small" placeholder="标题" value={String(config.title || "")} onChange={(e) => update("title", e.target.value)} />
          <InputNumber size="small" placeholder="所需积分" min={0} value={Number(config.points_cost) || undefined} onChange={(v) => update("points_cost", v)} />
          <Input size="small" placeholder="描述" value={String(config.description || "")} onChange={(e) => update("description", e.target.value)} />
        </div>
      );

    case "outer_code_guide":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="品牌名称" value={String(config.brand_name || "")} onChange={(e) => update("brand_name", e.target.value)} />
          <Input size="small" placeholder="产品名称" value={String(config.product_name || "")} onChange={(e) => update("product_name", e.target.value)} />
          <Input size="small" placeholder="内码提示文案" value={String(config.inner_code_hint || "")} onChange={(e) => update("inner_code_hint", e.target.value)} />
          <Input size="small" placeholder="产品图片 URL" value={String(config.product_image || "")} onChange={(e) => update("product_image", e.target.value)} />
        </div>
      );

    case "risk_alert":
      return (
        <div className="space-y-2">
          <Select size="small" placeholder="预警类型" value={config.alert_type || undefined} onChange={(v) => update("alert_type", v)}
            options={[{ value: "frequency", label: "频率限制" }, { value: "multi_location", label: "多地扫码" }, { value: "suspected_copy", label: "疑似复制码" }]}
            style={{ width: 140 }} />
          <Input size="small" placeholder="详情" value={String(config.detail || "")} onChange={(e) => update("detail", e.target.value)} />
        </div>
      );

    case "dual_code_verify":
      return (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Switch size="small" checked={!!config.product_verified} onChange={(v) => update("product_verified", v)} />
            <Text type="secondary" className="text-xs">标记产品已验证</Text>
          </div>
        </div>
      );

    default:
      return <Text type="secondary" className="text-xs italic">此模块无可配置项</Text>;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/admin/src/app/\(dashboard\)/pages/\[id\]/edit/components/ModuleConfigForms.tsx
git commit -m "refactor(admin): extract ModuleConfigForms to standalone component"
```

---

## Task 4: 拆分 — 活动期路由配置（RoutingConfig）

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/components/RoutingConfig.tsx`

- [ ] **Step 1: 创建 RoutingConfig.tsx**

从 `page.tsx:657-777` 的 `RoutingEditor` 提取。改造为受控组件，接收 `routing` 和 `onChange` props。

```tsx
// frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/components/RoutingConfig.tsx
"use client";

import { useState, useEffect } from "react";
import { Button, DatePicker, Input, Select, Space, Switch, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { Popconfirm } from "antd";
import dayjs from "dayjs";
import type { PageDSL, CampaignPeriod } from "@/lib/page-dsl";

const { Text } = Typography;

export function RoutingConfig({
  dsl,
  onChange,
}: {
  dsl: PageDSL;
  onChange: (dsl: PageDSL) => void;
}) {
  const routing = dsl.routing;
  const periods = routing?.campaign_periods ?? [];

  const updateRouting = (updates: Partial<PageDSL["routing"]>) => {
    const newRouting = { ...routing, ...updates };
    onChange({ ...dsl, routing: newRouting });
  };

  const addPeriod = () => {
    const newPeriods: CampaignPeriod[] = [
      { campaign_id: "", start_at: "", end_at: "", mode: "campaign" },
      ...periods,
    ];
    updateRouting({ campaign_periods: newPeriods });
  };

  const updatePeriod = (index: number, updates: Record<string, unknown>) => {
    const newPeriods = periods.map((p, i) => (i === index ? { ...p, ...updates } : p));
    updateRouting({ campaign_periods: newPeriods });
  };

  const removePeriod = (index: number) => {
    updateRouting({ campaign_periods: periods.filter((_, i) => i !== index) });
  };

  return (
    <div>
      <div className="mb-3">
        <div className="mb-2 flex items-center gap-2">
          <Switch
            size="small"
            checked={routing?.default_page ?? true}
            onChange={(checked) => updateRouting({ default_page: checked })}
          />
          <Text>设为默认页面</Text>
        </div>
      </div>

      <div className="mb-2 flex items-center justify-between">
        <Text strong>活动期配置</Text>
        <Button size="small" icon={<PlusOutlined />} onClick={addPeriod}>
          添加活动期
        </Button>
      </div>

      <div className="space-y-3">
        {periods.map((period, i) => (
          <div key={i} className="rounded border p-3">
            <div className="flex items-center gap-2 mb-2">
              <Tag color={period.mode === "evergreen" ? "green" : "blue"}>
                {period.mode === "evergreen" ? "常驻" : "活动期"}
              </Tag>
              <Select
                size="small"
                value={period.mode}
                onChange={(mode) => updatePeriod(i, { mode })}
                options={[
                  { value: "evergreen", label: "常驻（非活动期）" },
                  { value: "campaign", label: "活动期" },
                ]}
                style={{ width: 160 }}
              />
              <div className="flex-1" />
              <Button size="small" danger onClick={() => removePeriod(i)}>
                删除
              </Button>
            </div>
            {period.mode === "campaign" && (
              <div className="space-y-2">
                <Input
                  size="small"
                  placeholder="活动 ID（可选）"
                  value={period.campaign_id || ""}
                  onChange={(e) => updatePeriod(i, { campaign_id: e.target.value })}
                />
                <Space>
                  <DatePicker
                    size="small"
                    placeholder="开始时间"
                    value={period.start_at ? dayjs(period.start_at) : undefined}
                    onChange={(d) => updatePeriod(i, { start_at: d?.toISOString() || "" })}
                  />
                  <DatePicker
                    size="small"
                    placeholder="结束时间"
                    value={period.end_at ? dayjs(period.end_at) : undefined}
                    onChange={(d) => updatePeriod(i, { end_at: d?.toISOString() || "" })}
                  />
                </Space>
              </div>
            )}
          </div>
        ))}
        {periods.length === 0 && (
          <div className="py-8 text-center text-gray-400">暂无活动期配置</div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/admin/src/app/\(dashboard\)/pages/\[id\]/edit/components/RoutingConfig.tsx
git commit -m "refactor(admin): extract RoutingConfig to standalone component"
```

---

## Task 5: 创建拖拽模块列表组件（ModuleList + ModuleItem）

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/components/ModuleList.tsx`
- Create: `frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/components/ModuleItem.tsx`

- [ ] **Step 1: 创建 ModuleList.tsx**

```tsx
// frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/components/ModuleList.tsx
"use client";

import { useState } from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { Button, Popconfirm } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { MODULE_TYPES, type ModuleConfig, type ModuleType } from "@/lib/page-dsl";
import { ModuleItem } from "./ModuleItem";

export function ModuleList({
  modules,
  onChange,
}: {
  modules: ModuleConfig[];
  onChange: (modules: ModuleConfig[]) => void;
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const oldIndex = modules.findIndex((m) => m.id === active.id);
    const newIndex = modules.findIndex((m) => m.id === over.id);
    onChange(arrayMove(modules, oldIndex, newIndex));
  };

  const addModule = () => {
    const id = `mod_${Date.now()}`;
    const newMod: ModuleConfig = { id, type: "product_hero", enabled: true, config: {} };
    onChange([newMod, ...modules]);
    setExpandedId(id);
  };

  const updateModule = (id: string, updates: Partial<ModuleConfig>) => {
    onChange(modules.map((m) => (m.id === id ? { ...m, ...updates } : m)));
  };

  const removeModule = (id: string) => {
    onChange(modules.filter((m) => m.id !== id));
    if (expandedId === id) setExpandedId(null);
  };

  return (
    <div>
      <div className="mb-3 flex justify-end">
        <Button size="small" icon={<PlusOutlined />} onClick={addModule}>
          添加模块
        </Button>
      </div>

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleDragEnd}
      >
        <SortableContext
          items={modules.map((m) => m.id)}
          strategy={verticalListSortingStrategy}
        >
          <div className="space-y-2">
            {modules.map((mod) => (
              <ModuleItem
                key={mod.id}
                module={mod}
                expanded={expandedId === mod.id}
                onToggleExpand={() =>
                  setExpandedId(expandedId === mod.id ? null : mod.id)
                }
                onUpdate={(updates) => updateModule(mod.id, updates)}
                onRemove={() => removeModule(mod.id)}
              />
            ))}
          </div>
        </SortableContext>
      </DndContext>

      {modules.length === 0 && (
        <div className="py-8 text-center text-gray-400">
          暂无模块，点击"添加模块"开始
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 创建 ModuleItem.tsx**

```tsx
// frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/components/ModuleItem.tsx
"use client";

import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { HolderOutlined } from "@ant-design/icons";
import { Button, Popconfirm, Select, Switch, Typography } from "antd";
import { MODULE_TYPES, type ModuleConfig, type ModuleType } from "@/lib/page-dsl";
import { ModuleConfigForm } from "./ModuleConfigForms";

const { Text } = Typography;

export function ModuleItem({
  module,
  expanded,
  onToggleExpand,
  onUpdate,
  onRemove,
}: {
  module: ModuleConfig;
  expanded: boolean;
  onToggleExpand: () => void;
  onUpdate: (updates: Partial<ModuleConfig>) => void;
  onRemove: () => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: module.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  const typeLabel = MODULE_TYPES.find((t) => t.value === module.type)?.label || module.type;

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`rounded border transition-colors ${
        module.enabled ? "bg-white" : "bg-gray-50 opacity-60"
      } ${expanded ? "ring-2 ring-blue-200" : ""}`}
    >
      {/* 模块头部行 */}
      <div className="flex items-center gap-2 p-3 cursor-pointer" onClick={onToggleExpand}>
        <span
          {...attributes}
          {...listeners}
          className="cursor-grab text-gray-400 hover:text-gray-600 active:cursor-grabbing"
        >
          <HolderOutlined />
        </span>
        <Switch
          size="small"
          checked={module.enabled}
          onChange={(checked) => onUpdate({ enabled: checked })}
          onClick={(_, e) => e.stopPropagation()}
        />
        <span className="text-sm font-medium">{typeLabel}</span>
        <Text type="secondary" className="text-xs">{module.id}</Text>
        <div className="flex-1" />
        <Popconfirm title="删除此模块？" onConfirm={onRemove} onCancel={(e) => e?.stopPropagation()}>
          <Button
            size="small"
            danger
            type="text"
            onClick={(e) => e.stopPropagation()}
          >
            删除
          </Button>
        </Popconfirm>
      </div>

      {/* 展开的配置面板 */}
      {expanded && (
        <div className="border-t px-3 pb-3 pt-2">
          <div className="mb-2 flex gap-2">
            <Select
              size="small"
              value={module.type}
              onChange={(type: ModuleType) => onUpdate({ type })}
              options={MODULE_TYPES}
              style={{ width: 140 }}
            />
          </div>
          <div className="rounded bg-gray-50 p-2">
            <ModuleConfigForm
              moduleType={module.type}
              config={module.config || {}}
              onChange={(config) => onUpdate({ config })}
            />
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add apps/admin/src/app/\(dashboard\)/pages/\[id\]/edit/components/ModuleList.tsx apps/admin/src/app/\(dashboard\)/pages/\[id\]/edit/components/ModuleItem.tsx
git commit -m "feat(admin): add ModuleList and ModuleItem with dnd-kit drag-and-drop"
```

---

## Task 6: 创建预览面板组件（PreviewPanel）

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/components/PreviewPanel.tsx`

- [ ] **Step 1: 创建 PreviewPanel.tsx**

```tsx
// frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/components/PreviewPanel.tsx
"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { Button, Select, Slider, Typography } from "antd";
import {
  MobileOutlined,
  DesktopOutlined,
} from "@ant-design/icons";
import type { PageDSL } from "@/lib/page-dsl";

const { Text } = Typography;

type DevicePreset = {
  label: string;
  width: number;
  height: number;
  icon: React.ReactNode;
};

const DEVICE_PRESETS: Record<string, DevicePreset> = {
  iphone15: { label: "iPhone 15", width: 393, height: 852, icon: <MobileOutlined /> },
  iphone_se: { label: "iPhone SE", width: 375, height: 667, icon: <MobileOutlined /> },
  desktop: { label: "桌面", width: 1024, height: 768, icon: <DesktopOutlined /> },
};

export function PreviewPanel({ dsl }: { dsl: PageDSL }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [device, setDevice] = useState("iphone15");
  const [scale, setScale] = useState(75);
  const [ready, setReady] = useState(false);

  const preset = DEVICE_PRESETS[device];
  const h5Url = process.env.NEXT_PUBLIC_H5_URL || "http://localhost:3001";

  // 发送 DSL 到 iframe
  const sendDSL = useCallback(() => {
    if (!iframeRef.current?.contentWindow || !ready) return;
    iframeRef.current.contentWindow.postMessage(
      { type: "preview-dsl", payload: dsl },
      h5Url,
    );
  }, [dsl, ready, h5Url]);

  useEffect(() => {
    sendDSL();
  }, [sendDSL]);

  // 监听 iframe ready
  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.data?.type === "preview-ready") {
        setReady(true);
      }
    }
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  // iframe ready 后立即发送当前 DSL
  useEffect(() => {
    if (ready) sendDSL();
  }, [ready, sendDSL]);

  return (
    <div className="flex h-full flex-col items-center">
      {/* 设备切换 & 缩放控制 */}
      <div className="mb-3 flex w-full items-center justify-between border-b pb-2">
        <Select
          size="small"
          value={device}
          onChange={setDevice}
          options={Object.entries(DEVICE_PRESETS).map(([key, p]) => ({
            value: key,
            label: (
              <span className="flex items-center gap-1">
                {p.icon} {p.label}
              </span>
            ),
          }))}
          style={{ width: 150 }}
        />
        <div className="flex items-center gap-2">
          <Text type="secondary" className="text-xs">缩放</Text>
          <Slider
            min={50}
            max={100}
            value={scale}
            onChange={setScale}
            style={{ width: 100 }}
          />
          <Text type="secondary" className="text-xs">{scale}%</Text>
        </div>
      </div>

      {/* 手机外壳 */}
      <div
        className="flex-1 flex items-start justify-center overflow-auto"
        style={{ padding: "8px" }}
      >
        <div
          style={{
            width: preset.width * (scale / 100) + 24,
            height: preset.height * (scale / 100) + 24,
            border: "3px solid #333",
            borderRadius: 24,
            padding: 8,
            background: "#fff",
            overflow: "hidden",
          }}
        >
          <iframe
            ref={iframeRef}
            src={`${h5Url}/preview`}
            style={{
              width: preset.width,
              height: preset.height,
              border: "none",
              transform: `scale(${scale / 100})`,
              transformOrigin: "top left",
            }}
            title="H5 预览"
          />
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/admin/src/app/\(dashboard\)/pages/\[id\]/edit/components/PreviewPanel.tsx
git commit -m "feat(admin): add PreviewPanel with iframe live preview and device switching"
```

---

## Task 7: 创建编辑器顶部导航（EditorHeader）

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/components/EditorHeader.tsx`

- [ ] **Step 1: 创建 EditorHeader.tsx**

```tsx
// frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/components/EditorHeader.tsx
"use client";

import { useRouter } from "next/navigation";
import { App, Button, Popconfirm, Space, Tag } from "antd";
import {
  ArrowLeftOutlined,
  SaveOutlined,
  SendOutlined,
  CheckCircleOutlined,
} from "@ant-design/icons";
import api from "@/lib/api";

interface PageTemplate {
  id: string;
  name: string;
  template_type: string;
  status: string;
}

interface PageVersion {
  id: string;
  version: number;
  status: string;
}

export function EditorHeader({
  template,
  version,
  saving,
  onSave,
  onRefresh,
}: {
  template: PageTemplate;
  version: PageVersion;
  saving: boolean;
  onSave: () => void;
  onRefresh: () => void;
}) {
  const router = useRouter();
  const { message } = App.useApp();

  const handlePublish = async () => {
    try {
      await api.post(`/page-versions/${version.id}/publish`);
      message.success("发布成功");
      onRefresh();
    } catch {
      message.error("发布失败");
    }
  };

  return (
    <div className="flex h-12 items-center justify-between border-b bg-white px-4">
      <div className="flex items-center gap-3">
        <Button
          type="text"
          icon={<ArrowLeftOutlined />}
          onClick={() => router.push(`/pages/${template.id}`)}
        >
          返回
        </Button>
        <span className="font-medium">{template.name}</span>
        <Tag color={version.status === "draft" ? "default" : "blue"}>
          草稿 v{version.version}
        </Tag>
      </div>
      <Space>
        <Button
          icon={<SaveOutlined />}
          onClick={onSave}
          loading={saving}
        >
          保存
        </Button>
        {version.status === "draft" && (
          <Popconfirm
            title="确认发布此版本？"
            onConfirm={handlePublish}
          >
            <Button type="primary" icon={<SendOutlined />}>
              发布
            </Button>
          </Popconfirm>
        )}
      </Space>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/admin/src/app/\(dashboard\)/pages/\[id\]/edit/components/EditorHeader.tsx
git commit -m "feat(admin): add EditorHeader with save and publish actions"
```

---

## Task 8: 组装全屏编辑器页面（`/pages/[id]/edit`）

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/page.tsx`

- [ ] **Step 1: 创建全屏编辑器主页面**

这是核心组装页面，协调所有子组件。需要注意：此页面需要脱离 `(dashboard)` layout 的侧边栏。由于 Next.js App Router 布局是嵌套的，编辑器页面需要一种方式绕过 dashboard layout。

方案：在 `(dashboard)/pages/[id]/edit/` 下创建自己的 layout.tsx，不渲染 Sider 和 Header，只渲染子内容。

先创建 layout 文件：

```tsx
// frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/layout.tsx
export default function EditLayout({ children }: { children: React.ReactNode }) {
  return <div className="fixed inset-0 z-50 bg-gray-50">{children}</div>;
}
```

然后创建主页面：

```tsx
// frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/page.tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import { App, Tabs, Input } from "antd";
import api from "@/lib/api";
import {
  validateDSL,
  createEmptyDSL,
  type PageDSL,
  type ModuleConfig,
} from "@/lib/page-dsl";
import { EditorHeader } from "./components/EditorHeader";
import { ModuleList } from "./components/ModuleList";
import { RoutingConfig } from "./components/RoutingConfig";
import { PreviewPanel } from "./components/PreviewPanel";

const { TextArea } = Input;

interface PageTemplate {
  id: string;
  name: string;
  template_type: string;
  status: string;
}

interface PageVersion {
  id: string;
  version: number;
  status: string;
  config_json: Record<string, unknown>;
  published_at?: string;
  created_at: string;
}

export default function PageEditorPage() {
  const params = useParams<{ id: string }>();
  const { message } = App.useApp();

  const [template, setTemplate] = useState<PageTemplate | null>(null);
  const [version, setVersion] = useState<PageVersion | null>(null);
  const [dsl, setDsl] = useState<PageDSL>(createEmptyDSL());
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState("modules");

  // 加载模板和草稿版本
  useEffect(() => {
    async function load() {
      try {
        const { data: tpl } = await api.get(`/page-templates/${params.id}`);
        setTemplate(tpl);
        const { data: versions } = await api.get(`/page-templates/${params.id}/versions`);
        const draft = versions.find((v: PageVersion) => v.status === "draft");
        const target = draft || versions[0];
        if (target) {
          setVersion(target);
          setDsl((target.config_json as PageDSL) || createEmptyDSL());
        }
      } catch {
        message.error("加载页面数据失败");
      }
    }
    load();
  }, [params.id, message]);

  // 保存 DSL
  const handleSave = useCallback(async () => {
    if (!version) return;
    const errors = validateDSL(dsl);
    if (errors.length > 0) {
      message.error(`DSL 校验失败: ${errors[0]}`);
      return;
    }
    try {
      setSaving(true);
      await api.patch(`/page-versions/${version.id}`, { config_json: dsl });
      message.success("保存成功");
    } catch {
      message.error("保存失败");
    } finally {
      setSaving(false);
    }
  }, [version, dsl, message]);

  // 刷新数据（发布后）
  const refreshData = useCallback(async () => {
    try {
      const { data: versions } = await api.get(`/page-templates/${params.id}/versions`);
      const draft = versions.find((v: PageVersion) => v.status === "draft");
      const target = draft || versions[0];
      if (target) {
        setVersion(target);
        setDsl((target.config_json as PageDSL) || createEmptyDSL());
      }
    } catch { /* ignore */ }
  }, [params.id]);

  if (!template || !version) {
    return <div className="flex h-screen items-center justify-center text-gray-400">加载中...</div>;
  }

  return (
    <div className="flex h-screen flex-col">
      <EditorHeader
        template={template}
        version={version}
        saving={saving}
        onSave={handleSave}
        onRefresh={refreshData}
      />
      <div className="flex flex-1 overflow-hidden">
        {/* 左侧编辑区 */}
        <div className="w-[480px] shrink-0 overflow-y-auto border-r bg-white p-4">
          <Tabs
            activeKey={activeTab}
            onChange={setActiveTab}
            items={[
              {
                key: "modules",
                label: "模块排序",
                children: (
                  <ModuleList
                    modules={dsl.modules || []}
                    onChange={(modules) => setDsl({ ...dsl, modules })}
                  />
                ),
              },
              {
                key: "routing",
                label: "活动期配置",
                children: (
                  <RoutingConfig dsl={dsl} onChange={setDsl} />
                ),
              },
              {
                key: "json",
                label: "JSON 编辑",
                children: (
                  <JSONEditor dsl={dsl} onChange={setDsl} />
                ),
              },
            ]}
          />
        </div>
        {/* 右侧预览区 */}
        <div className="flex-1 overflow-hidden bg-gray-50 p-4">
          <PreviewPanel dsl={dsl} />
        </div>
      </div>
    </div>
  );
}

function JSONEditor({
  dsl,
  onChange,
}: {
  dsl: PageDSL;
  onChange: (dsl: PageDSL) => void;
}) {
  const [text, setText] = useState(JSON.stringify(dsl, null, 2));
  const [errors, setErrors] = useState<string[]>([]);

  useEffect(() => {
    setText(JSON.stringify(dsl, null, 2));
    setErrors(validateDSL(dsl));
  }, [dsl]);

  const handleChange = (value: string) => {
    setText(value);
    try {
      const parsed = JSON.parse(value);
      const errs = validateDSL(parsed);
      setErrors(errs);
      if (errs.length === 0) {
        onChange(parsed as PageDSL);
      }
    } catch (e) {
      setErrors([`JSON 语法错误: ${(e as Error).message}`]);
    }
  };

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs text-gray-500">直接编辑 JSON 配置</span>
        {errors.length > 0 ? (
          <span className="text-xs text-red-500">{errors.length} 个错误</span>
        ) : (
          <span className="text-xs text-green-500">校验通过</span>
        )}
      </div>
      <TextArea
        value={text}
        onChange={(e) => handleChange(e.target.value)}
        rows={20}
        className="font-mono text-sm"
      />
      {errors.length > 0 && (
        <div className="mt-2 rounded bg-red-50 p-3">
          {errors.map((err, i) => (
            <div key={i} className="text-xs text-red-600">{err}</div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 验证编辑器页面可以加载**

```bash
cd /Users/ericding/code/agriculture/yimatong/frontend && pnpm dev:admin
```

打开 `http://localhost:3000/pages`，创建或找到已有模板，通过 URL 直接访问 `http://localhost:3000/pages/{template_id}/edit`，应看到左右分屏布局。

- [ ] **Step 3: Commit**

```bash
git add apps/admin/src/app/\(dashboard\)/pages/\[id\]/edit/
git commit -m "feat(admin): create full-screen page editor with split layout and live preview"
```

---

## Task 9: 创建版本管理独立页面（`/pages/[id]`）

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/pages/[id]/page.tsx`

- [ ] **Step 1: 创建版本管理页面**

将现有 `page.tsx` 中版本管理 Modal 的逻辑提取为独立页面。

```tsx
// frontend/apps/admin/src/app/(dashboard)/pages/[id]/page.tsx
"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { App, Button, Popconfirm, Space, Table, Tag, Tooltip } from "antd";
import {
  ArrowLeftOutlined,
  EditOutlined,
  SendOutlined,
  StopOutlined,
  RollbackOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import api from "@/lib/api";
import { createEmptyDSL } from "@/lib/page-dsl";

interface PageVersion {
  id: string;
  version: number;
  status: string;
  config_json: Record<string, unknown>;
  published_at?: string;
  created_at: string;
}

const VERSION_STATUS_MAP: Record<string, { label: string; color: string }> = {
  draft: { label: "草稿", color: "default" },
  published: { label: "已发布", color: "blue" },
  archived: { label: "已归档", color: "gray" },
  offline: { label: "已下线", color: "orange" },
};

export default function VersionPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { message } = App.useApp();

  const [templateName, setTemplateName] = useState("");
  const [versions, setVersions] = useState<PageVersion[]>([]);

  useEffect(() => {
    async function load() {
      try {
        const { data: tpl } = await api.get(`/page-templates/${params.id}`);
        setTemplateName(tpl.name);
        const { data: vs } = await api.get(`/page-templates/${params.id}/versions`);
        setVersions(vs);
      } catch {
        message.error("加载失败");
      }
    }
    load();
  }, [params.id, message]);

  const refresh = async () => {
    try {
      const { data: vs } = await api.get(`/page-templates/${params.id}/versions`);
      setVersions(vs);
    } catch { /* ignore */ }
  };

  const publishVersion = async (versionId: string) => {
    try {
      await api.post(`/page-versions/${versionId}/publish`);
      message.success("发布成功");
      refresh();
    } catch {
      message.error("发布失败");
    }
  };

  const archiveVersion = async (versionId: string) => {
    try {
      await api.post(`/page-versions/${versionId}/archive`);
      message.success("已下线");
      refresh();
    } catch {
      message.error("下线失败");
    }
  };

  const rollbackVersion = async (versionId: string) => {
    try {
      await api.post(`/page-templates/${params.id}/versions/${versionId}/rollback`);
      message.success("已回滚，创建了新草稿版本");
      refresh();
    } catch {
      message.error("回滚失败");
    }
  };

  const createNewDraft = async () => {
    try {
      const baseConfig = versions.find((v) => v.status === "published")?.config_json
        ?? versions[0]?.config_json
        ?? createEmptyDSL();
      await api.post(`/page-templates/${params.id}/versions`, { config_json: baseConfig });
      message.success("新草稿版本已创建");
      refresh();
    } catch {
      message.error("创建草稿失败");
    }
  };

  const columns: ColumnsType<PageVersion> = [
    {
      title: "版本",
      dataIndex: "version",
      key: "version",
      render: (v: number) => `v${v}`,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = VERSION_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "发布时间",
      dataIndex: "published_at",
      key: "published_at",
      render: (v: string) => (v ? dayjs(v).format("YYYY-MM-DD HH:mm") : "-"),
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v: string) => dayjs(v).format("YYYY-MM-DD HH:mm"),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: PageVersion) => (
        <Space>
          {record.status === "draft" && (
            <>
              <Button
                size="small"
                icon={<EditOutlined />}
                onClick={() => router.push(`/pages/${params.id}/edit`)}
              >
                编辑
              </Button>
              <Popconfirm title="确认发布此版本？" onConfirm={() => publishVersion(record.id)}>
                <Button size="small" type="primary" icon={<SendOutlined />}>
                  发布
                </Button>
              </Popconfirm>
            </>
          )}
          {record.status === "published" && (
            <Popconfirm title="确认下线？" onConfirm={() => archiveVersion(record.id)}>
              <Button size="small" danger icon={<StopOutlined />}>
                下线
              </Button>
            </Popconfirm>
          )}
          {record.status !== "draft" && (
            <Tooltip title="基于此版本创建新草稿">
              <Button
                size="small"
                icon={<RollbackOutlined />}
                onClick={() => rollbackVersion(record.id)}
              >
                回滚
              </Button>
            </Tooltip>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => router.push("/pages")}>
            返回
          </Button>
          <h2 className="text-lg font-semibold !mb-0">版本管理 — {templateName}</h2>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={createNewDraft}>
          创建新草稿
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={versions}
        rowKey="id"
        pagination={false}
        size="middle"
      />
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/admin/src/app/\(dashboard\)/pages/\[id\]/page.tsx
git commit -m "feat(admin): create dedicated version management page for page templates"
```

---

## Task 10: 精简模板列表页（`/pages`）

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/pages/page.tsx`

将现有的 ~1087 行单文件精简为纯列表页，移除已拆分出去的组件。保留：模板列表 Table、创建模板 Modal、行业模板库 Modal。移除：版本管理 Modal、DSL 编辑 Drawer、ModuleEditor、RoutingEditor、ModuleConfigForm。

- [ ] **Step 1: 重写 pages/page.tsx 为纯列表页**

```tsx
// frontend/apps/admin/src/app/(dashboard)/pages/page.tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { usePaginatedList } from "@/lib/hooks";
import { App, Button, Form, Input, Modal, Select, Space, Table, Tag } from "antd";
import { PlusOutlined, EyeOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { createEmptyDSL, createDefaultModules } from "@/lib/page-dsl";

const { TextArea } = Input;

interface PageTemplate {
  id: string;
  name: string;
  template_type: string;
  status: string;
  description?: string;
  published_version?: { version: number } | null;
}

const TYPE_LABELS: Record<string, string> = {
  product_info: "产品信息",
  traceability: "溯源页",
  brand_story: "品牌故事",
  campaign: "活动页",
};

export default function PagesPage() {
  const router = useRouter();
  const { message } = App.useApp();
  const {
    items: templates,
    total,
    page,
    loading,
    setPage,
    refresh: refreshTemplates,
  } = usePaginatedList<PageTemplate>(
    async ({ page, page_size }) => {
      try {
        const { data } = await api.get("/page-templates", { params: { page, page_size } });
        return { items: data.items || [], total: data.total || 0 };
      } catch {
        message.error("加载页面列表失败");
        return { items: [], total: 0 };
      }
    }
  );

  const [createOpen, setCreateOpen] = useState(false);
  const [industryOpen, setIndustryOpen] = useState(false);
  const [industryTemplates, setIndustryTemplates] = useState<Record<string, unknown>[]>([]);
  const [form] = Form.useForm();

  const handleCreate = async (values: Record<string, string>) => {
    try {
      const { data: tpl } = await api.post("/page-templates", values);
      const initialDSL = createEmptyDSL();
      initialDSL.modules = createDefaultModules();
      await api.post(`/page-templates/${tpl.id}/versions`, { config_json: initialDSL });
      message.success("页面模板创建成功，已生成初始草稿");
      setCreateOpen(false);
      form.resetFields();
      refreshTemplates();
    } catch {
      message.error("创建失败");
    }
  };

  const fetchIndustryTemplates = async () => {
    try {
      const { data } = await api.get("/page-templates/industry-templates");
      setIndustryTemplates(data);
    } catch { /* ignore */ }
  };

  const cloneTemplate = async (index: number) => {
    try {
      await api.post(`/page-templates/industry-templates/${index}/clone`);
      message.success("模板复制成功");
      setIndustryOpen(false);
      refreshTemplates();
    } catch {
      message.error("复制失败");
    }
  };

  const columns: ColumnsType<PageTemplate> = [
    { title: "模板名称", dataIndex: "name", key: "name" },
    {
      title: "类型",
      dataIndex: "template_type",
      key: "template_type",
      render: (t: string) => TYPE_LABELS[t] || t,
    },
    {
      title: "已发布版本",
      key: "published",
      render: (_: unknown, record: PageTemplate) =>
        record.published_version ? (
          <Tag color="blue">v{record.published_version.version}</Tag>
        ) : (
          <Tag>未发布</Tag>
        ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: PageTemplate) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => router.push(`/pages/${record.id}`)}>
            版本管理
          </Button>
          <Button
            size="small"
            type="primary"
            onClick={() => router.push(`/pages/${record.id}/edit`)}
          >
            编辑
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h4 className="!mb-0 text-lg font-semibold">页面管理</h4>
        <Space>
          <Button onClick={() => { setIndustryOpen(true); fetchIndustryTemplates(); }}>
            行业模板库
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建页面
          </Button>
        </Space>
      </div>

      <Table
        columns={columns}
        dataSource={templates}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page, total, pageSize: 20, onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
      />

      <Modal
        title="新建页面模板"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="模板名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="template_type" label="类型" rules={[{ required: true }]}>
            <Select options={Object.entries(TYPE_LABELS).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="行业模板库"
        open={industryOpen}
        onCancel={() => setIndustryOpen(false)}
        footer={null}
        width={700}
      >
        <div className="grid grid-cols-1 gap-4">
          {industryTemplates.map((tpl, i) => (
            <div key={i} className="flex items-center justify-between rounded border p-4">
              <div>
                <div className="font-medium">{String(tpl.name)}</div>
                <div className="text-sm text-gray-500">{String(tpl.description)}</div>
              </div>
              <Button type="primary" onClick={() => cloneTemplate(i)}>
                使用模板
              </Button>
            </div>
          ))}
        </div>
      </Modal>
    </div>
  );
}
```

- [ ] **Step 2: 验证模板列表页正常工作**

```bash
cd /Users/ericding/code/agriculture/yimatong/frontend && pnpm dev:admin
```

打开 `http://localhost:3000/pages`，确认列表显示正常、创建模板和行业模板库功能正常、操作列有"版本管理"和"编辑"两个按钮。

- [ ] **Step 3: Commit**

```bash
git add apps/admin/src/app/\(dashboard\)/pages/page.tsx
git commit -m "refactor(admin): simplify pages list page, extract editor and versions to separate routes"
```

---

## Task 11: 端到端集成测试

- [ ] **Step 1: 启动全部服务**

```bash
cd /Users/ericding/code/agriculture/yimatong
# 确保 Docker 服务运行
docker compose -f docker-compose.dev.yml up -d
# 启动后端
cd backend && source .venv/bin/activate && uv run uvicorn app.main:app --reload &
# 启动前端
cd frontend && pnpm dev:admin & pnpm dev:h5 &
```

- [ ] **Step 2: 测试完整流程**

1. 打开 `http://localhost:3000/pages`
2. 点击"新建页面"创建模板
3. 在列表中点击"编辑"按钮
4. 确认进入全屏编辑器（无侧边栏）
5. 左侧拖拽模块排序
6. 点击模块展开属性配置
7. 切换 Tab 到"活动期配置"
8. 切换 Tab 到"JSON 编辑"
9. 右侧 iframe 显示 H5 预览
10. 点击"保存"
11. 点击"发布"
12. 点击"返回"回到版本管理页
13. 点击"返回"回到模板列表

- [ ] **Step 3: 修复测试中发现的问题**

记录并修复任何 UI 或功能问题。

- [ ] **Step 4: 最终 Commit**

```bash
git add -A
git commit -m "fix(admin): integration fixes for page editor split layout"
```

---

## Self-Review

### Spec Coverage

| Spec 要求 | 对应 Task |
|-----------|----------|
| dnd-kit 拖拽排序 | Task 1 (安装), Task 5 (组件) |
| iframe 实时预览 | Task 2 (H5 preview), Task 6 (PreviewPanel) |
| 模板列表优化 | Task 10 |
| 版本管理独立页 | Task 9 |
| 全屏编辑器 | Task 8 (主页面 + layout) |
| 活动期路由配置 | Task 4 (RoutingConfig) |
| 模块配置表单 | Task 3 (ModuleConfigForms) |
| 编辑器顶部导航 | Task 7 (EditorHeader) |
| postMessage 通信 | Task 2 (H5 端), Task 6 (Admin 端) |
| 设备切换+缩放 | Task 6 (PreviewPanel) |

### Placeholder Scan

无 TBD/TODO/待填写内容。所有代码完整。

### Type Consistency

- `PageDSL`, `ModuleConfig`, `ModuleType`, `CampaignPeriod` 类型从 `@/lib/page-dsl` 统一导出
- `PreviewPanel` 接收 `PageDSL` 类型
- `ModuleList` 接收 `ModuleConfig[]` 类型
- `RoutingConfig` 接收 `PageDSL` 类型
- 所有组件 props 类型一致
