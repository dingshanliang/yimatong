import { expect, type BrowserContext, type Page, test } from "@playwright/test";
import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const API_BASE = process.env.YIMATONG_U01E_API_BASE || "http://127.0.0.1:18180";
const ADMIN_BASE =
  process.env.YIMATONG_U01E_ADMIN_ORIGIN || "http://127.0.0.1:13180";
const PLATFORM_ORIGIN = "http://127.0.0.1:13182";

interface AccessIdentity {
  accountId: string;
  tenantId: string;
  tenantType: string;
  authVersion: number;
}

interface PlatformSession {
  cookieHeader: string;
  csrfToken: string;
}

interface AuditRow {
  action: string;
  operator_id: string;
  target_tenant_id: string;
  resource: string;
  agency_tenant_id: string | null;
  acting_tenant_id: string | null;
  scope: string[] | null;
}

interface DatabaseEvidence {
  brand_id: string;
  agency_id: string;
  brand_tenant_type: string;
  brand_admin_id: string;
  agency_admin_id: string;
  brand_auth_version: number;
  brand_open_sessions: number;
  audits: AuditRow[];
}

function logHttp(label: string, method: string, url: string, status: number) {
  console.log(`[u01e-evidence] ${label}: ${method} ${url} -> ${status}`);
}

function parseAccessIdentity(accessToken: string): AccessIdentity {
  const encodedPayload = accessToken.split(".")[1];
  if (!encodedPayload) throw new Error("access token payload is missing");
  const payload = JSON.parse(
    Buffer.from(encodedPayload, "base64url").toString("utf8")
  ) as Record<string, unknown>;
  if (
    typeof payload.sub !== "string" ||
    typeof payload.tenant_id !== "string" ||
    typeof payload.tenant_type !== "string" ||
    typeof payload.auth_version !== "number"
  ) {
    throw new Error("access token identity is incomplete");
  }
  return {
    accountId: payload.sub,
    tenantId: payload.tenant_id,
    tenantType: payload.tenant_type,
    authVersion: payload.auth_version,
  };
}

async function expectHydratedLogin(page: Page) {
  await expect(page.getByTestId("admin-login-form")).toHaveAttribute(
    "data-hydrated",
    "true"
  );
}

async function login(
  page: Page,
  email: string,
  password: string,
  tenantSlug: string,
  label: string
): Promise<AccessIdentity> {
  await page.goto(`${ADMIN_BASE}/login`);
  await expectHydratedLogin(page);
  const loginPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/auth/login`
  );
  await page.getByPlaceholder("邮箱").fill(email);
  await page.getByPlaceholder("密码").fill(password);
  await page.getByPlaceholder("例如 demo").fill(tenantSlug);
  await page.getByRole("button", { name: "手动登录" }).click();
  const response = await loginPromise;
  expect(response.status()).toBe(200);
  const payload = (await response.json()) as { access_token: string };
  logHttp(label, "POST", response.url(), response.status());
  await expect(page).toHaveURL(`${ADMIN_BASE}/`);
  return parseAccessIdentity(payload.access_token);
}

async function dismissOnboardingIfVisible(page: Page) {
  const onboarding = page
    .getByRole("dialog")
    .filter({ hasText: "欢迎使用一码通" });
  if (
    await onboarding
      .waitFor({ state: "visible", timeout: 5_000 })
      .then(() => true)
      .catch(() => false)
  ) {
    const dismiss = onboarding.getByText("稍后再说", { exact: true });
    await dismiss.click();
    await expect(onboarding).toBeHidden();
    console.log("[u01e-evidence] dismissed brand onboarding through real UI");
  }
}

function responseSetCookies(response: Response): string[] {
  const headers = response.headers as Headers & {
    getSetCookie?: () => string[];
  };
  if (headers.getSetCookie) return headers.getSetCookie();
  const combined = headers.get("set-cookie");
  return combined ? combined.split(/,(?=[^;,]+=)/) : [];
}

async function platformLogin(): Promise<PlatformSession> {
  const response = await fetch(`${API_BASE}/api/v1/platform/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      email: "platform@yimatong.cn",
      password: "platform_admin_2026",
    }),
  });
  const text = await response.text();
  expect(response.status, text).toBe(200);
  const cookies = new Map<string, string>();
  for (const setCookie of responseSetCookies(response)) {
    const [pair] = setCookie.split(";", 1);
    const separator = pair.indexOf("=");
    if (separator > 0) {
      cookies.set(pair.slice(0, separator), pair.slice(separator + 1));
    }
  }
  const accessToken = cookies.get("platform_access_token");
  const csrfToken = cookies.get("platform_csrf_token");
  if (!accessToken || !csrfToken) {
    throw new Error("platform login did not return its session cookies");
  }
  logHttp(
    "platform login for tenant-type transition",
    "POST",
    response.url,
    200
  );
  return {
    cookieHeader: `platform_access_token=${accessToken}; platform_csrf_token=${csrfToken}`,
    csrfToken,
  };
}

