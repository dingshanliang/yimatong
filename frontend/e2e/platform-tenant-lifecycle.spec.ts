import { expect, type BrowserContext, type Page, test } from "@playwright/test";

const API_BASE =
  process.env.YIMATONG_LIFECYCLE_API_BASE || "http://127.0.0.1:18100";
const ADMIN_BASE =
  process.env.YIMATONG_LIFECYCLE_ADMIN_ORIGIN || "http://127.0.0.1:13100";
const PLATFORM_BASE =
  process.env.YIMATONG_LIFECYCLE_PLATFORM_ORIGIN || "http://127.0.0.1:13102";
const PLATFORM_PASSWORD = "platform_admin_2026";
const CUSTOMER_PASSWORD = "Lifecycle1234";

function tenantRow(page: Page, tenantName: string) {
  return page.getByRole("row").filter({ hasText: tenantName });
}

async function expectHydratedForm(page: Page, testId: string) {
  await expect(page.getByTestId(testId)).toHaveAttribute(
    "data-hydrated",
    "true"
  );
}

async function closeConfirmModal(page: Page) {
  const dialog = page.getByRole("dialog").last();
  await expect(dialog).toBeVisible();
  await dialog.locator(".ant-modal-confirm-btns button").last().click();
}

async function chooseTenantAction(
  page: Page,
  tenantName: string,
  action: string
) {
  const row = tenantRow(page, tenantName);
  await expect(row).toBeVisible();
  await row.getByRole("button").last().click();
  await page
    .getByRole("menu")
    .last()
    .getByText(action, { exact: true })
    .click();
}

async function confirmStatusChange(
  page: Page,
  tenantId: string,
  tenantName: string,
  action: string,
  expectedStatus: string
) {
  const responsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "PATCH" &&
      response.url() ===
        `${API_BASE}/api/v1/platform/tenants/${tenantId}/status`
  );
  await chooseTenantAction(page, tenantName, action);
  const dialog = page.getByRole("dialog").last();
  await expect(dialog).toContainText(`确认${action}租户`);
  await dialog.locator(".ant-modal-confirm-btns button").last().click();
  expect((await responsePromise).status()).toBe(200);
  await expect(tenantRow(page, tenantName)).toContainText(expectedStatus);
}

async function clearAdminState(context: BrowserContext, page: Page) {
  await context.clearCookies();
  await page.goto(`${ADMIN_BASE}/login`);
  await expectHydratedForm(page, "admin-login-form");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await expectHydratedForm(page, "admin-login-form");
}

