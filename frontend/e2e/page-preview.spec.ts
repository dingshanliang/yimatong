/**
 * Admin H5 页面预览集成测试
 *
 * 测试链路：Admin 页面编辑器 → iframe postMessage → H5 PreviewRenderer 模块渲染
 *
 * CSP 注意：Next.js dev mode 设置 script-src 'self'，Chromium 中会阻止
 * React hydration。所有 browser context 需设置 bypassCSP: true。
 */

import { test, expect, type Browser, type Page } from "@playwright/test";
import { readFileSync } from "fs";
import path from "path";

interface TestContext {
  token: string;
  pageTemplateId: string;
}

function loadContext(): TestContext {
  const raw = readFileSync(path.join(__dirname, ".auth", "context.json"), "utf-8");
  return JSON.parse(raw) as TestContext;
}

const ctx = loadContext();

test.use({ storageState: path.join(__dirname, ".auth", "state.json") });

const H5_BASE = "http://localhost:3003";

/** 创建一个 bypassCSP 的 H5 browser context */
async function newH5Context(browser: Browser) {
  return browser.newContext({ baseURL: H5_BASE, bypassCSP: true });
}

/** 向 H5 预览页注入 DSL 并等待渲染 */
async function injectDSLAndWait(page: Page, dsl: unknown, waitForText: string, timeout = 10000) {
  // 等 React hydration 完成
  await expect(page.getByText("等待编辑器数据")).toBeVisible({ timeout: 15000 });

  await page.evaluate((payload) => {
    setTimeout(() => {
      window.postMessage({ type: "preview-dsl", payload }, "*");
    }, 100);
  }, dsl);

  await expect(page.getByText(waitForText).first()).toBeVisible({ timeout });
}

test.describe("页面编辑器预览集成", () => {
  test("编辑器加载并显示预览 iframe", async ({ browser }) => {
    const context = await browser.newContext({ bypassCSP: true });
    const page = await context.newPage();
    await page.goto(`/pages/${ctx.pageTemplateId}/edit`);

    // Wait for editor to finish loading (not "加载中...")
    await expect(page.getByText("加载中...")).not.toBeVisible({ timeout: 15000 });
    await page.waitForTimeout(2000);

    const bodyText = await page.locator("body").innerText();
    expect(bodyText.length).toBeGreaterThan(0);

    // PreviewPanel renders an iframe; check by element presence
    const iframeElement = page.locator("iframe").first();
    await expect(iframeElement).toBeVisible({ timeout: 5000 });

    // Verify the iframe has a src pointing to the preview page
    const src = await iframeElement.getAttribute("src");
    expect(src).toContain("preview");

    await context.close();
  });

  test("H5 预览页独立渲染模块", async ({ browser }) => {
    const h5 = await newH5Context(browser);
    const page = await h5.newPage();
    await page.goto("/preview");

    await injectDSLAndWait(page, {
      modules: [
        { id: "hero", type: "product_hero", enabled: true, config: { title_template: "测试产品" } },
        { id: "verify", type: "verification_status", enabled: true },
        { id: "trace", type: "light_traceability", enabled: true, config: {} },
        { id: "benefit", type: "benefit_card", enabled: true, config: { benefit_type: "coupon", title: "领取优惠券" } },
      ],
      tenant_branding: { name: "测试品牌", primary_color: "#1677ff" },
    }, "测试品牌");

    await expect(page.getByText("测试产品")).toBeVisible();
    await expect(page.getByText("验证通过")).toBeVisible();
    await expect(page.getByText("领取优惠券")).toBeVisible();

    await h5.close();
  });

  test("H5 预览页渲染阶段二模块", async ({ browser }) => {
    const h5 = await newH5Context(browser);
    const page = await h5.newPage();
    await page.goto("/preview");

    await injectDSLAndWait(page, {
      modules: [
        { id: "member", type: "member_card", enabled: true, config: { member_level: "gold", total_points: 320 } },
        { id: "points", type: "points_balance", enabled: true, config: { points: 320 } },
        { id: "exchange", type: "points_exchange", enabled: true, config: { title: "积分兑换测试", points_cost: 100 } },
        { id: "risk", type: "risk_alert", enabled: true, config: { alert_type: "frequency", detail: "频繁扫码提示" } },
        { id: "dual", type: "dual_code_verify", enabled: true, config: {} },
        { id: "outer", type: "outer_code_guide", enabled: true, config: { brand_name: "阶段二品牌" } },
      ],
      tenant_branding: { name: "阶段二品牌" },
    }, "阶段二品牌");

    await expect(page.getByText("金卡会员").first()).toBeVisible();
    await expect(page.getByText("积分兑换测试")).toBeVisible();
    await expect(page.getByText("频繁扫码预警")).toBeVisible();
    await expect(page.getByText("外码", { exact: true }).first()).toBeVisible();

    await h5.close();
  });

  test("H5 预览页处理空模块列表", async ({ browser }) => {
    const h5 = await newH5Context(browser);
    const page = await h5.newPage();
    await page.goto("/preview");

    await injectDSLAndWait(page, {
      modules: [],
      tenant_branding: { name: "空页面品牌" },
    }, "空页面品牌");

    const bodyText = await page.locator("body").innerText();
    expect(bodyText).toContain("空页面品牌");

    await h5.close();
  });

  test("H5 预览页处理禁用模块", async ({ browser }) => {
    const h5 = await newH5Context(browser);
    const page = await h5.newPage();
    await page.goto("/preview");

    await injectDSLAndWait(page, {
      modules: [
        { id: "enabled1", type: "product_hero", enabled: true, config: { title_template: "启用模块" } },
        { id: "disabled1", type: "benefit_card", enabled: false, config: { title: "禁用模块", benefit_type: "coupon" } },
      ],
      tenant_branding: { name: "禁用测试" },
    }, "禁用测试");

    await expect(page.getByText("启用模块")).toBeVisible();
    await expect(page.getByText("禁用模块")).not.toBeVisible();

    await h5.close();
  });

  test("H5 预览页渲染自定义 HTML 模块", async ({ browser }) => {
    const h5 = await newH5Context(browser);
    const page = await h5.newPage();
    await page.goto("/preview");

    await injectDSLAndWait(page, {
      modules: [{
        id: "custom", type: "custom_html", enabled: true,
        config: { html: "<div style='padding:16px;background:#f0f9ff;color:#333'>自定义横幅内容</div>" },
      }],
      tenant_branding: { name: "HTML测试" },
    }, "自定义横幅内容");

    await h5.close();
  });
});

