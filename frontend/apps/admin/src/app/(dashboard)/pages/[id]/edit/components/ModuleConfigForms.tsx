"use client";

import { useEffect, useMemo, useState } from "react";
import { Button, Input, InputNumber, Select, Switch, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import ImageUploadInput from "@/components/ImageUploadInput";
import api from "@/lib/api";

const { Text } = Typography;
const { TextArea } = Input;

interface ProductAssetOption {
  id: string;
  asset_type: string;
  name: string;
  issuer?: string;
  valid_until?: string;
}

export function ModuleConfigForm({
  moduleType,
  productId,
  config,
  onChange,
}: {
  moduleType: string;
  productId?: string | null;
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  const [assets, setAssets] = useState<ProductAssetOption[]>([]);

  useEffect(() => {
    const needsAssets = ["test_reports", "certificates", "media_section"].includes(moduleType);
    if (!productId || !needsAssets) {
      setAssets([]);
      return;
    }
    api.get(`/products/${productId}/assets`, { params: { page_size: 100 } })
      .then(({ data }) => setAssets(data.items || []))
      .catch(() => setAssets([]));
  }, [moduleType, productId]);

  const reportOptions = useMemo(
    () => assets
      .filter((asset) => asset.asset_type === "test_report")
      .map((asset) => ({ value: asset.id, label: `${asset.name}${asset.issuer ? ` · ${asset.issuer}` : ""}` })),
    [assets],
  );
  const certificateOptions = useMemo(
    () => assets
      .filter((asset) => asset.asset_type === "certificate")
      .map((asset) => ({ value: asset.id, label: `${asset.name}${asset.valid_until ? ` · 有效期至 ${asset.valid_until}` : ""}` })),
    [assets],
  );
  const mediaOptions = useMemo(
    () => assets
      .filter((asset) => ["image", "video", "story"].includes(asset.asset_type))
      .map((asset) => ({ value: asset.id, label: asset.name })),
    [assets],
  );

  const update = (key: string, value: unknown) => {
    onChange({ ...config, [key]: value });
  };

  switch (moduleType) {
    /* ─── 产品展示 ────────────────────────────── */
    case "product_hero":
      return (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Switch size="small" checked={!!config.show_verify_badge} onChange={(v) => update("show_verify_badge", v)} />
            <Text type="secondary" className="text-xs">显示验真徽章</Text>
          </div>
          <ImageUploadInput
            size="small"
            module="page-image"
            buttonText="上传"
            previewAlt="产品展示图预览"
            placeholder="产品图片（可选，留空使用产品数据）"
            value={String(config.image_url || "") || undefined}
            onChange={(value) => update("image_url", value || "")}
          />
          <Input size="small" placeholder="标题模板，如 {{product.name}}" value={String(config.title_template || "")} onChange={(e) => update("title_template", e.target.value)} />
        </div>
      );

    /* ─── 验真状态 ────────────────────────────── */
    case "verification_status":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="首次扫码提示语" value={String(config.first_scan_text || "")} onChange={(e) => update("first_scan_text", e.target.value)} />
          <Input size="small" placeholder="重复扫码提示语" value={String(config.repeat_scan_text || "")} onChange={(e) => update("repeat_scan_text", e.target.value)} />
          <Input size="small" placeholder="无效码提示语" value={String(config.invalid_text || "")} onChange={(e) => update("invalid_text", e.target.value)} />
        </div>
      );

    /* ─── 溯源信息 ────────────────────────────── */
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
          <Select
            mode="multiple"
            size="small"
            placeholder="选择要显示的溯源字段"
            value={(config.fields as string[]) || []}
            onChange={(v) => update("fields", v)}
            options={allFields}
            style={{ width: "100%" }}
          />
        </div>
      );
    }

    /* ─── 检测报告 ────────────────────────────── */
    case "test_reports":
      return (
        <div className="space-y-2">
          <Text type="secondary" className="text-xs">检测报告</Text>
          <Select
            mode={productId ? "multiple" : "tags"}
            size="small"
            placeholder={productId ? "选择产品资料库中的检测报告" : "输入报告 ID 后回车添加"}
            value={((config.report_ids as string[]) || []).map(String)}
            onChange={(v) => update("report_ids", v)}
            options={reportOptions}
            style={{ width: "100%" }}
            open={productId ? undefined : false}
          />
        </div>
      );

    /* ─── 资质证书 ────────────────────────────── */
    case "certificates":
      return (
        <div className="space-y-2">
          <Text type="secondary" className="text-xs">资质证书</Text>
          <Select
            mode={productId ? "multiple" : "tags"}
            size="small"
            placeholder={productId ? "选择产品资料库中的资质证书" : "输入证书 ID 后回车添加"}
            value={((config.certificate_ids as string[]) || []).map(String)}
            onChange={(v) => update("certificate_ids", v)}
            options={certificateOptions}
            style={{ width: "100%" }}
            open={productId ? undefined : false}
          />
        </div>
      );

    /* ─── 权益卡片 ────────────────────────────── */
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

    /* ─── 私域/跳转按钮 ───────────────────────── */
    case "cta_group":
      return (
        <div className="space-y-1">
          <Text type="secondary" className="text-xs">按钮配置（JSON）</Text>
          <TextArea size="small" rows={3} value={JSON.stringify(config.buttons || [], null, 0)}
            onChange={(e) => { try { update("buttons", JSON.parse(e.target.value)); } catch { /* ignore */ } }} />
        </div>
      );

    /* ─── 购买渠道 ────────────────────────────── */
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
                  { value: "taobao", label: "淘宝" },
                  { value: "jd", label: "京东" },
                  { value: "douyin", label: "抖音" },
                  { value: "pdd", label: "拼多多" },
                  { value: "other", label: "其他" },
                ]}
                style={{ width: 80 }}
              />
              <Input size="small" placeholder="渠道名称" value={shop.name} onChange={(e) => updateShop(i, "name", e.target.value)} style={{ flex: 1 }} />
              <Input size="small" placeholder="链接" value={shop.url} onChange={(e) => updateShop(i, "url", e.target.value)} style={{ flex: 1 }} />
              <Button size="small" danger onClick={() => removeShop(i)}>×</Button>
            </div>
          ))}
          {shops.length === 0 && <Text type="secondary" className="text-xs">暂未配置渠道</Text>}
        </div>
      );
    }

    /* ─── 留资表单 ────────────────────────────── */
    case "lead_form": {
      const allFormFields = [
        { value: "name", label: "姓名" },
        { value: "phone", label: "手机号" },
        { value: "address", label: "地址" },
        { value: "email", label: "邮箱" },
        { value: "remark", label: "备注" },
      ];
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="表单标题" value={String(config.title || "")} onChange={(e) => update("title", e.target.value)} />
          <Input size="small" placeholder="副标题" value={String(config.subtitle || "")} onChange={(e) => update("subtitle", e.target.value)} />
          <Input size="small" placeholder="提交按钮文案" value={String(config.submit_label || "")} onChange={(e) => update("submit_label", e.target.value)} />
          <Text type="secondary" className="text-xs">表单字段</Text>
          <Select
            mode="multiple"
            size="small"
            placeholder="选择需要收集的字段"
            value={(config.fields as string[]) || []}
            onChange={(v) => update("fields", v)}
            options={allFormFields}
            style={{ width: "100%" }}
          />
        </div>
      );
    }

    /* ─── 视频/图文 ────────────────────────────── */
    case "media_section":
      return (
        <div className="space-y-2">
          <Text type="secondary" className="text-xs">图文/视频素材</Text>
          <Select
            mode={productId ? "multiple" : "tags"}
            size="small"
            placeholder={productId ? "选择产品资料库中的图片、视频或故事" : "输入素材 ID 后回车添加"}
            value={((config.asset_ids as string[]) || []).map(String)}
            onChange={(v) => update("asset_ids", v)}
            options={mediaOptions}
            style={{ width: "100%" }}
            open={productId ? undefined : false}
          />
        </div>
      );

    /* ─── 法律条款 ────────────────────────────── */
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
          <Input size="small" placeholder="隐私政策内容（可选，留空使用默认）" value={String(config.privacy_content || "")} onChange={(e) => update("privacy_content", e.target.value)} />
        </div>
      );

    /* ─── 自定义 HTML ────────────────────────────── */
    case "custom_html":
      return (
        <div className="space-y-2">
          <Text type="secondary" className="text-xs">自定义 HTML 内容</Text>
          <TextArea
            size="small"
            rows={5}
            value={String(config.html || "")}
            onChange={(e) => update("html", e.target.value)}
            placeholder="<div>...</div>"
            className="font-mono text-xs"
          />
        </div>
      );

    /* ─── 会员卡片 ────────────────────────────── */
    case "member_card":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="消费者 ID" value={String(config.consumer_id || "")} onChange={(e) => update("consumer_id", e.target.value)} />
          <Select size="small" placeholder="会员等级" value={config.member_level || undefined} onChange={(v) => update("member_level", v)}
            options={[{ value: "bronze", label: "青铜" }, { value: "silver", label: "白银" }, { value: "gold", label: "黄金" }, { value: "diamond", label: "钻石" }]}
            style={{ width: 120 }} />
        </div>
      );

    /* ─── 积分余额 ────────────────────────────── */
    case "points_balance":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="消费者 ID（可选）" value={String(config.consumer_id || "")} onChange={(e) => update("consumer_id", e.target.value)} />
          <InputNumber size="small" placeholder="初始积分" min={0} value={Number(config.points) || undefined} onChange={(v) => update("points", v)} />
        </div>
      );

    /* ─── 积分兑换 ────────────────────────────── */
    case "points_exchange":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="权益 ID" value={String(config.benefit_id || "")} onChange={(e) => update("benefit_id", e.target.value)} />
          <Input size="small" placeholder="标题" value={String(config.title || "")} onChange={(e) => update("title", e.target.value)} />
          <InputNumber size="small" placeholder="所需积分" min={0} value={Number(config.points_cost) || undefined} onChange={(v) => update("points_cost", v)} />
          <Input size="small" placeholder="描述" value={String(config.description || "")} onChange={(e) => update("description", e.target.value)} />
        </div>
      );

    /* ─── 外码引导 ────────────────────────────── */
    case "outer_code_guide":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="品牌名称" value={String(config.brand_name || "")} onChange={(e) => update("brand_name", e.target.value)} />
          <Input size="small" placeholder="产品名称" value={String(config.product_name || "")} onChange={(e) => update("product_name", e.target.value)} />
          <Input size="small" placeholder="内码提示文案" value={String(config.inner_code_hint || "")} onChange={(e) => update("inner_code_hint", e.target.value)} />
          <ImageUploadInput
            size="small"
            module="page-image"
            buttonText="上传"
            previewAlt="外码引导产品图预览"
            placeholder="产品图片"
            value={String(config.product_image || "") || undefined}
            onChange={(value) => update("product_image", value || "")}
          />
        </div>
      );

    /* ─── 风险预警 ────────────────────────────── */
    case "risk_alert":
      return (
        <div className="space-y-2">
          <Select size="small" placeholder="预警类型" value={config.alert_type || undefined} onChange={(v) => update("alert_type", v)}
            options={[{ value: "frequency", label: "频率限制" }, { value: "multi_location", label: "多地扫码" }, { value: "suspected_copy", label: "疑似复制码" }]}
            style={{ width: 140 }} />
          <Input size="small" placeholder="详情" value={String(config.detail || "")} onChange={(e) => update("detail", e.target.value)} />
        </div>
      );

    /* ─── 双码验真 ────────────────────────────── */
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
      return (
        <Text type="secondary" className="text-xs italic">
          此模块无可配置项
        </Text>
      );
  }
}
