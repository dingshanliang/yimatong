import { expect, test } from "@playwright/test";

const API_BASE = process.env.YIMATONG_U02B_API_BASE || "http://127.0.0.1:18250";

async function confirmPopover(page: import("@playwright/test").Page) {
  const popover = page.locator(".ant-popover").filter({ hasText: "确 定" });
  await popover.getByRole("button", { name: "确 定" }).click();
}

async function dismissOnboarding(page: import("@playwright/test").Page) {
  const onboarding = page
    .getByRole("dialog")
    .filter({ hasText: "欢迎使用一码通" });
  const visible = await onboarding
    .waitFor({ state: "visible", timeout: 5_000 })
    .then(() => true)
    .catch(() => false);
  if (visible) {
    await onboarding.getByText("稍后再说", { exact: true }).click();
    await expect(onboarding).toBeHidden();
  }
}

test("admin owns the full page-version lifecycle", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByTestId("admin-login-form")).toHaveAttribute(
    "data-hydrated",
    "true"
  );
  await page.getByPlaceholder("邮箱").fill("admin@demo.com");
  await page.getByPlaceholder("密码").fill("Admin1234");
  await page.getByPlaceholder("例如 demo").fill("demo");
  await page.getByRole("button", { name: "手动登录" }).click();
  await expect(page).toHaveURL(/\/($|\?)/);
  await dismissOnboarding(page);

  const lifecycleRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().startsWith(`${API_BASE}/api/v1/page-`)) {
      lifecycleRequests.push(
        `${request.method()} ${new URL(request.url()).pathname}`
      );
    }
  });

  await page.goto("/pages");
  await expect(page.getByRole("heading", { name: "页面管理" })).toBeVisible();
  await dismissOnboarding(page);
  await page.getByRole("button", { name: /新建页面/ }).click();

  const dialog = page.getByRole("dialog", { name: "新建页面" });
  await expect(dialog).toBeVisible();
  const pageName = `U05A 页面 ${Date.now()}`;
  await dialog.getByLabel("页面名称").fill(pageName);
  await dialog.getByLabel("关联产品").click();
  await page
    .locator(".ant-select-dropdown:visible .ant-select-item-option")
    .first()
    .click();
  await dialog.getByRole("button", { name: "创建并编辑草稿" }).click();

  await expect(page).toHaveURL(/\/pages\/[0-9a-f-]+\/edit$/);
  await expect(page.getByText(pageName)).toBeVisible();
  await page.getByRole("button", { name: "发布草稿" }).click();
  const publishDialog = page.getByRole("dialog", { name: "发布此页面草稿？" });
  await expect(publishDialog).toBeVisible();
  await publishDialog.getByRole("button", { name: "发布草稿" }).click();
  await expect(page.getByText("草稿已发布")).toBeVisible();

  const templateId = page.url().match(/\/pages\/([0-9a-f-]+)\/edit$/)?.[1];
  expect(templateId).toBeTruthy();
  await page.goto(`/pages/${templateId}`);
  const publishedRow = page.locator("tr", { hasText: "已发布" });
  await expect(publishedRow).toBeVisible();
  await publishedRow.getByRole("button", { name: "下线线上版本" }).click();
  await confirmPopover(page);
  await expect(page.locator("tr", { hasText: "已归档" })).toBeVisible();

  const archivedRow = page.locator("tr", { hasText: "已归档" });
  await archivedRow.getByRole("button", { name: "创建草稿" }).click();
  await expect(page.locator("tr", { hasText: "草稿" })).toBeVisible();

  expect(lifecycleRequests).toEqual(
    expect.arrayContaining([
      "POST /api/v1/page-templates",
      expect.stringMatching(
        /^POST \/api\/v1\/page-templates\/[0-9a-f-]+\/versions$/
      ),
      expect.stringMatching(
        /^POST \/api\/v1\/page-versions\/[0-9a-f-]+\/publish$/
      ),
      expect.stringMatching(
        /^POST \/api\/v1\/page-versions\/[0-9a-f-]+\/archive$/
      ),
      expect.stringMatching(
        /^POST \/api\/v1\/page-templates\/[0-9a-f-]+\/versions\/[0-9a-f-]+\/rollback$/
      ),
    ])
  );
});