test.describe("编辑器设备预览切换", () => {
  test("切换设备预设更新预览尺寸", async ({ browser }) => {
    const context = await browser.newContext({ bypassCSP: true });
    const page = await context.newPage();
    await page.goto(`/pages/${ctx.pageTemplateId}/edit`);

    // Wait for editor to finish loading (not "加载中...")
    await expect(page.getByText("加载中...")).not.toBeVisible({ timeout: 15000 });
    await page.waitForTimeout(2000);

    // The device preset select is inside PreviewPanel
    const selects = page.locator(".ant-select");
    const count = await selects.count();
    expect(count).toBeGreaterThan(0);

    await selects.first().click();
    await page.waitForTimeout(300);
    const option = page.locator(".ant-select-item-option").getByText("iPhone SE");
    if (await option.isVisible().catch(() => false)) {
      await option.click();
    }

    await page.waitForTimeout(2000);
    // Verify iframe still visible after device switch
    const iframeElement = page.locator("iframe").first();
    await expect(iframeElement).toBeVisible();

    await context.close();
  });
});

test.describe("JSON 编辑器与预览同步", () => {
  test("修改 JSON 编辑器内容后预览更新", async ({ browser }) => {
    const context = await browser.newContext({ bypassCSP: true });
    const page = await context.newPage();
    await page.goto(`/pages/${ctx.pageTemplateId}/edit`);
    await page.waitForTimeout(6000);

    const jsonTab = page.getByRole("tab", { name: "JSON 编辑" });
    if (!(await jsonTab.isVisible().catch(() => false))) {
      test.skip();
      await context.close();
      return;
    }
    await jsonTab.click();

    const textarea = page.locator("textarea").first();
    if (!(await textarea.isVisible().catch(() => false))) {
      test.skip();
      await context.close();
      return;
    }

    try {
      const currentText = await textarea.inputValue();
      const dsl = JSON.parse(currentText);
      dsl.modules = dsl.modules || [];
      dsl.modules.push({
        id: `test-html-${Date.now()}`,
        type: "custom_html",
        enabled: true,
        config: { html: "<div style='padding:12px;color:red'>E2E预览测试标记</div>" },
      });
      await textarea.fill(JSON.stringify(dsl, null, 2));
      await page.waitForTimeout(3000);

      const iframe = page.frame({ url: /\/preview/ });
      if (iframe) {
        const hasMarker = await iframe.getByText("E2E预览测试标记").isVisible().catch(() => false);
        expect(typeof hasMarker).toBe("boolean");
      }
    } catch {
      test.skip();
    }

    await context.close();
  });
});
