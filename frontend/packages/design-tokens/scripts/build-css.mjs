import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  accessibilityPairs,
  contrastRatio,
  getTokenValue,
  primitives,
  themes,
} from "../tokens.ts";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const checkOnly = process.argv.includes("--check");

function kebab(value) {
  return value.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase();
}

function flatten(source, prefix = []) {
  return Object.entries(source).flatMap(([key, value]) => {
    const path = [...prefix, kebab(key)];
    return typeof value === "object" && value !== null
      ? flatten(value, path)
      : [[path.join("-"), value]];
  });
}

function cssValue(path, value) {
  if (
    typeof value === "number" &&
    (path.startsWith("space-") ||
      path.startsWith("radius-") ||
      path.startsWith("font-size-") ||
      path.startsWith("focus-") ||
      path.startsWith("target-"))
  ) {
    return `${value}px`;
  }
  return String(value);
}

function declarations(source, prefix = "--ymt") {
  return flatten(source)
    .map(([path, value]) => `  ${prefix}-${path}: ${cssValue(path, value)};`)
    .join("\n");
}

const primitiveThemeMappings = [
  ...Object.entries(primitives.color.brand).map(([step]) => [
    `--color-brand-${step}`,
    `--ymt-color-brand-${step}`,
  ]),
  ...Object.entries(primitives.color.accent).map(([step]) => [
    `--color-accent-${step}`,
    `--ymt-color-accent-${step}`,
  ]),
  ...Object.entries(primitives.font.size).map(([step]) => [
    `--text-${step}`,
    `--ymt-font-size-${kebab(step)}`,
  ]),
  ...Object.entries(primitives.font.weight).map(([step]) => [
    `--font-weight-${kebab(step)}`,
    `--ymt-font-weight-${kebab(step)}`,
  ]),
  ...Object.entries(primitives.radius).map(([step]) => [
    `--radius-${kebab(step)}`,
    `--ymt-radius-${kebab(step)}`,
  ]),
  ...Object.entries(primitives.shadow).map(([step]) => [
    `--shadow-${kebab(step)}`,
    `--ymt-shadow-${kebab(step)}`,
  ]),
];

const semanticThemeMappings = {
  "--color-canvas": "--ymt-color-bg-canvas",
  "--color-surface": "--ymt-color-bg-surface",
  "--color-elevated": "--ymt-color-bg-elevated",
  "--color-muted": "--ymt-color-bg-muted",
  "--color-foreground": "--ymt-color-text-primary",
  "--color-foreground-secondary": "--ymt-color-text-secondary",
  "--color-foreground-tertiary": "--ymt-color-text-tertiary",
  "--color-border-base": "--ymt-color-border-base",
  "--color-border-strong": "--ymt-color-border-strong",
  "--color-brand": "--ymt-color-brand-primary",
  "--color-brand-subtle": "--ymt-color-brand-subtle",
  "--color-brand-accent": "--ymt-color-brand-accent",
  "--color-action": "--ymt-color-action-primary",
  "--color-action-hover": "--ymt-color-action-primary-hover",
  "--color-action-active": "--ymt-color-action-primary-active",
  "--color-on-action": "--ymt-color-action-on-primary",
  "--color-link": "--ymt-color-action-link",
  "--color-success": "--ymt-color-feedback-success",
  "--color-success-bg": "--ymt-color-feedback-success-bg",
  "--color-success-border": "--ymt-color-feedback-success-border",
  "--color-warning": "--ymt-color-feedback-warning",
  "--color-warning-bg": "--ymt-color-feedback-warning-bg",
  "--color-warning-border": "--ymt-color-feedback-warning-border",
  "--color-danger": "--ymt-color-feedback-danger",
  "--color-danger-bg": "--ymt-color-feedback-danger-bg",
  "--color-danger-border": "--ymt-color-feedback-danger-border",
  "--color-info": "--ymt-color-feedback-info",
  "--color-info-bg": "--ymt-color-feedback-info-bg",
  "--color-info-border": "--ymt-color-feedback-info-border",
  "--color-focus-ring": "--ymt-color-focus-ring",
};

