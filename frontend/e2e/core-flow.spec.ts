/**
 * Core user flow E2E test
 *
 * Flow: 登录 → 创建产品 → 生成码 → 配置页面 → 扫码 → 领取权益
 *
 * Prerequisites (seeded by global-setup.ts):
 *   - tenant + admin account
 *   - brand, product, SKU, code batch (activated), page template (published),
 *     campaign, benefit
 */

import { test, expect } from "@playwright/test";
import { readFileSync } from "fs";
import path from "path";

interface TestContext {
  token: string;
  email: string;
  tenantId: string;
  brandId: string;
  productId: string;
  skuId: string;
  codeBatchId: string;
  publicId: string;
  pageTemplateId: string;
  pageVersionId: string;
  campaignId: string;
  benefitId: string;
}

function loadContext(): TestContext {
  const raw = readFileSync(
    path.join(__dirname, ".auth", "context.json"),
    "utf-8"
  );
  return JSON.parse(raw) as TestContext;
}

const ctx = loadContext();

// Reuse the authenticated state from global-setup so we skip the login UI
test.use({ storageState: path.join(__dirname, ".auth", "state.json") });

/** Helper: reliably open an Ant Design Select dropdown and pick an option */
async function antdSelect(page: any, testId: string, optionText?: string) {
  const select = page.locator(`[data-testid="${testId}"]`).first();
  await select.click();
  // Wait for dropdown to appear (dev-server first compile can be slow)
  await page.waitForSelector(".ant-select-dropdown:visible", { timeout: 8000 });
  const dropdown = page
    .locator(".ant-select-dropdown")
    .filter({ visible: true });
  if (optionText) {
    await dropdown.getByText(optionText).first().click({ force: true });
  } else {
    // Pick the first option in the visible dropdown
    const firstOption = dropdown.locator(".ant-select-item-option").first();
    await firstOption.click({ force: true });
  }
  await page.waitForTimeout(200);
}

/** Helper: click confirm button inside an Ant Design Popconfirm tooltip */
async function antdPopconfirmConfirm(page: any) {
  // Popconfirm in Ant Design 6 renders as an overlay; use the visible confirm button
  await page.getByRole("button", { name: /确 定/ }).last().click();
}

