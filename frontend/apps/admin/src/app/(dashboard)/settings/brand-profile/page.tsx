"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  App,
  Button,
  Card,
  ColorPicker,
  Form,
  Space,
  Spin,
  Switch,
  Typography,
} from "antd";
import type { Color } from "antd/es/color-picker";
import ImageUploadInput from "@/components/ImageUploadInput";
import api, { extractErrorMessage } from "@/lib/api";
import PresetPicker, { type PresetOption } from "./_components/PresetPicker";
import { validatePrimaryColor } from "./_lib/validate-primary-color";

const { Title, Text } = Typography;

type RadiusPreset = "sm" | "md" | "lg";
type BackgroundPreset = "canvas" | "muted" | "tinted";

interface BrandProfile {
  primary_color?: string;
  radius_preset?: RadiusPreset;
  background_preset?: BackgroundPreset;
  hide_yimatong_brand?: boolean;
  logo_url?: string;
}

interface TenantMe {
  id: string;
  brand_profile: BrandProfile | null;
}

/** 圆角预设档（与 H5 brand-theme.ts RADIUS_MAP 对齐） */
const RADIUS_OPTIONS: PresetOption<RadiusPreset>[] = [
  {
    value: "sm",
    label: "紧凑",
    preview: { radius: 4 },
    desc: "圆角较小，偏利落",
  },
  { value: "md", label: "标准", preview: { radius: 10 }, desc: "默认圆角" },
  {
    value: "lg",
    label: "圆润",
    preview: { radius: 18 },
    desc: "圆角较大，偏亲和",
  },
];

/** 背景预设档（与 H5 brand-theme.ts pageBg 对齐） */
const BACKGROUND_OPTIONS: PresetOption<BackgroundPreset>[] = [
  {
    value: "canvas",
    label: "纯净",
    preview: { background: "#f6f8f5" },
    desc: "默认浅灰底",
  },
  {
    value: "muted",
    label: "柔和",
    preview: { background: "#f0f4ef" },
    desc: "略带绿调的柔和底",
  },
  {
    value: "tinted",
    label: "品牌",
    preview: { background: "#eef5ef" },
    desc: "带品牌主色调染",
  },
];

const DEFAULT_PRIMARY = "#15803d";

