import { expect, type Page, test } from "@playwright/test";
import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const API_BASE = process.env.YIMATONG_U01D_API_BASE || "http://127.0.0.1:18170";
const ADMIN_BASE =
  process.env.YIMATONG_U01D_ADMIN_ORIGIN || "http://127.0.0.1:13170";

function logHttp(label: string, method: string, url: string, status: number) {
  console.log(`[u01d-evidence] ${label}: ${method} ${url} -> ${status}`);
}

async function expectHydratedForm(page: Page) {
  await expect(page.getByTestId("admin-login-form")).toHaveAttribute(
    "data-hydrated",
    "true"
  );
}

async function login(
  page: Page,
  email: string,
  password: string,
  label: string
) {
  await page.goto(`${ADMIN_BASE}/login`);
  await expectHydratedForm(page);
  const loginPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/auth/login`
  );
  await page.getByPlaceholder("邮箱").fill(email);
  await page.getByPlaceholder("密码").fill(password);
  await page.getByPlaceholder("例如 demo").fill("demo");
  await page.getByRole("button", { name: "手动登录" }).click();
  const response = await loginPromise;
  expect(response.status()).toBe(200);
  logHttp(label, "POST", response.url(), response.status());
  await expect(page).toHaveURL(`${ADMIN_BASE}/`);
}

async function logout(page: Page, email: string, label: string) {
  const logoutPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/auth/logout`
  );
  await page.getByRole("banner").getByText(email, { exact: true }).click();
  await page.getByRole("menuitem", { name: /退出登录/ }).click();
  const response = await logoutPromise;
  expect(response.status()).toBe(200);
  logHttp(label, "POST", response.url(), response.status());
  await expect(page).toHaveURL(`${ADMIN_BASE}/login`);
}

async function openAccountAction(page: Page, accountName: string) {
  const row = page.getByRole("row").filter({ hasText: accountName });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: `操作菜单-${accountName}` }).click();
  return row;
}

interface AuditEvidence {
  action: string;
  resource_name: string | null;
  before_snapshot: Record<string, unknown> | null;
  after_snapshot: Record<string, unknown> | null;
}

async function readAuditEvidence(memberName: string): Promise<AuditEvidence[]> {
  const databaseName = process.env.YIMATONG_U01D_DB;
  if (
    !databaseName ||
    !/^yimatong_acceptance_u01d_[a-z0-9_]+$/.test(databaseName)
  ) {
    throw new Error(
      "refusing audit verification without the owned U01D database"
    );
  }
  const composeFile = path.resolve(
    __dirname,
    "..",
    "..",
    "docker-compose.infra.yml"
  );
  const { stdout } = await execFileAsync(
    "docker",
    [
      "compose",
      "-f",
      composeFile,
      "exec",
      "-T",
      "postgres",
      "psql",
      "-X",
      "-tA",
      "-v",
      "ON_ERROR_STOP=1",
      "-U",
      "yimatong",
      "-d",
      databaseName,
      "-c",
      `SELECT COALESCE(json_agg(row_to_json(evidence) ORDER BY evidence.timestamp, evidence.id), '[]'::json)::text
         FROM (
           SELECT id, timestamp, action,
                  details->>'resource_name' AS resource_name,
                  details->'before' AS before_snapshot,
                  details->'after' AS after_snapshot
             FROM platform_audit_log
            WHERE action IN ('account_created', 'account_updated', 'account_disabled', 'account_enabled')
         ) AS evidence;`,
    ],
    { maxBuffer: 1024 * 1024 }
  );
  return (JSON.parse(stdout.trim()) as AuditEvidence[]).filter(
    (item) => item.resource_name === memberName
  );
}