async function changeTenantType(tenantId: string, tenantType: string) {
  const session = await platformLogin();
  const endpoint = `${API_BASE}/api/v1/tenants/${tenantId}`;
  const response = await fetch(endpoint, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      Cookie: session.cookieHeader,
      Origin: PLATFORM_ORIGIN,
      "X-Platform-CSRF": session.csrfToken,
    },
    body: JSON.stringify({ tenant_type: tenantType }),
  });
  const text = await response.text();
  expect(response.status, text).toBe(200);
  logHttp("platform tenant-type transition", "PATCH", endpoint, 200);
}

async function readDatabaseEvidence(): Promise<DatabaseEvidence> {
  const databaseName = process.env.YIMATONG_U01E_DB;
  if (
    !databaseName ||
    !/^yimatong_acceptance_u01e_[a-z0-9_]+$/.test(databaseName)
  ) {
    throw new Error("refusing evidence read without the owned U01E database");
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
      `WITH identities AS (
         SELECT
           (SELECT id FROM tenants WHERE slug = 'demo') AS brand_id,
           (SELECT id FROM tenants WHERE slug = 'demo-agency') AS agency_id,
           (SELECT id FROM accounts WHERE email = 'admin@demo.com'
             AND tenant_id = (SELECT id FROM tenants WHERE slug = 'demo')) AS brand_admin_id,
           (SELECT id FROM accounts WHERE email = 'agency_admin@demo.com'
             AND tenant_id = (SELECT id FROM tenants WHERE slug = 'demo-agency')) AS agency_admin_id
       )
       SELECT json_build_object(
         'brand_id', identities.brand_id,
         'agency_id', identities.agency_id,
         'brand_tenant_type', (SELECT tenant_type FROM tenants WHERE id = identities.brand_id),
         'brand_admin_id', identities.brand_admin_id,
         'agency_admin_id', identities.agency_admin_id,
         'brand_auth_version', (SELECT auth_version FROM accounts WHERE id = identities.brand_admin_id),
         'brand_open_sessions', (SELECT count(*) FROM auth_sessions WHERE tenant_id = identities.brand_id AND revoked_at IS NULL),
         'audits', COALESCE((
           SELECT json_agg(json_build_object(
             'action', action,
             'operator_id', operator_id,
             'target_tenant_id', target_tenant_id,
             'resource', resource,
             'agency_tenant_id', details->>'agency_tenant_id',
             'acting_tenant_id', details->>'acting_tenant_id',
             'scope', details->'scope'
           ) ORDER BY timestamp, id)
             FROM platform_audit_log
            WHERE action IN (
              'agency_authorization_granted',
              'agency_context_entered',
              'agency_authorization_revoked',
              'agency_context_exited',
              'tenant_type_changed'
            )
         ), '[]'::json)
       )::text
       FROM identities;`,
    ],
    { maxBuffer: 1024 * 1024 }
  );
  return JSON.parse(stdout.trim()) as DatabaseEvidence;
}