export default function BrandProfilePage() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [profile, setProfile] = useState<BrandProfile>({});
  const [primaryColor, setPrimaryColor] = useState<string>(DEFAULT_PRIMARY);

  // 预览 iframe（决策 3：复用 H5 /preview + postMessage）
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [previewReady, setPreviewReady] = useState(false);
  const configuredH5Url = process.env.NEXT_PUBLIC_H5_URL?.replace(/\/$/, "");
  const previewUrl = configuredH5Url ? `${configuredH5Url}/preview` : null;

  // 加载当前 brand_profile
  const fetchProfile = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get<TenantMe>("/tenants/me");
      const p = data.brand_profile || {};
      setProfile(p);
      setPrimaryColor(p.primary_color || DEFAULT_PRIMARY);
    } catch (err) {
      message.error(extractErrorMessage(err, "加载品牌配置失败"));
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    fetchProfile();
  }, [fetchProfile]);

  // 主色前端预校验（决策 4）
  const colorCheck = validatePrimaryColor(primaryColor);

  // 把当前编辑中的配置拼成 tenant_branding，推给 H5 预览。
  // tenant_branding 是 dsl 的顶层字段（H5 PreviewRenderer 从 config.tenant_branding 读）。
  const sendPreview = useCallback(() => {
    if (!iframeRef.current?.contentWindow || !previewReady || !previewUrl)
      return;
    const origin = new URL(previewUrl, window.location.origin).origin;
    iframeRef.current.contentWindow.postMessage(
      {
        type: "preview-dsl",
        payload: {
          dsl: {
            modules: [
              { id: "hero", type: "product_hero", enabled: true },
              { id: "benefit", type: "benefit_claim", enabled: true },
            ],
            tenant_branding: {
              name: "品牌预览",
              logo_url: profile.logo_url,
              primary_color: colorCheck.ok ? primaryColor : DEFAULT_PRIMARY,
              radius_preset: profile.radius_preset,
              background_preset: profile.background_preset,
              hide_yimatong_brand: profile.hide_yimatong_brand,
            },
          },
          previewContext: {},
          previewMode: "example",
        },
      },
      origin
    );
  }, [previewReady, previewUrl, profile, primaryColor, colorCheck]);

  useEffect(() => {
    sendPreview();
  }, [sendPreview]);

  // 监听 H5 预览就绪
  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.data?.type === "preview-ready") setPreviewReady(true);
    }
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  const handleSave = async () => {
    if (!colorCheck.ok) {
      message.error(colorCheck.reason || "主色不合规");
      return;
    }
    const payload: BrandProfile = {
      // 与后端 validate_brand_primary_color 一致：归一化为小写 hex
      primary_color: primaryColor.toLowerCase(),
      radius_preset: profile.radius_preset,
      background_preset: profile.background_preset,
      hide_yimatong_brand: profile.hide_yimatong_brand,
      logo_url: profile.logo_url,
    };
    setSaving(true);
    try {
      await api.patch("/tenants/me", { brand_profile: payload });
      message.success("品牌配置已保存");
      setProfile(payload);
    } catch (err) {
      message.error(extractErrorMessage(err, "保存失败"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <Spin />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
      {/* 左：五槽位配置表单 */}
      <div className="w-full lg:max-w-[480px]">
        <Title level={4} className="!mb-1">
          品牌定制
        </Title>
        <Text type="secondary" className="mb-4 block">
          配置消费者扫码 H5
          页面的品牌外观，改动实时预览。仅开放受控槽位，保证观感下限。
        </Text>

        <Card size="small">
          <Form layout="vertical">
            <Form.Item label="品牌 Logo" extra="展示在 H5 页头，留空使用默认。">
              <ImageUploadInput
                module="brand-logo"
                previewAlt="品牌 Logo 预览"
                value={profile.logo_url}
                onChange={(v) => setProfile((p) => ({ ...p, logo_url: v }))}
              />
            </Form.Item>

            <Form.Item
              label="品牌主色"
              extra={
                colorCheck.ok
                  ? `与白底对比度 ${colorCheck.ratio?.toFixed(2)}:1，合规`
                  : colorCheck.reason
              }
              validateStatus={colorCheck.ok ? "success" : "error"}
            >
              <ColorPicker
                value={primaryColor}
                onChange={(_c: Color, hex: string) => setPrimaryColor(hex)}
                format="hex"
                showText
              />
            </Form.Item>

            <Form.Item label="按钮圆角风格">
              <PresetPicker<RadiusPreset>
                value={profile.radius_preset || "md"}
                onChange={(v) =>
                  setProfile((p) => ({ ...p, radius_preset: v }))
                }
                options={RADIUS_OPTIONS}
              />
            </Form.Item>

            <Form.Item label="页面背景">
              <PresetPicker<BackgroundPreset>
                value={profile.background_preset || "canvas"}
                onChange={(v) =>
                  setProfile((p) => ({ ...p, background_preset: v }))
                }
                options={BACKGROUND_OPTIONS}
              />
            </Form.Item>

            <Form.Item label="隐藏一码通背书" valuePropName="checked">
              <Switch
                checked={profile.hide_yimatong_brand || false}
                onChange={(v) =>
                  setProfile((p) => ({ ...p, hide_yimatong_brand: v }))
                }
              />
            </Form.Item>

            <Space>
              <Button
                type="primary"
                onClick={handleSave}
                loading={saving}
                disabled={!colorCheck.ok}
              >
                保存配置
              </Button>
              <Button onClick={fetchProfile}>重置</Button>
            </Space>
          </Form>
        </Card>
      </div>

      {/* 右：H5 实时预览 */}
      <div className="w-full lg:flex-1">
        <Card size="small" title="H5 实时预览">
          {previewUrl ? (
            <div className="flex justify-center">
              <div
                style={{
                  width: 375,
                  height: 667,
                  border: "3px solid var(--ymt-color-border)",
                  borderRadius: "var(--ymt-radius-xl)",
                  overflow: "hidden",
                }}
              >
                <iframe
                  ref={iframeRef}
                  src={previewUrl}
                  style={{ width: "100%", height: "100%", border: "none" }}
                  title="H5 品牌预览"
                />
              </div>
            </div>
          ) : (
            <Text type="secondary">
              未配置
              NEXT_PUBLIC_H5_URL，无法显示实时预览。配置后可在此预览消费者扫码页效果。
            </Text>
          )}
        </Card>
      </div>
    </div>
  );
}