test("tenant admins manage canonical members without losing governance boundaries", async ({
  page,
}, testInfo) => {
  const suffix = `${Date.now().toString(36)}-${testInfo.workerIndex}`;
  const organizationName = `U01D 华东运营 ${suffix}`;
  const memberName = `U01D 运营成员 ${suffix}`;
  const memberEmail = `u01d.operator.${suffix}@example.com`;

  await login(page, "admin@demo.com", "Admin1234", "admin login");

  const organizationsPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url() === `${API_BASE}/api/v1/organizations/tree`
  );
  await page.goto(`${ADMIN_BASE}/accounts`);
  const organizationsResponse = await organizationsPromise;
  expect(organizationsResponse.status()).toBe(200);
  logHttp(
    "load organization tree",
    "GET",
    organizationsResponse.url(),
    organizationsResponse.status()
  );
  await expect(page.getByRole("heading", { name: "组织与账户" })).toBeVisible();
  const onboardingDialog = page.getByRole("dialog").filter({
    hasText: "完成初始化向导",
  });
  await expect(onboardingDialog).toBeVisible({ timeout: 10_000 });
  await onboardingDialog.getByText("稍后再说", { exact: true }).click();
  await expect(onboardingDialog).toBeHidden();
  console.log("[u01d-evidence] dismissed onboarding through real UI");

  await page.getByRole("button", { name: "新建组织" }).click();
  const organizationDialog = page.getByRole("dialog");
  await organizationDialog.getByLabel("组织名称").fill(organizationName);
  const createOrganizationPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/organizations`
  );
  await organizationDialog.getByRole("button", { name: /创\s*建/ }).click();
  const createOrganizationResponse = await createOrganizationPromise;
  expect(createOrganizationResponse.status()).toBe(201);
  logHttp(
    "create organization",
    "POST",
    createOrganizationResponse.url(),
    createOrganizationResponse.status()
  );
  await expect(page.getByText(organizationName, { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "账户管理" }).click();
  await expect(page.getByRole("button", { name: "新建账户" })).toBeEnabled();
  await page.getByRole("button", { name: "新建账户" }).click();
  const createAccountDialog = page.getByRole("dialog");
  await createAccountDialog.getByLabel("邮箱").fill(memberEmail);
  await createAccountDialog.getByLabel("姓名").fill(memberName);

  await createAccountDialog.getByLabel("所属组织").click();
  await page.getByText(organizationName, { exact: true }).last().click();

  const roleCombobox = createAccountDialog.getByLabel("角色");
  await roleCombobox.click();
  await roleCombobox.press("ArrowDown");
  await roleCombobox.press("Enter");
  await expect(createAccountDialog).toContainText("运营人员");
  await roleCombobox.press("Escape");
  await expect(page.getByRole("option", { name: "运营人员" })).toBeHidden();

  const createAccountPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/accounts`
  );
  await createAccountDialog.getByRole("button", { name: "创建账户" }).click();
  const createAccountResponse = await createAccountPromise;
  expect(createAccountResponse.status()).toBe(201);
  logHttp(
    "create operator member",
    "POST",
    createAccountResponse.url(),
    createAccountResponse.status()
  );
  await expect(
    page.getByTestId("account-initial-password-alert")
  ).toBeVisible();
  await createAccountDialog.locator(".ant-modal-close").click();
  const closeConfirmation = page.getByRole("dialog").filter({
    hasText: "临时密码仅在此处显示一次",
  });
  await closeConfirmation.getByRole("button", { name: "确认关闭" }).click();

  const memberRow = page.getByRole("row").filter({ hasText: memberEmail });
  await expect(memberRow).toContainText(memberName);
  await expect(memberRow).toContainText("运营人员");
  await expect(memberRow).toContainText(organizationName);
  await expect(memberRow).toContainText("已启用");

  await openAccountAction(page, "品牌管理员");
  await page.getByRole("menuitem", { name: /编辑账户/ }).click();
  const editAdminDialog = page.getByRole("dialog");
  const adminRoleField = editAdminDialog
    .locator(".ant-form-item")
    .filter({ hasText: "角色" });
  await adminRoleField.locator(".ant-select-selection-item-remove").click();
  const selfLockoutPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "PATCH" &&
      /\/api\/v1\/accounts\/[0-9a-f-]+$/.test(response.url())
  );
  await editAdminDialog.getByRole("button", { name: /保\s*存/ }).click();
  const selfLockoutResponse = await selfLockoutPromise;
  expect(selfLockoutResponse.status()).toBe(400);
  const selfLockoutPayload = (await selfLockoutResponse.json()) as {
    detail: string;
  };
  expect(selfLockoutPayload.detail).toBe("不能移除当前登录账户的管理员角色");
  logHttp(
    "self-admin demotion denied",
    "PATCH",
    selfLockoutResponse.url(),
    selfLockoutResponse.status()
  );
  await expect(
    page.getByText("不能移除当前登录账户的管理员角色", { exact: true })
  ).toBeVisible();
  await expect(editAdminDialog).toBeVisible();
  await editAdminDialog.locator(".ant-modal-close").click();

  await openAccountAction(page, memberName);
  await page.getByRole("menuitem", { name: /停用账户/ }).click();
  const disableDialog = page.getByRole("dialog");
  await disableDialog.getByLabel("停用原因").fill("U01D 浏览器离职验证");
  const disablePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "PATCH" &&
      response.url().endsWith("/status")
  );
  await disableDialog.getByRole("button", { name: "确认停用" }).click();
  const disableResponse = await disablePromise;
  expect(disableResponse.status()).toBe(200);
  logHttp(
    "disable operator member",
    "PATCH",
    disableResponse.url(),
    disableResponse.status()
  );
  await expect(memberRow).toContainText("已停用");

  await openAccountAction(page, memberName);
  const enablePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "PATCH" &&
      response.url().endsWith("/status")
  );
  await page.getByRole("menuitem", { name: /重新启用/ }).click();
  const enableResponse = await enablePromise;
  expect(enableResponse.status()).toBe(200);
  logHttp(
    "enable operator member",
    "PATCH",
    enableResponse.url(),
    enableResponse.status()
  );
  await expect(memberRow).toContainText("已启用");

  const adminRolesPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().startsWith(`${API_BASE}/api/v1/roles`)
  );
  await page.goto(`${ADMIN_BASE}/settings/roles`);
  const adminRolesResponse = await adminRolesPromise;
  expect(adminRolesResponse.status()).toBe(200);
  logHttp(
    "admin role directory",
    "GET",
    adminRolesResponse.url(),
    adminRolesResponse.status()
  );
  await expect(page.getByRole("heading", { name: "角色与权限" })).toBeVisible();
  await expect(page.getByText("租户管理员", { exact: true })).toBeVisible();
  await expect(page.getByText("运营人员", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /新建角色/ })).toHaveCount(0);

  await logout(page, "admin@demo.com", "admin logout before viewer boundary");

  let viewerRoleRequests = 0;
  const countViewerRoleRequests = (request: {
    method: () => string;
    url: () => string;
  }) => {
    if (
      request.method() === "GET" &&
      request.url().startsWith(`${API_BASE}/api/v1/roles`)
    ) {
      viewerRoleRequests += 1;
    }
  };
  page.on("request", countViewerRoleRequests);
  await login(page, "viewer.u01d@demo.com", "Viewer1234", "viewer login");
  await expect(page.locator('a[href="/settings/roles"]')).toHaveCount(0);
  await page.goto(`${ADMIN_BASE}/settings/roles`);
  await expect(
    page.getByText("当前角色不能查看角色目录", { exact: true })
  ).toBeVisible();
  await expect.poll(() => viewerRoleRequests).toBe(0);
  page.off("request", countViewerRoleRequests);
  console.log(
    `[u01d-evidence] viewer role-directory network count: ${viewerRoleRequests}`
  );
  await logout(
    page,
    "viewer.u01d@demo.com",
    "viewer logout after role boundary"
  );

  await login(page, "admin@demo.com", "Admin1234", "admin audit login");
  const auditPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().startsWith(`${API_BASE}/api/v1/audit-logs?`)
  );
  await page.goto(`${ADMIN_BASE}/settings/audit-logs`);
  const auditResponse = await auditPromise;
  expect(auditResponse.status()).toBe(200);
  logHttp(
    "load tenant audit",
    "GET",
    auditResponse.url(),
    auditResponse.status()
  );
  const enabledAuditRow = page
    .getByRole("row")
    .filter({ hasText: memberName })
    .filter({ hasText: "启用账户" });
  await expect(enabledAuditRow).toBeVisible();
  await enabledAuditRow.getByRole("button", { name: /详\s*情/ }).click();
  const auditDrawer = page.getByRole("dialog");
  await expect(auditDrawer).toContainText(memberName);
  await expect(auditDrawer.locator("pre")).toContainText(
    '"status": "disabled"'
  );
  await expect(auditDrawer.locator("pre")).toContainText('"status": "enabled"');
  await expect(auditDrawer.locator("pre")).toContainText('"name": "operator"');

  const auditEvidence = await readAuditEvidence(memberName);
  expect(auditEvidence.map((item) => item.action)).toEqual([
    "account_created",
    "account_disabled",
    "account_enabled",
  ]);
  for (const item of auditEvidence) {
    expect(item.resource_name).toBe(memberName);
  }
  for (const item of auditEvidence.filter((item) => item.before_snapshot)) {
    expect(item.before_snapshot).toMatchObject({
      name: memberName,
      roles: [{ name: "operator" }],
    });
    expect(item.after_snapshot).toMatchObject({
      name: memberName,
      roles: [{ name: "operator" }],
    });
  }
  console.log(
    `[u01d-evidence] account audit rows: ${auditEvidence
      .map((item) => item.action)
      .join(",")}`
  );

  const screenshotPath = testInfo.outputPath("account-governance-audit.png");
  await page.screenshot({ path: screenshotPath, fullPage: true });
  console.log(`[u01d-evidence] screenshot: ${screenshotPath}`);
  await auditDrawer.getByRole("button", { name: "关闭" }).click();
  await expect(auditDrawer).not.toBeVisible();

  await logout(page, "admin@demo.com", "final admin logout");
  const refreshAfterLogout = await page.request.post(
    `${API_BASE}/api/v1/auth/refresh`,
    { data: {} }
  );
  expect(refreshAfterLogout.status()).toBe(401);
  logHttp(
    "post-logout refresh denied",
    "POST",
    `${API_BASE}/api/v1/auth/refresh`,
    refreshAfterLogout.status()
  );
});
