import assert from "node:assert/strict";
import test from "node:test";

import {
  brandCssVars,
  contrastRatio,
  deriveShades,
  resolveBrandSlots,
} from "./brand-theme.ts";

test("三层回退：页面 DSL 覆盖 → 租户 profile → 默认主题", () => {
  // 全无配置 → 默认绿
  const fallback = resolveBrandSlots();
  assert.equal(fallback.primaryColor, "#15803d");
  assert.equal(fallback.hideYimatongBrand, false);

  // 租户级生效
  const tenant = resolveBrandSlots({ primary_color: "#1F7A4D" });
  assert.equal(tenant.primaryColor, "#1f7a4d");

  // 页面 DSL 覆盖租户级
  const page = resolveBrandSlots(
    { primary_color: "#1F7A4D" },
    { brand_theme: { primary_color: "#b45309" } }
  );
  assert.equal(page.primaryColor, "#b45309");

  // DSL 未覆盖的槽位回落租户级
  assert.equal(
    resolveBrandSlots(
      { primary_color: "#1F7A4D", radius_preset: "lg" },
      { brand_theme: { primary_color: "#b45309" } }
    ).radiusPreset,
    "lg"
  );
});

test("page.brand_theme 嵌套形状同样生效", () => {
  const slots = resolveBrandSlots(undefined, {
    page: {
      brand_theme: { primary_color: "#0f766e", hide_yimatong_brand: true },
    },
  });
  assert.equal(slots.primaryColor, "#0f766e");
  assert.equal(slots.hideYimatongBrand, true);
});

test("非法主色被忽略并回落", () => {
  assert.equal(
    resolveBrandSlots({ primary_color: "red" }).primaryColor,
    "#15803d"
  );
  assert.equal(
    resolveBrandSlots(
      { primary_color: "#1F7A4D" },
      { brand_theme: { primary_color: "#xyz" } }
    ).primaryColor,
    "#1f7a4d"
  );
});

test("deriveShades：hover/active 逐级加深，subtle 为浅底", () => {
  const shades = deriveShades("#16a34a");
  for (const key of ["primary", "hover", "active", "subtle", "onPrimary"]) {
    assert.match(shades[key], /^#[0-9a-f]{6}$/);
  }
  assert.equal(shades.primary, "#16a34a");
  assert.notEqual(shades.hover, shades.primary);
  assert.notEqual(shades.active, shades.hover);
  // subtle 浅底应能承载 primary 色文字（≥3:1）
  assert.ok(contrastRatio(shades.primary, shades.subtle) >= 3.0);
  assert.equal(shades.onPrimary, "#000000"); // 中亮度绿黑字更稳
});

test("brandCssVars：映射到 --ymt 语义变量与半径/背景槽位", () => {
  const vars = brandCssVars(
    resolveBrandSlots({ primary_color: "#1F7A4D", radius_preset: "lg" })
  );
  assert.equal(vars["--ymt-color-brand"], "#1f7a4d");
  assert.equal(vars["--ymt-color-action"], "#1f7a4d");
  assert.ok(vars["--ymt-color-action-hover"]);
  assert.ok(vars["--ymt-color-on-action"]);
  assert.ok(vars["--ymt-radius-md"]);
  assert.ok(vars["--ymt-brand-page-bg"]);
});

test("radius/background 预设默认值", () => {
  const slots = resolveBrandSlots();
  assert.equal(slots.radiusPreset, "md");
  assert.equal(slots.backgroundPreset, "canvas");
});

test("客服槽位回退：租户配置生效，非法值回落平台默认", () => {
  // 无配置 → 平台默认（占位电话，无企微链接）
  const fallback = resolveBrandSlots();
  assert.equal(fallback.supportPhone, "400-000-0000");
  assert.equal(fallback.supportWecomUrl, "");

  // 租户配置生效
  const tenant = resolveBrandSlots({
    support_phone: "400-123-4567",
    support_wecom_url: "https://work.weixin.qq.com/kabc/example",
  });
  assert.equal(tenant.supportPhone, "400-123-4567");
  assert.equal(
    tenant.supportWecomUrl,
    "https://work.weixin.qq.com/kabc/example"
  );

  // 非法值回落：注入字符的电话与非 https 链接不透出
  const unsafe = resolveBrandSlots({
    support_phone: "400<script>alert(1)</script>",
    support_wecom_url: "javascript:alert(1)",
  });
  assert.equal(unsafe.supportPhone, "400-000-0000");
  assert.equal(unsafe.supportWecomUrl, "");
});

test("客服电话规则与后端 _validate_support_phone 对齐", () => {
  // 前导 + 与 - * ( ) 空格分隔合法；剔除分隔符后 5-20 位数字
  assert.equal(
    resolveBrandSlots({ support_phone: "+86 400-123*4567" }).supportPhone,
    "+86 400-123*4567"
  );
  assert.equal(
    resolveBrandSlots({ support_phone: "(0571) 8888 8888" }).supportPhone,
    "(0571) 8888 8888"
  );
  // 过短/过长/多前导 + 均回落
  assert.equal(
    resolveBrandSlots({ support_phone: "123" }).supportPhone,
    "400-000-0000"
  );
  assert.equal(
    resolveBrandSlots({ support_phone: "+86+4012345678" }).supportPhone,
    "400-000-0000"
  );
  assert.equal(
    resolveBrandSlots({ support_phone: "01234567890123456789012" })
      .supportPhone,
    "400-000-0000"
  );
});