async function loginTenantAdmin(
  page: Page,
  email: string,
  tenantSlug: string,
  shouldSucceed: boolean
) {
  const responsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/auth/login`
  );
  await page.getByPlaceholder("邮箱").fill(email);
  await page.getByPlaceholder("密码").fill(CUSTOMER_PASSWORD);
  await page.getByPlaceholder("例如 demo").fill(tenantSlug);
  await page.getByRole("button", { name: "手动登录" }).click();
  const response = await responsePromise;

  if (shouldSucceed) {
    expect(response.status()).toBe(200);
    await expect(page).toHaveURL(`${ADMIN_BASE}/`);
    await expect(page.getByRole("heading", { name: "经营看板" })).toBeVisible();
    return;
  }

  expect([401, 403]).toContain(response.status());
  await expect(page).toHaveURL(`${ADMIN_BASE}/login`);
}

test("Platform tenant lifecycle and invitation recover from lost responses", async ({
  page,
  browser,
}) => {
  const runId = `${Date.now().toString(36)}-${process.pid}`;
  const tenantName = `浏览器生命周期 ${runId}`;
  const tenantEmail = `lifecycle-${runId}@example.com`;
  const invitedTenantName = `邀请注册 ${runId}`;
  const invitedEmail = `invite-${runId}@example.com`;

  await page.goto(`${PLATFORM_BASE}/login`);
  await expectHydratedForm(page, "platform-login-form");
  const platformLoginPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/platform/auth/login`
  );
  await page.getByPlaceholder("管理员邮箱").fill("platform@yimatong.cn");
  await page.getByPlaceholder("密码").fill(PLATFORM_PASSWORD);
  await page.getByRole("button", { name: "登 录" }).click();
  expect((await platformLoginPromise).status()).toBe(200);
  await expect(page).toHaveURL(`${PLATFORM_BASE}/`);
  await expect(page.getByRole("heading", { name: "平台概览" })).toBeVisible();

  await page.goto(`${PLATFORM_BASE}/tenants`);
  await expect(page.getByRole("button", { name: "创建租户" })).toBeEnabled();

  let committedTenant: {
    id: string;
    slug: string;
    activation_url: string | null;
  } | null = null;
  const openingKeys: string[] = [];
  let loseFirstOpeningResponse = true;
  await page.route(`${API_BASE}/api/v1/platform/tenants`, async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    openingKeys.push(
      (await route.request().allHeaders())["idempotency-key"] || ""
    );
    if (loseFirstOpeningResponse) {
      loseFirstOpeningResponse = false;
      const upstream = await route.fetch();
      expect(upstream.status()).toBe(201);
      committedTenant = (await upstream.json()) as typeof committedTenant;
      await route.abort("failed");
      return;
    }
    await route.continue();
  });

  await page.getByRole("button", { name: "创建租户" }).click();
  const createDialog = page.getByRole("dialog").last();
  await createDialog.getByLabel("租户名称").fill(tenantName);
  await createDialog.getByLabel("行业").fill("食品饮料");
  await createDialog.getByLabel("管理员姓名").fill("生命周期管理员");
  await createDialog.getByLabel("管理员邮箱").fill(tenantEmail);
  await createDialog.locator(".ant-modal-footer button").last().click();
  await expect.poll(() => openingKeys.length).toBe(1);
  await expect.poll(() => committedTenant?.id).toBeTruthy();
  await expect(createDialog).toBeVisible();

  const replayResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/platform/tenants`
  );
  await createDialog.locator(".ant-modal-footer button").last().click();
  const replayResponse = await replayResponsePromise;
  expect(replayResponse.status()).toBe(201);
  expect(openingKeys).toHaveLength(2);
  expect(openingKeys[0]).toBeTruthy();
  expect(openingKeys[1]).toBe(openingKeys[0]);
  expect(committedTenant).not.toBeNull();
  const tenantId = committedTenant!.id;
  expect((await replayResponse.json()).id).toBe(tenantId);
  await expect(page.getByRole("dialog").last()).toContainText(
    "租户已创建，激活链接尚未生成"
  );
  await closeConfirmModal(page);
  await page.unroute(`${API_BASE}/api/v1/platform/tenants`);

  await page.getByPlaceholder("搜索名称或 Slug").fill(tenantName);
  await page.getByPlaceholder("搜索名称或 Slug").press("Enter");
  await expect(tenantRow(page, tenantName)).toHaveCount(1);

  const activationResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() ===
        `${API_BASE}/api/v1/platform/tenants/${tenantId}/initial-admin-activation`
  );
  await chooseTenantAction(page, tenantName, "恢复管理员激活");
  const activationResponse = await activationResponsePromise;
  expect(activationResponse.status()).toBe(200);
  const activationUrl = (await activationResponse.json())
    .activation_url as string;
  expect(activationUrl).toContain(`${ADMIN_BASE}/reset-password?`);
  await expect(page.getByRole("dialog").last()).toContainText(
    "管理员激活链接已生成"
  );
  await closeConfirmModal(page);

  const tenantAdminContext = await browser.newContext();
  const activationPage = await tenantAdminContext.newPage();
  await activationPage.goto(activationUrl);
  await expectHydratedForm(activationPage, "admin-reset-password-form");
  await activationPage
    .getByLabel("新密码", { exact: true })
    .fill(CUSTOMER_PASSWORD);
  await activationPage
    .getByLabel("确认新密码", { exact: true })
    .fill(CUSTOMER_PASSWORD);
  await activationPage.getByRole("button", { name: "确认重置" }).click();
  await expect(
    activationPage.getByText("密码已重置", { exact: true })
  ).toBeVisible();

  await clearAdminState(tenantAdminContext, activationPage);
  await loginTenantAdmin(
    activationPage,
    tenantEmail,
    committedTenant!.slug,
    true
  );

  await confirmStatusChange(page, tenantId, tenantName, "暂停", "暂停");
  await clearAdminState(tenantAdminContext, activationPage);
  await loginTenantAdmin(
    activationPage,
    tenantEmail,
    committedTenant!.slug,
    false
  );

  await confirmStatusChange(page, tenantId, tenantName, "恢复", "活跃");
  await clearAdminState(tenantAdminContext, activationPage);
  await loginTenantAdmin(
    activationPage,
    tenantEmail,
    committedTenant!.slug,
    true
  );

  await confirmStatusChange(page, tenantId, tenantName, "终止", "已终止");
  await clearAdminState(tenantAdminContext, activationPage);
  await loginTenantAdmin(
    activationPage,
    tenantEmail,
    committedTenant!.slug,
    false
  );
  await tenantAdminContext.close();

  await page.goto(`${PLATFORM_BASE}/invite-codes`);
  const inviteResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/invite-codes`
  );
  await page.getByRole("button", { name: "创建邀请码" }).click();
  const inviteDialog = page.getByRole("dialog").last();
  await inviteDialog
    .getByRole("button", { name: "创建并生成注册链接" })
    .click();
  const inviteResponse = await inviteResponsePromise;
  expect(inviteResponse.status()).toBe(201);
  const invite = (await inviteResponse.json()) as {
    registration_url: string;
  };
  expect(invite.registration_url).toContain(`${ADMIN_BASE}/register?`);
  await closeConfirmModal(page);

  const inviteAdminContext = await browser.newContext();
  const registrationPage = await inviteAdminContext.newPage();
  await registrationPage.goto(invite.registration_url);
  await expectHydratedForm(registrationPage, "admin-registration-form");
  await expect(registrationPage.getByLabel("邀请码")).not.toHaveValue("");
  await registrationPage.getByLabel("品牌或企业名称").fill(invitedTenantName);
  await registrationPage.getByLabel("所属行业（可选）").fill("食品饮料");
  await registrationPage.getByLabel("管理员姓名").fill("邀请管理员");
  await registrationPage.getByLabel("管理员邮箱").fill(invitedEmail);
  await registrationPage.getByLabel("设置登录密码").fill(CUSTOMER_PASSWORD);
  await registrationPage.getByLabel("确认登录密码").fill(CUSTOMER_PASSWORD);

  const registrationKeys: string[] = [];
  let committedRegistration: { tenant_slug: string } | null = null;
  let loseFirstRegistrationResponse = true;
  await registrationPage.route(
    `${API_BASE}/api/v1/invite-codes/register`,
    async (route) => {
      registrationKeys.push(
        (await route.request().allHeaders())["idempotency-key"] || ""
      );
      if (loseFirstRegistrationResponse) {
        loseFirstRegistrationResponse = false;
        const upstream = await route.fetch();
        expect(upstream.status()).toBe(201);
        committedRegistration =
          (await upstream.json()) as typeof committedRegistration;
        await route.abort("failed");
        return;
      }
      await route.continue();
    }
  );

  await registrationPage
    .getByRole("button", { name: "提交并开通品牌账号" })
    .click();
  await expect.poll(() => registrationKeys.length).toBe(1);
  await expect.poll(() => committedRegistration?.tenant_slug).toBeTruthy();
  await expect(
    registrationPage.getByRole("button", { name: "提交并开通品牌账号" })
  ).toBeVisible();

  const registrationReplayPromise = registrationPage.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/invite-codes/register`
  );
  await registrationPage
    .getByRole("button", { name: "提交并开通品牌账号" })
    .click();
  expect((await registrationReplayPromise).status()).toBe(201);
  expect(registrationKeys).toHaveLength(2);
  expect(registrationKeys[0]).toBeTruthy();
  expect(registrationKeys[1]).toBe(registrationKeys[0]);
  expect(committedRegistration).not.toBeNull();
  await expect(
    registrationPage.getByText("品牌账号已创建", { exact: true })
  ).toBeVisible();

  await registrationPage
    .getByRole("button", { name: "核对信息并登录" })
    .click();
  await expect(registrationPage).toHaveURL(`${ADMIN_BASE}/login`);
  await expect(registrationPage.getByPlaceholder("邮箱")).toHaveValue(
    invitedEmail
  );
  await expect(registrationPage.getByPlaceholder("例如 demo")).toHaveValue(
    committedRegistration!.tenant_slug
  );
  await registrationPage.getByPlaceholder("密码").fill(CUSTOMER_PASSWORD);
  await registrationPage.getByRole("button", { name: "手动登录" }).click();
  await expect(registrationPage).toHaveURL(`${ADMIN_BASE}/`);
  await expect(
    registrationPage.getByRole("heading", { name: "经营看板" })
  ).toBeVisible();
  await inviteAdminContext.close();
});
