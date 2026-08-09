import { expect, type Page, test } from "@playwright/test";
import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const API_BASE = process.env.YIMATONG_U01F_API_BASE || "http://127.0.0.1:18160";
const ADMIN_BASE =
  process.env.YIMATONG_U01F_ADMIN_ORIGIN || "http://127.0.0.1:13160";

function logHttp(label: string, method: string, url: string, status: number) {
  console.log(`[u01f-evidence] ${label}: ${method} ${url} -> ${status}`);
}

function tenantIdFromAccessToken(accessToken: string): string {
  const encodedPayload = accessToken.split(".")[1];
  if (!encodedPayload) throw new Error("access token payload is missing");
  const payload = JSON.parse(
    Buffer.from(encodedPayload, "base64url").toString("utf8")
  ) as { tenant_id?: unknown };
  if (typeof payload.tenant_id !== "string") {
    throw new Error("access token tenant_id is missing");
  }
  return payload.tenant_id;
}

interface AuditEvidence {
  action: string;
  target_tenant_id: string;
  agency_tenant_id: string | null;
  acting_tenant_id: string | null;
}

async function readAuditEvidence(): Promise<AuditEvidence[]> {
  const databaseName = process.env.YIMATONG_U01F_DB;
  if (
    !databaseName ||
    !/^yimatong_acceptance_u01f_[a-z0-9_]+$/.test(databaseName)
  ) {
    throw new Error(
      "refusing audit verification without the owned U01F database"
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
      "-U",
      "yimatong",
      "-d",
      databaseName,
      "-c",
      `SELECT COALESCE(json_agg(row_to_json(evidence)), '[]'::json)::text
         FROM (
           SELECT action,
                  target_tenant_id,
                  details->>'agency_tenant_id' AS agency_tenant_id,
                  details->>'acting_tenant_id' AS acting_tenant_id
             FROM platform_audit_log
            WHERE action IN ('agency_context_entered', 'agency_context_exited')
            ORDER BY timestamp, id
         ) AS evidence;`,
    ],
    { maxBuffer: 1024 * 1024 }
  );
  return JSON.parse(stdout.trim()) as AuditEvidence[];
}

async function expectHydratedForm(page: Page, testId: string) {
  await expect(page.getByTestId(testId)).toHaveAttribute(
    "data-hydrated",
    "true"
  );
}