test.describe("核心用户流程", () => {
  // The E2E tenant completed onboarding server-side in global-setup; mirror
  // the dismissed-wizard sessionStorage flag so the welcome overlay (which
  // only persists per browser context) can never intercept page clicks.
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() =>
      sessionStorage.setItem("onboarding_dismissed", "1")
    );
  });

  test("已登录状态访问工作台", async ({ page }) => {
    // storageState 已预设 cookie + localStorage，直接验证 dashboard 可访问
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "经营看板" })).toBeVisible({
      timeout: 10000,
    });
  });

  test("创建产品", async ({ page }) => {
    await page.goto("/products");
    await expect(page.getByRole("heading", { name: "产品管理" })).toBeVisible({
      timeout: 15000,
    });
    await page.waitForTimeout(1500);

    await page.getByRole("button", { name: /新建产品/ }).click();
    await expect(
      page.locator(".ant-modal-title").filter({ hasText: "新建产品" })
    ).toBeVisible();

    const productName = `E2E-Product-${Date.now()}`;
    await page.getByTestId("product-name-input").fill(productName);
    await antdSelect(page, "product-brand-select");
    // 品类是 showSearch 下拉，输入即落值（onSearch 同步进表单）
    await page.getByTestId("product-category-input").click();
    await page.keyboard.type("测试品类");
    await page.keyboard.press("Escape");

    await page
      .locator(".ant-modal-footer")
      .getByRole("button", { name: /创建并进入工作台/ })
      .click();

    // Verify modal closes and the product workbench opens with the new name
    await expect(
      page.locator(".ant-modal-title").filter({ hasText: "新建产品" })
    ).not.toBeVisible();
    await expect(page).toHaveURL(/\/products\/[0-9a-f-]+$/);
    await expect(page.getByText(productName).first()).toBeVisible({
      timeout: 10000,
    });
  });

  test("生成码批次并激活", async ({ page }) => {
    await page.goto("/codes");
    await expect(page.getByRole("heading", { name: "码管理" })).toBeVisible({
      timeout: 15000,
    });
    await page.waitForTimeout(1500);

    await page.getByRole("button", { name: /生成码批次/ }).click();
    await expect(
      page.locator(".ant-modal-title").filter({ hasText: "生成码批次" })
    ).toBeVisible();

    // 权威码批次链路：产品 → SKU → 生产批次（global-setup 已备好各一条）
    await antdSelect(page, "code-batch-product-select", "E2E Product");
    await page.waitForTimeout(500);
    await antdSelect(page, "code-batch-sku-select");
    await page.waitForTimeout(500);
    await antdSelect(page, "code-batch-production-batch-select");
    await page.getByTestId("code-batch-quantity-input").fill("10");

    await page
      .locator(".ant-modal-footer")
      .getByRole("button", { name: /^生成码批次/ })
      .click();

    await expect(
      page.locator(".ant-modal-title").filter({ hasText: "生成码批次" })
    ).not.toBeVisible();

    // 完整生命周期：已生成 → 导出码表 → 印刷中 → 已交付 → 激活。
    // 新批次按创建时间排在首行；用 tbody 首行定位以避开状态文案变化。
    const row = page.locator("tbody tr").first();
    await expect(row.getByText("已生成")).toBeVisible({ timeout: 10000 });
    // 码项生成略滞后于批次状态，等数量落定再导出（空码导出会被跳过）
    await expect(row.getByText(/个物理码/)).toBeVisible({ timeout: 10000 });

    await row.getByRole("button", { name: /导出码表/ }).click();
    // 导出/交付两个 Modal 同时挂载且字段 name 都是 reason，DOM id 冲突会使
    // getByLabel 锚到隐藏控件；按 placeholder 精确定位
    await page
      .locator(".ant-modal")
      .getByPlaceholder("例如：月度经营复盘")
      .fill("e2e 生命周期验证导出");
    await page
      .locator(".ant-modal-footer")
      .getByRole("button", { name: "准备导出" })
      .click();
    await expect(row.getByText("已导出")).toBeVisible({ timeout: 15000 });

    await row.getByRole("button", { name: "标记印刷中" }).click();
    await expect(row.getByText("印刷中")).toBeVisible({ timeout: 10000 });

    await row.getByRole("button", { name: "标记已交付" }).click();
    await page
      .locator(".ant-modal")
      .getByPlaceholder("例如：华东印刷供应商")
      .fill("E2E 印刷供应商");
    await page
      .locator(".ant-modal")
      .getByPlaceholder("说明本次交付用途或交付单据")
      .fill("e2e 生命周期验证交付");
    await page
      .locator(".ant-modal-footer")
      .getByRole("button", { name: "确认交付" })
      .click();
    await expect(row.getByText("已交付")).toBeVisible({ timeout: 10000 });

    await row.getByRole("button", { name: "激活码批次" }).click();
    await page
      .locator(".ant-modal-confirm")
      .getByRole("button", { name: "确认激活" })
      .click();
    await expect(row.getByText("已激活")).toBeVisible({ timeout: 10000 });
  });

  test("配置页面并发布", async ({ page }) => {
    await page.goto("/pages");
    await expect(page.getByRole("heading", { name: "页面管理" })).toBeVisible({
      timeout: 15000,
    });
    await page.waitForTimeout(1500);

    await page.getByRole("button", { name: /新建页面/ }).click();
    await expect(
      page.locator(".ant-modal-title").filter({ hasText: "新建页面" })
    ).toBeVisible();

    const pageName = `E2E-Page-${Date.now()}`;
    await page.getByLabel("页面名称").fill(pageName);
    await page.getByLabel("页面类型").click();
    await page
      .locator(".ant-select-dropdown:visible")
      .getByText("产品信息")
      .first()
      .click();
    // 关联产品是发布就绪门禁的阻断项（未关联则消费者扫码不会命中）
    await page.locator(".ant-modal").getByLabel("关联产品").click();
    await page
      .locator(".ant-select-dropdown:visible")
      .getByText("E2E Product")
      .first()
      .click();
    await page.getByRole("radio", { name: "从空白页开始" }).check();

    await page
      .locator(".ant-modal-footer")
      .getByRole("button", { name: /创建并编辑草稿/ })
      .click();

    // 创建后直接进入页面编辑器（/pages/{id}/edit）
    await expect(page).toHaveURL(/\/pages\/[0-9a-f-]+\/edit$/, {
      timeout: 15000,
    });

    // 编辑器头部发布草稿 → 确认弹窗（点击后先异步就绪检查）→ 发布成功。
    // 按钮可达名含图标前缀 send；确认按钮用带超时的 click 自等待弹窗。
    await page
      .getByRole("button", { name: /发布草稿/ })
      .first()
      .click();
    await page
      .locator(".ant-modal-confirm")
      .getByRole("button", { name: "发布草稿" })
      .click({ timeout: 30_000 });
    await expect(page.getByText("草稿已发布")).toBeVisible({ timeout: 15_000 });
  });

  test("创建活动", async ({ page }) => {
    await page.goto("/campaigns");
    await expect(page.getByRole("heading", { name: "活动管理" })).toBeVisible({
      timeout: 15000,
    });
    await page.waitForTimeout(1500);

    await page.getByRole("button", { name: /新建活动/ }).click();
    await expect(
      page.locator(".ant-modal-title").filter({ hasText: "新建活动" })
    ).toBeVisible();

    const campaignName = `E2E-Campaign-${Date.now()}`;
    await page.getByTestId("campaign-name-input").fill(campaignName);
    // 系统活动类型在新建时锁定默认值；关联产品为必填
    await page.locator(".ant-modal").getByLabel("关联产品").click();
    await page
      .locator(".ant-select-dropdown:visible")
      .getByText("E2E Product")
      .first()
      .click();

    // 投放时间 RangePicker（showTime，直接键入起止）
    await page.getByLabel("投放时间").click();
    await page.keyboard.type("2026-01-01 00:00");
    await page.keyboard.press("Enter");
    await page.keyboard.type("2026-12-31 23:59");
    await page.keyboard.press("Enter");

    await page
      .locator(".ant-modal-footer")
      .getByRole("button", { name: /创建草稿并配置权益/ })
      .click();

    // 新建流程按产品模板自动命名（"{产品} · {品类}首扫领券复购活动"）并
    // 自动带出草稿活动 + 1 个权益；断言草稿行真实呈现
    await expect(
      page.locator(".ant-modal-title").filter({ hasText: "新建活动" })
    ).not.toBeVisible();
    const draftRow = page
      .locator("tr")
      .filter({ hasText: "草稿" })
      .filter({ hasText: "E2E Product" })
      .first();
    await expect(draftRow).toBeVisible({ timeout: 10000 });
  });

  test("创建权益", async ({ page }) => {
    await page.goto("/benefits");
    await expect(page.getByRole("heading", { name: "权益管理" })).toBeVisible({
      timeout: 15000,
    });
    await page.waitForTimeout(1500);

    await page.getByRole("button", { name: /新建权益/ }).click();
    await expect(
      page.locator(".ant-modal-title").filter({ hasText: "新建权益" })
    ).toBeVisible();

    const benefitName = `E2E-Benefit-${Date.now()}`;
    await page.getByTestId("benefit-name-input").fill(benefitName);
    // 权益类型是单选卡片组（平台券等），活动关联已移入活动侧配置
    await page
      .getByTestId("benefit-type-select")
      .getByRole("radio", { name: /平台券/ })
      .check();
    await page.getByTestId("benefit-stock-input").fill("100");
    await page.getByTestId("benefit-limit-input").fill("1");

    await page
      .locator(".ant-modal-footer")
      .getByRole("button", { name: /创建权益/ })
      .click();

    await expect(
      page.locator(".ant-modal-title").filter({ hasText: "新建权益" })
    ).not.toBeVisible();
    await expect(page.getByText(benefitName)).toBeVisible();
  });

  test("H5 扫码并领取权益", async ({ browser }) => {
    const h5Context = await browser.newContext({
      baseURL: "http://localhost:3003",
    });
    const page = await h5Context.newPage();

    await page.goto(`/c/${ctx.publicId}`);
    await expect(page.locator("h1, h2").first()).toBeVisible({
      timeout: 15000,
    });

    const claimBtn = page.getByRole("button", { name: "立即领取" });
    if (await claimBtn.isVisible().catch(() => false)) {
      await claimBtn.click();
      await expect(page.getByText("已领取")).toBeVisible({ timeout: 10000 });
    }

    await h5Context.close();
  });
});