const css = `/* Generated from @yimatong/design-tokens. Do not edit by hand. */

@theme {
${primitiveThemeMappings.map(([target, source]) => `  ${target}: var(${source});`).join("\n")}
  --font-body: var(--ymt-font-family-body);
  --font-mono: var(--ymt-font-family-mono);
}

@theme inline {
${Object.entries(semanticThemeMappings)
  .map(([target, source]) => `  ${target}: var(${source});`)
  .join("\n")}
}

:root {
${declarations(primitives)}
${declarations(themes.light)}
  --ymt-target-minimum: ${primitives.target.minimum}px;
  --ymt-target-desktop: ${primitives.target.desktop}px;
  --ymt-target-touch: ${primitives.target.touch}px;
  --ymt-target-primary: ${primitives.target.primary}px;
}

html[data-theme="dark"] {
${declarations(themes.dark)}
  color-scheme: dark;
}

:where(a, button, input, select, textarea, summary, [tabindex]):focus-visible {
  outline: var(--ymt-focus-width) solid var(--ymt-color-focus-ring);
  outline-offset: var(--ymt-focus-offset);
}

.ymt-target-desktop {
  min-block-size: var(--ymt-target-desktop);
  min-inline-size: var(--ymt-target-desktop);
}

.ymt-target-touch {
  min-block-size: var(--ymt-target-touch);
  min-inline-size: var(--ymt-target-touch);
}

.ymt-target-primary {
  min-block-size: var(--ymt-target-primary);
}

.ymt-tabular-nums {
  font-variant-numeric: tabular-nums;
}
`;

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function contrastRows(mode) {
  const tokens = themes[mode];
  return accessibilityPairs
    .map((pair) => {
      const foreground = String(getTokenValue(tokens, pair.foreground));
      const background = String(getTokenValue(tokens, pair.background));
      const ratio = contrastRatio(foreground, background);
      const passed = ratio >= pair.minimum;
      return `<tr>
        <td>${escapeHtml(pair.label)}</td>
        <td><span class="swatch-dot" style="--swatch:${foreground}"></span><code>${foreground}</code></td>
        <td><span class="swatch-dot" style="--swatch:${background}"></span><code>${background}</code></td>
        <td class="ratio">${ratio.toFixed(2)}:1</td>
        <td><span class="pass ${passed ? "" : "fail"}">${passed ? "PASS" : "FAIL"} · ${pair.minimum}:1</span></td>
      </tr>`;
    })
    .join("");
}

function scaleCards(name, scale) {
  return Object.entries(scale)
    .map(
      ([step, color]) => `<article class="scale-card">
        <div class="scale-color" style="--swatch:${color}"></div>
        <strong>${name}.${step}</strong>
        <code>${color}</code>
      </article>`
    )
    .join("");
}

function semanticRows(mode) {
  return flatten(themes[mode].color)
    .map(
      ([path, value]) => `<tr>
        <td><code>color.${path.replaceAll("-", ".")}</code></td>
        <td><span class="swatch-dot" style="--swatch:${value}"></span><code>${value}</code></td>
      </tr>`
    )
    .join("");
}