async function logoutAgency(page: Page, context: BrowserContext) {
  const logoutPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/auth/logout`
  );
  await page
    .getByRole("banner")
    .getByText("agency_admin@demo.com", { exact: true })
    .click();
  await page.getByRole("menuitem", { name: /退出登录/ }).click();
  const logoutResponse = await logoutPromise;
  expect(logoutResponse.status()).toBe(200);
  logHttp("agency logout", "POST", logoutResponse.url(), 200);
  await expect(page).toHaveURL(`${ADMIN_BASE}/login`);

  const postLogoutRefresh = await context.request.post(
    `${API_BASE}/api/v1/auth/refresh`,
    { data: {} }
  );
  expect(postLogoutRefresh.status()).toBe(401);
  logHttp(
    "post-logout refresh denied",
    "POST",
    `${API_BASE}/api/v1/auth/refresh`,
    401
  );
}

test("agency access remains live-scoped, revocable, auditable, and recoverable", async ({
  browser,
}, testInfo) => {
  const brandContext = await browser.newContext();
  const agencyContext = await browser.newContext();
  const brandPage = await brandContext.newPage();
  const agencyPage = await agencyContext.newPage();

  try {
    const brandIdentity = await login(
      brandPage,
      "admin@demo.com",
      "Admin1234",
      "demo",
      "brand admin login"
    );
    expect(brandIdentity.tenantType).toBe("brand");

    const authorizationListPromise = brandPage.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        response.url().startsWith(`${API_BASE}/api/v1/ops/authorizations`)
    );
    await brandPage.goto(`${ADMIN_BASE}/settings/agency-authorizations`);
    const authorizationListResponse = await authorizationListPromise;
    expect(authorizationListResponse.status()).toBe(200);
    logHttp(
      "brand loads agency authorization directory",
      "GET",
      authorizationListResponse.url(),
      200
    );
    await expect(
      brandPage.getByRole("heading", { name: "代运营授权" })
    ).toBeVisible();
    await dismissOnboardingIfVisible(brandPage);
    await brandPage.getByRole("button", { name: "新增授权" }).click();
    const authorizationDialog = brandPage.getByRole("dialog");
    await authorizationDialog
      .getByLabel("服务商工作区标识")
      .fill("demo-agency");
    await authorizationDialog.getByLabel("营销活动").uncheck();
    await authorizationDialog.getByLabel("码批次").uncheck();
    await authorizationDialog.getByLabel("经营分析").uncheck();
    const grantPromise = brandPage.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url() === `${API_BASE}/api/v1/ops/authorizations`
    );
    await authorizationDialog
      .getByRole("button", { name: "授权后立即生效" })
      .click();
    const grantResponse = await grantPromise;
    expect(grantResponse.status()).toBe(201);
    const grantPayload = (await grantResponse.json()) as {
      id: string;
      agency_tenant_id: string;
      client_tenant_id: string;
      scope: string[];
    };
    expect(grantPayload.scope).toEqual(["products", "pages"]);
    expect(grantPayload.client_tenant_id).toBe(brandIdentity.tenantId);
    logHttp(
      "brand grants scoped agency access",
      "POST",
      grantResponse.url(),
      201
    );
    const authorizationRow = brandPage
      .getByRole("row")
      .filter({ hasText: "示例代运营服务商" });
    await expect(authorizationRow).toContainText("商品资料");
    await expect(authorizationRow).toContainText("扫码页面");
    await expect(authorizationRow).not.toContainText("营销活动");

    const agencyIdentity = await login(
      agencyPage,
      "agency_admin@demo.com",
      "demopass",
      "demo-agency",
      "agency admin login"
    );
    expect(agencyIdentity.tenantType).toBe("agency");
    expect(agencyIdentity.tenantId).toBe(grantPayload.agency_tenant_id);
    const workbenchPromise = agencyPage.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        response.url().startsWith(`${API_BASE}/api/v1/ops/workbench?`)
    );
    await agencyPage.goto(`${ADMIN_BASE}/agency`);
    const workbenchResponse = await workbenchPromise;
    expect(workbenchResponse.status()).toBe(200);
    logHttp("agency loads live workbench", "GET", workbenchResponse.url(), 200);
    await expect(
      agencyPage.getByRole("heading", { name: "代运营工作台" })
    ).toBeVisible();
    const clientRow = agencyPage
      .getByRole("row")
      .filter({ hasText: "青岭良仓演示租户" })
      .filter({ has: agencyPage.getByRole("button", { name: "进入管理" }) });
    await expect(clientRow).toContainText("仅限已授权模块");

    const switchPromise = agencyPage.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url() === `${API_BASE}/api/v1/agency/switch-context`
    );
    const productsPromise = agencyPage.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        response.url().startsWith(`${API_BASE}/api/v1/products`)
    );
    await clientRow.getByRole("button", { name: "进入管理" }).click();
    const switchResponse = await switchPromise;
    const productsResponse = await productsPromise;
    expect(switchResponse.status()).toBe(200);
    expect(productsResponse.status()).toBe(200);
    logHttp("agency enters client", "POST", switchResponse.url(), 200);
    logHttp("agency reads scoped products", "GET", productsResponse.url(), 200);
    await expect(agencyPage).toHaveURL(`${ADMIN_BASE}/products`);
    await expect(
      agencyPage.getByRole("heading", { name: "产品管理" })
    ).toBeVisible();
    await expect(agencyPage.getByText(/^客户: /)).toBeVisible();

    let campaignApiRequests = 0;
    const countCampaignApiRequests = (request: {
      method: () => string;
      url: () => string;
    }) => {
      if (
        request.method() === "GET" &&
        request.url().startsWith(`${API_BASE}/api/v1/campaigns`)
      ) {
        campaignApiRequests += 1;
      }
    };
    agencyPage.on("request", countCampaignApiRequests);
    await expect(
      agencyPage.getByRole("menuitem", { name: "活动管理" })
    ).toHaveCount(0);
    await agencyPage.goto(`${ADMIN_BASE}/campaigns`);
    await expect(agencyPage).toHaveURL(`${ADMIN_BASE}/products`);
    await expect.poll(() => campaignApiRequests).toBe(0);
    agencyPage.off("request", countCampaignApiRequests);
    console.log(
      `[u01e-evidence] out-of-scope campaign entry redirected before API; network count=${campaignApiRequests}`
    );

    // Corrupt, but do not replace or inject, the browser's current short-lived
    // access token so the next real UI load exercises 401 -> refresh -> live
    // agency authorization revalidation with the HttpOnly refresh cookie.
    await agencyPage.evaluate(() => {
      const token = localStorage.getItem("access_token");
      if (!token) throw new Error("missing acting access token");
      const parts = token.split(".");
      const signature = parts[2];
      if (!signature) throw new Error("acting access token has no signature");
      parts[2] = `${signature.startsWith("a") ? "b" : "a"}${signature.slice(1)}`;
      localStorage.setItem("access_token", parts.join("."));
    });
    const refreshPromise = agencyPage.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url() === `${API_BASE}/api/v1/auth/refresh`
    );
    const restoreContextPromise = agencyPage.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url() === `${API_BASE}/api/v1/agency/switch-context`
    );
    const restoredProductsPromise = agencyPage.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        response.url().startsWith(`${API_BASE}/api/v1/products`) &&
        response.status() === 200
    );
    await agencyPage.reload();
    const [refreshResponse, restoreContextResponse, restoredProductsResponse] =
      await Promise.all([
        refreshPromise,
        restoreContextPromise,
        restoredProductsPromise,
      ]);
    expect(refreshResponse.status()).toBe(200);
    expect(restoreContextResponse.status()).toBe(200);
    logHttp("acting session refresh", "POST", refreshResponse.url(), 200);
    logHttp(
      "acting context restored against live grant",
      "POST",
      restoreContextResponse.url(),
      200
    );
    logHttp(
      "scoped products reload after refresh",
      "GET",
      restoredProductsResponse.url(),
      200
    );
    await expect(agencyPage.getByText(/^客户: /)).toBeVisible();

    const revokeButton = authorizationRow.getByRole("button", {
      name: "撤销",
    });
    await revokeButton.click();
    const revokeStarted = brandPage.waitForRequest(
      (request) =>
        request.method() === "DELETE" &&
        request.url() ===
          `${API_BASE}/api/v1/ops/authorizations/${grantPayload.id}`
    );
    const revokeResponsePromise = brandPage.waitForResponse(
      (response) =>
        response.request().method() === "DELETE" &&
        response.url() ===
          `${API_BASE}/api/v1/ops/authorizations/${grantPayload.id}`
    );
    await brandPage
      .getByRole("dialog")
      .getByRole("button", { name: "确认撤销" })
      .click();
    await revokeStarted;
    const revokeResponse = await revokeResponsePromise;
    expect(revokeResponse.status()).toBe(204);
    logHttp("brand revokes grant", "DELETE", revokeResponse.url(), 204);

    // A 204 response is the commit barrier for the revocation transaction.
    // Issue the old acting request only after that barrier so this assertion
    // proves post-commit invalidation rather than racing two valid lock orders.
    const oldActingProductsPromise = agencyPage.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        response.url().startsWith(`${API_BASE}/api/v1/products`)
    );
    await agencyPage.reload();
    const oldActingProductsResponse = await oldActingProductsPromise;
    expect(oldActingProductsResponse.status()).toBe(403);
    logHttp(
      "old acting request denied after committed revoke",
      "GET",
      oldActingProductsResponse.url(),
      403
    );

    const exitPromise = agencyPage.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url() === `${API_BASE}/api/v1/agency/exit-context`
    );
    await agencyPage.getByRole("button", { name: "退出客户" }).click();
    const exitResponse = await exitPromise;
    expect(exitResponse.status()).toBe(200);
    logHttp("agency exits revoked client", "POST", exitResponse.url(), 200);
    await expect(agencyPage).toHaveURL(`${ADMIN_BASE}/agency`);
    await expect(
      agencyPage.getByRole("heading", { name: "代运营工作台" })
    ).toBeVisible();
    await expect(agencyPage.getByText(/^客户: /)).toHaveCount(0);

    await changeTenantType(brandIdentity.tenantId, "agency");
    const staleBrandStatus = await brandPage.evaluate(async (apiBase) => {
      const accessToken = localStorage.getItem("access_token");
      if (!accessToken) throw new Error("missing pre-transition brand token");
      const response = await fetch(`${apiBase}/api/v1/ops/authorizations`, {
        headers: {
          Authorization: `Bearer ${accessToken}`,
          Accept: "application/json",
        },
      });
      return response.status;
    }, API_BASE);
    expect(staleBrandStatus).toBe(401);
    logHttp(
      "stale brand token after tenant-type transition",
      "GET",
      `${API_BASE}/api/v1/ops/authorizations`,
      staleBrandStatus
    );

    const evidence = await readDatabaseEvidence();
    expect(evidence.brand_id).toBe(brandIdentity.tenantId);
    expect(evidence.agency_id).toBe(agencyIdentity.tenantId);
    expect(evidence.brand_admin_id).toBe(brandIdentity.accountId);
    expect(evidence.agency_admin_id).toBe(agencyIdentity.accountId);
    expect(evidence.brand_tenant_type).toBe("agency");
    expect(evidence.brand_auth_version).toBeGreaterThan(
      brandIdentity.authVersion
    );
    expect(evidence.brand_open_sessions).toBe(0);

    const grantedAudits = evidence.audits.filter(
      (audit) => audit.action === "agency_authorization_granted"
    );
    const enteredAudits = evidence.audits.filter(
      (audit) => audit.action === "agency_context_entered"
    );
    const revokedAudits = evidence.audits.filter(
      (audit) => audit.action === "agency_authorization_revoked"
    );
    const exitedAudits = evidence.audits.filter(
      (audit) => audit.action === "agency_context_exited"
    );
    const typeChangedAudits = evidence.audits.filter(
      (audit) => audit.action === "tenant_type_changed"
    );
    expect(grantedAudits).toEqual([
      expect.objectContaining({
        operator_id: brandIdentity.accountId,
        target_tenant_id: brandIdentity.tenantId,
        resource: `agency_authorization:${grantPayload.id}`,
        agency_tenant_id: agencyIdentity.tenantId,
        scope: ["products", "pages"],
      }),
    ]);
    expect(enteredAudits.length).toBeGreaterThanOrEqual(2);
    for (const audit of enteredAudits) {
      expect(audit).toEqual(
        expect.objectContaining({
          operator_id: agencyIdentity.accountId,
          target_tenant_id: brandIdentity.tenantId,
          resource: `agency_authorization:${grantPayload.id}`,
          agency_tenant_id: agencyIdentity.tenantId,
          acting_tenant_id: brandIdentity.tenantId,
          scope: ["products", "pages"],
        })
      );
    }
    expect(revokedAudits).toEqual([
      expect.objectContaining({
        operator_id: brandIdentity.accountId,
        target_tenant_id: brandIdentity.tenantId,
        resource: `agency_authorization:${grantPayload.id}`,
        agency_tenant_id: agencyIdentity.tenantId,
        scope: ["products", "pages"],
      }),
    ]);
    expect(exitedAudits).toEqual([
      expect.objectContaining({
        operator_id: agencyIdentity.accountId,
        target_tenant_id: agencyIdentity.tenantId,
        acting_tenant_id: brandIdentity.tenantId,
      }),
    ]);
    expect(typeChangedAudits).toEqual([
      expect.objectContaining({
        operator_id: "platform-admin",
        target_tenant_id: brandIdentity.tenantId,
        resource: `tenant:${brandIdentity.tenantId}`,
      }),
    ]);
    console.log(
      `[u01e-evidence] trustworthy audits grant=${grantedAudits.length} enter=${enteredAudits.length} revoke=${revokedAudits.length} exit=${exitedAudits.length} tenant_type=${typeChangedAudits.length}`
    );
    console.log(
      `[u01e-evidence] tenant-type transition invalidated sessions: auth_version ${brandIdentity.authVersion} -> ${evidence.brand_auth_version}; open_sessions=${evidence.brand_open_sessions}`
    );

    const screenshotPath = testInfo.outputPath(
      "agency-back-in-own-workspace.png"
    );
    await agencyPage.screenshot({ path: screenshotPath, fullPage: true });
    console.log(`[u01e-evidence] screenshot: ${screenshotPath}`);

    await logoutAgency(agencyPage, agencyContext);
  } finally {
    await brandContext.close();
    await agencyContext.close();
  }
});
