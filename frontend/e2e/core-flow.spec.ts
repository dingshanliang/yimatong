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
  const raw = readFileSync(path.join(__dirname, ".auth", "context.json"), "utf-8");
  return JSON.parse(raw) as TestContext;
}

const ctx = loadContext();

// Reuse the authenticated state from global-setup so we skip the login UI
test.use({ storageState: path.join(__dirname, ".auth", "state.json") });

/** Helper: reliably open an Ant Design Select dropdown and pick an option via keyboard */
async function antdSelect(page: any, testId: string, optionText?: string) {
  const select = page.locator(`[data-testid="${testId}"]`).first();
  await select.click();
  await page.waitForTimeout(300);
  const dropdown = page.locator(".ant-select-dropdown").filter({ visible: true });
  if (optionText) {
    await dropdown.getByText(optionText).first().click({ force: true });
  } else {
    // Use keyboard navigation for better reliability
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
  }
  await page.waitForTimeout(200);
}

/** Helper: click confirm button inside an Ant Design Popconfirm tooltip */
async function antdPopconfirmConfirm(page: any) {
  // Popconfirm in Ant Design 6 renders as an overlay; use the visible confirm button
  await page.getByRole("button", { name: /确 定/ }).last().click();
}

test.describe("核心用户流程", () => {
  test("登录后跳转工作台", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("E2E Admin")).toBeVisible();
    await expect(page.getByRole("heading", { name: "工作台" })).toBeVisible();
  });

  test("创建产品", async ({ page }) => {
    await page.goto("/products");
    await expect(page.getByRole("heading", { name: "产品管理" })).toBeVisible();
    await page.waitForTimeout(1500);

    await page.getByRole("button", { name: /新建产品/ }).click();
    await expect(page.locator(".ant-modal-title").filter({ hasText: "新建产品" })).toBeVisible();

    const productName = `E2E-Product-${Date.now()}`;
    await page.getByTestId("product-name-input").fill(productName);
    await antdSelect(page, "product-brand-select");
    await page.getByTestId("product-category-input").fill("测试品类");

    await page.locator(".ant-modal-footer").getByRole("button", { name: /确 定/ }).click();

    // Verify modal closes and product appears in table (message auto-dismisses in 3s)
    await expect(page.locator(".ant-modal-title").filter({ hasText: "新建产品" })).not.toBeVisible();
    await expect(page.getByText(productName)).toBeVisible();
  });

  test("生成码批次并激活", async ({ page }) => {
    await page.goto("/codes");
    await expect(page.getByRole("heading", { name: "码管理" })).toBeVisible();
    await page.waitForTimeout(1500);

    await page.getByRole("button", { name: /生成码批次/ }).click();
    await expect(page.locator(".ant-modal-title").filter({ hasText: "生成码批次" })).toBeVisible();

    // Select the global-setup product which has SKUs
    await antdSelect(page, "code-batch-product-select", "E2E Product");
    await page.waitForTimeout(500);
    await antdSelect(page, "code-batch-sku-select");

    const batchCode = `E2E-BATCH-${Date.now()}`;
    await page.getByTestId("code-batch-code-input").fill(batchCode);
    await page.getByTestId("code-batch-quantity-input").fill("10");

    await page.locator(".ant-modal-footer").getByRole("button", { name: /确 定/ }).click();

    await expect(page.locator(".ant-modal-title").filter({ hasText: "生成码批次" })).not.toBeVisible();
    await expect(page.getByText(batchCode)).toBeVisible();

    const activateBtn = page.getByRole("button", { name: "激活" }).first();
    if (await activateBtn.isVisible().catch(() => false)) {
      await activateBtn.click();
      await antdPopconfirmConfirm(page);
      // Wait for status update in table instead of transient message
      await expect(page.locator("tr", { hasText: batchCode }).getByText("已激活")).toBeVisible({ timeout: 10000 });
    }
  });

  test("配置页面并发布", async ({ page }) => {
    await page.goto("/pages");
    await expect(page.getByRole("heading", { name: "页面管理" })).toBeVisible();
    await page.waitForTimeout(1500);

    await page.getByRole("button", { name: /新建页面/ }).click();
    await expect(page.locator(".ant-modal-title").filter({ hasText: "新建页面模板" })).toBeVisible();

    const pageName = `E2E-Page-${Date.now()}`;
    await page.getByTestId("page-template-name-input").fill(pageName);
    await antdSelect(page, "page-template-type-select", "产品信息");

    await page.locator(".ant-modal-footer").getByRole("button", { name: /确 定/ }).click();

    // Wait for modal to close and new template to appear
    await expect(page.locator(".ant-modal-title").filter({ hasText: "新建页面模板" })).not.toBeVisible();
    await expect(page.getByText(pageName)).toBeVisible();

    // Open version management
    const row = page.locator("tr", { hasText: pageName });
    await row.getByRole("button", { name: "版本管理" }).click();
    await expect(page.locator(".ant-modal-title").filter({ hasText: "版本管理" })).toBeVisible();

    // Publish the draft version
    const publishBtn = page.getByRole("button", { name: "发布" }).first();
    await publishBtn.click();
    await antdPopconfirmConfirm(page);

    // Verify status changes to "已发布" in the version modal
    await expect(page.locator("tr", { hasText: "v1" }).getByText("已发布")).toBeVisible({ timeout: 10000 });
  });

  test("创建活动", async ({ page }) => {
    await page.goto("/campaigns");
    await expect(page.getByRole("heading", { name: "活动管理" })).toBeVisible();
    await page.waitForTimeout(1500);

    await page.getByRole("button", { name: /新建活动/ }).click();
    await expect(page.locator(".ant-modal-title").filter({ hasText: "新建活动" })).toBeVisible();

    const campaignName = `E2E-Campaign-${Date.now()}`;
    await page.getByTestId("campaign-name-input").fill(campaignName);
    await antdSelect(page, "campaign-type-select", "优惠券");
    await page.getByTestId("campaign-start-input").fill("2026-01-01T00:00:00");
    await page.getByTestId("campaign-end-input").fill("2026-12-31T23:59:59");

    await page.locator(".ant-modal-footer").getByRole("button", { name: /确 定/ }).click();

    await expect(page.locator(".ant-modal-title").filter({ hasText: "新建活动" })).not.toBeVisible();
    await expect(page.getByText(campaignName)).toBeVisible();
  });

  test("创建权益", async ({ page }) => {
    await page.goto("/benefits");
    await expect(page.getByRole("heading", { name: "权益管理" })).toBeVisible();
    await page.waitForTimeout(1500);

    await page.getByRole("button", { name: /新建权益/ }).click();
    await expect(page.locator(".ant-modal-title").filter({ hasText: "新建权益" })).toBeVisible();

    const benefitName = `E2E-Benefit-${Date.now()}`;
    await page.getByTestId("benefit-name-input").fill(benefitName);
    await antdSelect(page, "benefit-type-select", "平台券");
    await antdSelect(page, "benefit-campaign-select");
    await page.getByTestId("benefit-stock-input").fill("100");
    await page.getByTestId("benefit-limit-input").fill("1");

    await page.locator(".ant-modal-footer").getByRole("button", { name: /确 定/ }).click();

    await expect(page.locator(".ant-modal-title").filter({ hasText: "新建权益" })).not.toBeVisible();
    await expect(page.getByText(benefitName)).toBeVisible();
  });

  test("H5 扫码并领取权益", async ({ browser }) => {
    const h5Context = await browser.newContext({
      baseURL: "http://localhost:3003",
    });
    const page = await h5Context.newPage();

    await page.goto(`/c/${ctx.publicId}`);
    await expect(page.locator("h1, h2").first()).toBeVisible({ timeout: 15000 });

    const claimBtn = page.getByRole("button", { name: "立即领取" });
    if (await claimBtn.isVisible().catch(() => false)) {
      await claimBtn.click();
      await expect(page.getByText("已领取")).toBeVisible({ timeout: 10000 });
    }

    await h5Context.close();
  });
});