const preview = `<!doctype html>
<html lang="zh-CN" data-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>一码通设计体系预览</title>
  <style>
${css.replaceAll("@theme inline", "@media all").replaceAll("@theme", "@media all")}
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      color: var(--ymt-color-text-primary);
      background: var(--ymt-color-bg-canvas);
      font: 14px/1.6 var(--ymt-font-family-body);
    }
    button, input { font: inherit; }
    code { font-family: var(--ymt-font-family-mono); font-size: 12px; }
    .shell { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 24px 0 72px; }
    .hero {
      position: relative;
      overflow: hidden;
      padding: 36px;
      border: 1px solid var(--ymt-color-border-base);
      border-radius: var(--ymt-radius-xl);
      color: #edf7ef;
      background: var(--ymt-color-chrome-sider);
      box-shadow: var(--ymt-shadow-surface);
    }
    .hero::after {
      content: "";
      position: absolute;
      inline-size: 320px;
      block-size: 320px;
      inset: -150px -80px auto auto;
      border-radius: 999px;
      background: color-mix(in srgb, var(--ymt-color-brand-primary) 35%, transparent);
    }
    .eyebrow { margin: 0 0 8px; color: #86efac; font-size: 12px; font-weight: 700; letter-spacing: .12em; }
    h1 { max-width: 720px; margin: 0; font-size: clamp(30px, 5vw, 52px); line-height: 1.1; letter-spacing: -.035em; }
    .lede { max-width: 680px; margin: 16px 0 0; color: #bdccbf; font-size: 16px; }
    .hero-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 24px; }
    .button {
      min-height: 44px;
      padding: 0 18px;
      border: 1px solid transparent;
      border-radius: var(--ymt-radius-md);
      font-weight: 600;
      cursor: pointer;
    }
    .button-primary { color: var(--ymt-color-action-on-primary); background: var(--ymt-color-action-primary); }
    .button-quiet { color: #edf7ef; border-color: #7e9583; background: transparent; }
    .section { margin-top: 36px; }
    .section-head { display: flex; align-items: end; justify-content: space-between; gap: 20px; margin-bottom: 14px; }
    h2 { margin: 0; font-size: 24px; line-height: 1.3; }
    .section-head p { max-width: 560px; margin: 0; color: var(--ymt-color-text-secondary); }
    .panel {
      overflow: hidden;
      border: 1px solid var(--ymt-color-border-base);
      border-radius: var(--ymt-radius-lg);
      background: var(--ymt-color-bg-surface);
      box-shadow: var(--ymt-shadow-surface);
    }
    .scale-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); }
    .scale-card { min-width: 0; padding: 10px; border-right: 1px solid var(--ymt-color-border-base); }
    .scale-color { height: 72px; margin-bottom: 8px; border-radius: var(--ymt-radius-sm); background: var(--swatch); }
    .scale-card strong, .scale-card code { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .scale-card code { color: var(--ymt-color-text-tertiary); }
    .tabs { display: flex; gap: 8px; margin-bottom: 12px; }
    .tab { min-height: 36px; padding: 0 14px; border: 1px solid var(--ymt-color-border-strong); border-radius: 999px; color: var(--ymt-color-text-primary); background: var(--ymt-color-bg-surface); cursor: pointer; }
    .tab[aria-selected="true"] { color: var(--ymt-color-action-on-primary); border-color: var(--ymt-color-action-primary); background: var(--ymt-color-action-primary); }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 11px 14px; border-bottom: 1px solid var(--ymt-color-border-base); text-align: left; vertical-align: middle; }
    th { color: var(--ymt-color-text-secondary); background: var(--ymt-color-chrome-table-header); font-size: 12px; }
    tr:last-child td { border-bottom: 0; }
    .swatch-dot { display: inline-block; width: 16px; height: 16px; margin-right: 8px; border: 1px solid var(--ymt-color-border-strong); border-radius: 50%; background: var(--swatch); vertical-align: -3px; }
    .pass { display: inline-flex; padding: 2px 8px; border-radius: 999px; color: var(--ymt-color-feedback-success); background: var(--ymt-color-feedback-success-bg); font-size: 12px; font-weight: 700; }
    .pass.fail { color: var(--ymt-color-feedback-danger); background: var(--ymt-color-feedback-danger-bg); }
    .ratio { font-variant-numeric: tabular-nums; font-weight: 700; }
    .type-grid, .measure-grid, .component-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; padding: 20px; }
    .type-row { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; padding: 10px 0; border-bottom: 1px solid var(--ymt-color-border-base); }
    .measure { padding: 18px; border-radius: var(--ymt-radius-md); background: var(--ymt-color-bg-muted); }
    .measure-bars { display: flex; align-items: end; gap: 8px; min-height: 76px; margin-top: 12px; }
    .measure-bar { width: var(--size); height: var(--size); max-width: 64px; max-height: 64px; border-radius: 4px; background: var(--ymt-color-brand-primary); }
    .sample-card { padding: 20px; border: 1px solid var(--ymt-color-border-base); border-radius: var(--ymt-radius-lg); background: var(--ymt-color-bg-surface); box-shadow: var(--ymt-shadow-surface); }
    .sample-card h3 { margin: 0 0 12px; font-size: 16px; }
    .field { display: grid; gap: 6px; }
    .field input { min-height: 40px; padding: 0 12px; border: 1px solid var(--ymt-color-border-strong); border-radius: var(--ymt-radius-sm); color: var(--ymt-color-text-primary); background: var(--ymt-color-bg-surface); }
    .status { display: inline-flex; align-items: center; gap: 6px; padding: 3px 9px; border-radius: 999px; color: var(--ymt-color-feedback-success); background: var(--ymt-color-feedback-success-bg); font-weight: 600; }
    .status::before { content: "✓"; }
    .tenant { display: grid; grid-template-columns: 240px 1fr; gap: 24px; padding: 24px; }
    .tenant-controls { display: grid; align-content: start; gap: 12px; }
    .tenant-controls input { width: 100%; min-height: 44px; }
    .phone { max-width: 360px; padding: 18px; border-radius: 28px; background: #f8faf8; color: #17211a; box-shadow: var(--ymt-shadow-overlay); }
    .phone-brand { display: flex; align-items: center; gap: 10px; font-weight: 700; }
    .phone-logo { display: grid; width: 36px; height: 36px; place-items: center; border-radius: 12px; color: var(--tenant-on); background: var(--tenant-brand); }
    .phone-hero { margin-top: 18px; padding: 20px; border-radius: 18px; background: color-mix(in srgb, var(--tenant-brand) 12%, white); }
    .tenant-cta { width: 100%; min-height: 48px; margin-top: 16px; border: 0; border-radius: 14px; color: var(--tenant-on); background: var(--tenant-brand); font-weight: 700; }
    .footer { margin-top: 36px; color: var(--ymt-color-text-tertiary); text-align: center; }
    [hidden] { display: none !important; }
    @media (max-width: 760px) {
      .shell { width: min(100% - 20px, 1180px); padding-top: 10px; }
      .hero { padding: 26px 22px; }
      .section-head { align-items: start; flex-direction: column; gap: 6px; }
      .type-grid, .measure-grid, .component-grid, .tenant { grid-template-columns: 1fr; }
      .table-wrap { overflow-x: auto; }
      th, td { white-space: nowrap; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header class="hero">
      <p class="eyebrow">YIMATONG DESIGN SYSTEM · 0.1.0</p>
      <h1>自然可信，清晰可用。</h1>
      <p class="lede">Admin、Platform 与消费者 H5 共用一套语义根基。品牌表达可以有温度，关键操作和信息必须可读、可达、可检查。</p>
      <div class="hero-actions">
        <button class="button button-primary" type="button">创建扫码活动</button>
        <button class="button button-quiet" id="theme-toggle" type="button">切换深色主题</button>
      </div>
    </header>

    <section class="section" aria-labelledby="palette-title">
      <div class="section-head"><div><p class="eyebrow">FOUNDATIONS</p><h2 id="palette-title">品牌色阶</h2></div><p>500 是品牌锚点；可读文字和主操作使用通过对比度验证的更深或更浅语义色。</p></div>
      <div class="panel"><div class="scale-grid">${scaleCards("brand", primitives.color.brand)}</div><div class="scale-grid">${scaleCards("accent", primitives.color.accent)}</div></div>
    </section>

    <section class="section" aria-labelledby="semantic-title">
      <div class="section-head"><div><p class="eyebrow">SEMANTIC TOKENS</p><h2 id="semantic-title">语义颜色</h2></div><p>切换查看 light / dark。业务代码消费语义名，不直接消费色阶。</p></div>
      <div class="tabs" role="tablist" aria-label="语义主题">
        <button class="tab" role="tab" aria-selected="true" data-semantic-tab="light">Light</button>
        <button class="tab" role="tab" aria-selected="false" data-semantic-tab="dark">Dark</button>
      </div>
      <div class="panel table-wrap"><table><thead><tr><th>Token</th><th>值</th></tr></thead><tbody id="semantic-light">${semanticRows("light")}</tbody><tbody id="semantic-dark" hidden>${semanticRows("dark")}</tbody></table></div>
    </section>

    <section class="section" aria-labelledby="contrast-title">
      <div class="section-head"><div><p class="eyebrow">WCAG 2.2</p><h2 id="contrast-title">可访问性检查</h2></div><p>构建脚本与测试使用同一份配对清单；任何 FAIL 都会阻止交付。</p></div>
      <div class="tabs" role="tablist" aria-label="对比度主题">
        <button class="tab" role="tab" aria-selected="true" data-contrast-tab="light">Light</button>
        <button class="tab" role="tab" aria-selected="false" data-contrast-tab="dark">Dark</button>
      </div>
      <div class="panel table-wrap"><table><thead><tr><th>配对</th><th>前景</th><th>背景</th><th>比值</th><th>门禁</th></tr></thead><tbody id="contrast-light">${contrastRows("light")}</tbody><tbody id="contrast-dark" hidden>${contrastRows("dark")}</tbody></table></div>
    </section>

    <section class="section" aria-labelledby="type-title">
      <div class="section-head"><div><p class="eyebrow">TYPOGRAPHY</p><h2 id="type-title">排版与度量</h2></div><p>中文系统字体优先；Admin 基准 14px，H5 基准 16px，数字默认支持等宽展示。</p></div>
      <div class="panel type-grid">
        <div>${Object.entries(primitives.font.size)
          .map(
            ([name, size]) =>
              `<div class="type-row"><span style="font-size:${size}px">${size}px 一码通增长</span><code>${name}</code></div>`
          )
          .join("")}</div>
        <div class="measure-grid">
          <div class="measure"><strong>间距 · 4px 基数</strong><div class="measure-bars">${Object.values(
            primitives.space
          )
            .map(
              (size) =>
                `<i class="measure-bar" style="--size:${size}px" title="${size}px"></i>`
            )
            .join("")}</div></div>
          <div class="measure"><strong>交互目标</strong><p>AA 24 · 桌面 32 · 触控 44 · 主 CTA 48</p><button class="button button-primary" type="button">48px 主操作</button></div>
        </div>
      </div>
    </section>

    <section class="section" aria-labelledby="component-title">
      <div class="section-head"><div><p class="eyebrow">COMPONENT RECIPES</p><h2 id="component-title">核心样张</h2></div><p>这些是规范样张，不是共享组件库；实际页面继续使用 antd 或 H5 原生组件。</p></div>
      <div class="panel component-grid">
        <article class="sample-card"><h3>操作与状态</h3><div class="hero-actions"><button class="button button-primary" type="button">发布页面</button><button class="button tab" type="button">保存草稿</button><span class="status">已启用</span></div></article>
        <article class="sample-card"><h3>表单与焦点</h3><label class="field"><span>活动名称</span><input value="春季扫码有礼" aria-describedby="field-help"><small id="field-help">对外展示的活动名称</small></label></article>
      </div>
    </section>

    <section class="section" aria-labelledby="tenant-title">
      <div class="section-head"><div><p class="eyebrow">TENANT BRANDING</p><h2 id="tenant-title">租户定制安全槽位</h2></div><p>租户只提供品牌锚点；按钮文字自动选择黑/白，焦点、正文和错误色不可覆盖。</p></div>
      <div class="panel tenant">
        <div class="tenant-controls"><label class="field"><span>品牌主色</span><input id="tenant-color" type="color" value="#16a34a"></label><p id="tenant-ratio" class="ratio"></p></div>
        <div class="phone" id="tenant-phone" style="--tenant-brand:#16a34a;--tenant-on:#000000"><div class="phone-brand"><span class="phone-logo">禾</span>禾野食品</div><div class="phone-hero"><small>扫码验真</small><h3>来自云南高原的安心玉米</h3><p>批次、检测和品牌故事，一次扫码看清。</p><button class="tenant-cta" type="button">查看完整溯源</button></div></div>
      </div>
    </section>

    <p class="footer">生成自 @yimatong/design-tokens · 修改 token 后运行 pnpm build</p>
  </main>
  <script>
    const root = document.documentElement;
    const themeToggle = document.querySelector("#theme-toggle");
    themeToggle.addEventListener("click", () => {
      const next = root.dataset.theme === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      themeToggle.textContent = next === "dark" ? "切换浅色主题" : "切换深色主题";
    });
    function bindTabs(name) {
      document.querySelectorAll("[data-" + name + "-tab]").forEach((tab) => {
        tab.addEventListener("click", () => {
          const mode = tab.dataset[name + "Tab"];
          document.querySelectorAll("[data-" + name + "-tab]").forEach((item) => item.setAttribute("aria-selected", String(item === tab)));
          document.querySelector("#" + name + "-light").hidden = mode !== "light";
          document.querySelector("#" + name + "-dark").hidden = mode !== "dark";
        });
      });
    }
    bindTabs("semantic");
    bindTabs("contrast");
    function luminance(hex) {
      const rgb = hex.match(/[a-f\\d]{2}/gi).map((part) => parseInt(part, 16) / 255).map((channel) => channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4);
      return .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2];
    }
    function ratio(a, b) {
      const first = luminance(a), second = luminance(b);
      return (Math.max(first, second) + .05) / (Math.min(first, second) + .05);
    }
    const tenantColor = document.querySelector("#tenant-color");
    const tenantPhone = document.querySelector("#tenant-phone");
    const tenantRatio = document.querySelector("#tenant-ratio");
    function updateTenant() {
      const color = tenantColor.value;
      const black = ratio("#000000", color);
      const white = ratio("#ffffff", color);
      const on = black >= white ? "#000000" : "#ffffff";
      tenantPhone.style.setProperty("--tenant-brand", color);
      tenantPhone.style.setProperty("--tenant-on", on);
      tenantRatio.textContent = "按钮文字 " + on + " · " + Math.max(black, white).toFixed(2) + ":1 · PASS";
    }
    tenantColor.addEventListener("input", updateTenant);
    updateTenant();
  </script>
</body>
</html>
`;

const outputs = [
  [resolve(packageRoot, "dist/theme.css"), css],
  [resolve(packageRoot, "preview/index.html"), preview],
];

let drifted = false;
for (const [path, content] of outputs) {
  if (checkOnly) {
    let current = "";
    try {
      current = readFileSync(path, "utf8");
    } catch {
      // Missing generated files are reported as drift below.
    }
    if (current !== content) {
      console.error(`Generated file is stale: ${path}`);
      drifted = true;
    }
  } else {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, content);
    console.log(`Generated ${path}`);
  }
}

if (drifted) {
  process.exitCode = 1;
}