test("expired agency client stays recoverable while business writes remain blocked", async ({
  page,
}, testInfo) => {
  await page.goto(`${ADMIN_BASE}/login`);
  await expectHydratedForm(page, "admin-login-form");

  const loginPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/auth/login`
  );
  await page.getByPlaceholder("邮箱").fill("agency_admin@demo.com");
  await page.getByPlaceholder("密码").fill("demopass");
  await page.getByPlaceholder("例如 demo").fill("demo-agency");
  await page.getByRole("button", { name: "手动登录" }).click();
  const loginResponse = await loginPromise;
  expect(loginResponse.status()).toBe(200);
  const loginPayload = (await loginResponse.json()) as { access_token: string };
  const agencyTenantId = tenantIdFromAccessToken(loginPayload.access_token);
  logHttp("real agency login", "POST", loginResponse.url(), 200);

  await expect(page).toHaveURL(`${ADMIN_BASE}/`);
  const workbenchPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().startsWith(`${API_BASE}/api/v1/ops/workbench?`)
  );
  await page.goto(`${ADMIN_BASE}/agency`);
  const workbenchResponse = await workbenchPromise;
  expect(workbenchResponse.status()).toBe(200);
  logHttp("load agency clients", "GET", workbenchResponse.url(), 200);
  await expect(
    page.getByRole("heading", { name: "代运营工作台" })
  ).toBeVisible();

  const clientRow = page
    .getByRole("row")
    .filter({ hasText: "青岭良仓演示租户" })
    .filter({ has: page.getByRole("button", { name: "进入管理" }) });
  await expect(clientRow).toBeVisible();
  await expect(clientRow).toContainText("已过期");

  const switchPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/agency/switch-context`
  );
  await clientRow.getByRole("button", { name: "进入管理" }).click();
  const switchResponse = await switchPromise;
  expect(switchResponse.status()).toBe(200);
  const switchPayload = (await switchResponse.json()) as {
    acting_tenant_id: string;
  };
  logHttp("enter expired client", "POST", switchResponse.url(), 200);

  await expect(
    page.getByText("当前套餐已到期，后台已切换为只读")
  ).toBeVisible();
  await expect(page.getByText(/^客户: /)).toBeVisible();

  // Keep the real login and refresh cookie, but invalidate the signed access
  // token to exercise the browser client's actual 401 -> refresh -> agency
  // context revalidation path.
  await page.evaluate(() => {
    const token = localStorage.getItem("access_token");
    if (!token) throw new Error("missing access token after real login");
    const parts = token.split(".");
    const signature = parts[2];
    if (!signature) throw new Error("missing JWT signature");
    parts[2] = `${signature.startsWith("a") ? "b" : "a"}${signature.slice(1)}`;
    localStorage.setItem("access_token", parts.join("."));
  });

  const refreshPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/auth/refresh`
  );
  const revalidationPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/agency/switch-context`
  );
  await page.getByRole("button", { name: "刷新套餐状态" }).click();
  const refreshResponse = await refreshPromise;
  const revalidationResponse = await revalidationPromise;
  expect(refreshResponse.status()).toBe(200);
  expect(revalidationResponse.status()).toBe(200);
  logHttp("refresh while read-only", "POST", refreshResponse.url(), 200);
  logHttp(
    "restore expired client context",
    "POST",
    revalidationResponse.url(),
    200
  );
  await expect(
    page.getByText("当前套餐已到期，后台已切换为只读")
  ).toBeVisible();
  await expect(page.getByText(/^客户: /)).toBeVisible();

  await page.goto(`${ADMIN_BASE}/campaigns`);
  await expect(page.getByRole("heading", { name: "活动管理" })).toBeVisible();
  await expect(
    page.getByText("当前套餐已到期，后台已切换为只读")
  ).toBeVisible();

  let campaignPostCount = 0;
  const countCampaignPosts = (request: {
    method: () => string;
    url: () => string;
  }) => {
    if (
      request.method() === "POST" &&
      request.url() === `${API_BASE}/api/v1/campaigns`
    ) {
      campaignPostCount += 1;
    }
  };
  page.on("request", countCampaignPosts);
  const createCampaignButtons = page.getByRole("button", {
    name: "新建活动",
  });
  await expect(createCampaignButtons).toHaveCount(2);
  for (const createCampaignButton of await createCampaignButtons.all()) {
    await expect(createCampaignButton).toBeDisabled();
  }
  await expect.poll(() => campaignPostCount).toBe(0);
  page.off("request", countCampaignPosts);
  console.log(
    `[u01f-evidence] read-only create controls disabled; business POST network count: ${campaignPostCount}`
  );

  const screenshotPath = testInfo.outputPath("expired-client-read-only.png");
  await page.screenshot({ path: screenshotPath, fullPage: true });
  console.log(`[u01f-evidence] screenshot: ${screenshotPath}`);

  const exitPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/agency/exit-context`
  );
  await page.getByRole("button", { name: "退出客户" }).click();
  const exitResponse = await exitPromise;
  expect(exitResponse.status()).toBe(200);
  logHttp("exit expired client", "POST", exitResponse.url(), 200);
  await expect(page).toHaveURL(`${ADMIN_BASE}/agency`);
  await expect(
    page.getByRole("heading", { name: "代运营工作台" })
  ).toBeVisible();
  await expect(page.getByText(/^客户: /)).toHaveCount(0);

  const auditEvidence = await readAuditEvidence();
  const enteredAudits = auditEvidence.filter(
    (audit) => audit.action === "agency_context_entered"
  );
  const exitedAudits = auditEvidence.filter(
    (audit) => audit.action === "agency_context_exited"
  );
  expect(enteredAudits).toHaveLength(2);
  for (const enteredAudit of enteredAudits) {
    expect(enteredAudit.target_tenant_id).toBe(switchPayload.acting_tenant_id);
    expect(enteredAudit.agency_tenant_id).toBe(agencyTenantId);
    expect(enteredAudit.acting_tenant_id).toBe(switchPayload.acting_tenant_id);
  }
  expect(exitedAudits).toEqual([
    {
      action: "agency_context_exited",
      target_tenant_id: agencyTenantId,
      agency_tenant_id: agencyTenantId,
      acting_tenant_id: switchPayload.acting_tenant_id,
    },
  ]);
  console.log(
    `[u01f-evidence] audit rows: entered=${enteredAudits.length}, exited=${exitedAudits.length}`
  );

  const logoutPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/auth/logout`
  );
  await page.getByText("agency_admin@demo.com", { exact: true }).click();
  await page.getByText("退出登录", { exact: true }).click();
  const logoutResponse = await logoutPromise;
  expect(logoutResponse.status()).toBe(200);
  logHttp("logout from agency workspace", "POST", logoutResponse.url(), 200);
  await expect(page).toHaveURL(`${ADMIN_BASE}/login`);

  const refreshAfterLogout = await page.request.post(
    `${API_BASE}/api/v1/auth/refresh`,
    { data: {} }
  );
  expect(refreshAfterLogout.status()).toBe(401);
  logHttp(
    "post-logout refresh denied",
    "POST",
    `${API_BASE}/api/v1/auth/refresh`,
    401
  );
});
