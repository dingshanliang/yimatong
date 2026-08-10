import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import { expect, type Page, test } from "@playwright/test";

const execFileAsync = promisify(execFile);
const API_BASE = process.env.YIMATONG_U01H_API_BASE || "http://127.0.0.1:18190";
const ADMIN_BASE =
  process.env.YIMATONG_U01H_ADMIN_ORIGIN || "http://127.0.0.1:13190";

interface LoginIdentity {
  accessToken: string;
  accountId: string;
}

interface IssuedCredential {
  id: string;
  key: string;
  key_prefix: string;
  role: string;
  expires_at: string | null;
}

interface LifecycleEvidence {
  api_keys: Array<{
    id: string;
    key_prefix: string;
    key_digest: string;
    role: string;
    permissions: string[];
    revoked: boolean;
    rotated_from_id: string | null;
    created_by: string | null;
  }>;
  audits: Array<{
    action: string;
    operator_id: string;
    resource: string;
    details: Record<string, unknown>;
  }>;
}

function logHttp(label: string, method: string, url: string, status: number) {
  console.log(`[u01h-evidence] ${label}: ${method} ${url} -> ${status}`);
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
): Promise<LoginIdentity> {
  await page.goto(`${ADMIN_BASE}/login`);
  await page.evaluate(() => localStorage.clear());
  await page.context().clearCookies();
  await page.reload();
  await expectHydratedLogin(page);
  const responsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/auth/login`
  );
  await page.getByPlaceholder("邮箱").fill(email);
  await page.getByPlaceholder("密码").fill(password);
  await page.getByPlaceholder("例如 demo").fill(tenantSlug);
  await page.getByRole("button", { name: "手动登录" }).click();
  const response = await responsePromise;
  const responseText = await response.text();
  expect(response.status(), responseText).toBe(200);
  const payload = JSON.parse(responseText) as { access_token: string };
  const tokenPayload = JSON.parse(
    Buffer.from(payload.access_token.split(".")[1] ?? "", "base64url").toString(
      "utf8"
    )
  ) as { sub: string };
  logHttp(label, "POST", response.url(), response.status());
  await expect(page).toHaveURL(`${ADMIN_BASE}/`);
  return { accessToken: payload.access_token, accountId: tokenPayload.sub };
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
    await onboarding.getByText("稍后再说", { exact: true }).click();
    await expect(onboarding).toBeHidden();
  }
}

function ownedDatabaseName(): string {
  const databaseName = process.env.YIMATONG_U01H_DB;
  if (
    !databaseName ||
    !/^yimatong_acceptance_u01h_[a-z0-9_]+$/.test(databaseName)
  ) {
    throw new Error("Refusing U01H evidence access without the owned database");
  }
  return databaseName;
}

async function psql(sql: string): Promise<string> {
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
      ownedDatabaseName(),
      "-c",
      sql,
    ],
    { maxBuffer: 1024 * 1024 }
  );
  return stdout.trim();
}

async function suspendBrandTenant() {
  await psql(
    "UPDATE tenants SET status = 'suspended' WHERE slug = 'demo' AND tenant_type = 'brand';"
  );
}

async function activateBrandTenant() {
  await psql(
    "UPDATE tenants SET status = 'active' WHERE slug = 'demo' AND tenant_type = 'brand';"
  );
}

async function expireBrandPlan() {
  await psql(
    "UPDATE tenants SET plan_expires_at = now() - interval '1 day' WHERE slug = 'demo' AND tenant_type = 'brand';"
  );
}

async function readLifecycleEvidence(): Promise<LifecycleEvidence> {
  const raw = await psql(
    `WITH brand AS (SELECT id FROM tenants WHERE slug = 'demo')
     SELECT json_build_object(
       'api_keys', COALESCE((
         SELECT json_agg(json_build_object(
           'id', api_keys.id,
           'key_prefix', api_keys.key_prefix,
           'key_digest', api_keys.key_digest,
           'role', api_keys.role,
           'permissions', api_keys.permissions,
           'revoked', api_keys.revoked,
           'rotated_from_id', api_keys.rotated_from_id,
           'created_by', api_keys.created_by
         ) ORDER BY api_keys.created_at, api_keys.id)
           FROM api_keys
          WHERE api_keys.tenant_id = (SELECT id FROM brand)
       ), '[]'::json),
       'audits', COALESCE((
         SELECT json_agg(json_build_object(
           'action', action,
           'operator_id', operator_id,
           'resource', resource,
           'details', details
         ) ORDER BY timestamp, id)
           FROM platform_audit_log
          WHERE target_tenant_id = (SELECT id::text FROM brand)
            AND action IN ('api_key_issued', 'api_key_rotated', 'api_key_revoked')
       ), '[]'::json)
     )::text;`
  );
  return JSON.parse(raw) as LifecycleEvidence;
}

async function assertNoApiKeyRequestForIdentity(
  page: Page,
  identity: {
    email: string;
    password: string;
    tenantSlug: string;
    expectedPath: string;
  }
) {
  const apiKeyRequests: string[] = [];
  const listener = (request: { url(): string }) => {
    if (request.url().includes("/api/v1/webhooks/api-keys")) {
      apiKeyRequests.push(request.url());
    }
  };
  page.on("request", listener);
  await login(
    page,
    identity.email,
    identity.password,
    identity.tenantSlug,
    `${identity.email} login`
  );
  await page.goto(`${ADMIN_BASE}/integrations`);
  await expect(page).toHaveURL(new RegExp(`${identity.expectedPath}$`));
  await expect(page.getByRole("tab", { name: "API 密钥" })).toHaveCount(0);
  await page.waitForLoadState("networkidle");
  page.off("request", listener);
  expect(apiKeyRequests).toEqual([]);
}

test("U01H external API credentials remain tenant-bound and admin-controlled", async ({
  page,
  request,
}) => {
  await assertNoApiKeyRequestForIdentity(page, {
    email: "ops@demo.com",
    password: "Ops123456",
    tenantSlug: "demo",
    expectedPath: "/integrations",
  });
  await assertNoApiKeyRequestForIdentity(page, {
    email: "viewer.u01h@demo.com",
    password: "Viewer1234",
    tenantSlug: "demo",
    expectedPath: "/integrations",
  });
  await assertNoApiKeyRequestForIdentity(page, {
    email: "agency_admin@demo.com",
    password: "demopass",
    tenantSlug: "demo-agency",
    expectedPath: "/agency",
  });

  const admin = await login(
    page,
    "admin@demo.com",
    "Admin1234",
    "demo",
    "brand administrator login"
  );
  await dismissOnboardingIfVisible(page);
  await page.goto(`${ADMIN_BASE}/integrations`);
  const listPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url() ===
        `${API_BASE}/api/v1/webhooks/api-keys?page=1&page_size=20`
  );
  await page.getByRole("tab", { name: "API 密钥" }).click();
  const listResponse = await listPromise;
  expect(listResponse.status()).toBe(200);
  const listBody = (await listResponse.json()) as {
    items: unknown[];
    total: number;
    page: number;
    page_size: number;
  };
  expect(listBody).toMatchObject({ total: 0, page: 1, page_size: 20 });
  expect(listBody.items).toEqual([]);

  await page.getByRole("button", { name: "创建第一把密钥" }).click();
  await page.getByLabel("用途名称").fill("U01H ERP 同步");
  const issuePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/webhooks/api-keys`
  );
  await page.getByRole("button", { name: "创建密钥" }).click();
  const issueResponse = await issuePromise;
  const issueText = await issueResponse.text();
  expect(issueResponse.status(), issueText).toBe(201);
  expect(issueResponse.request().headers()["idempotency-key"]).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
  );
  const issued = JSON.parse(issueText) as IssuedCredential;
  expect(issued.key).toMatch(/^ymt_[0-9a-f]{48}$/);
  expect(issued.key_prefix).toBe(issued.key.slice(0, 12));
  expect(issued.role).toBe("data_reader");
  expect(issued.expires_at).not.toBeNull();
  logHttp("issue credential", "POST", issueResponse.url(), 201);
  await expect(page.getByLabel("一次性 API 密钥")).toHaveValue(issued.key);
  await page.getByRole("button", { name: "我已安全保存，关闭" }).click();
  await expect(page.getByLabel("一次性 API 密钥")).toBeHidden();
  await expect(page.getByText(issued.key_prefix)).toBeVisible();
  await expect(page.getByText(issued.key, { exact: true })).toHaveCount(0);

  const issuedRow = page
    .getByRole("row")
    .filter({ hasText: issued.key_prefix });
  await issuedRow.getByRole("button", { name: /轮\s*换/ }).click();
  const rotatePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() ===
        `${API_BASE}/api/v1/webhooks/api-keys/${issued.id}/rotate`
  );
  await page.getByRole("button", { name: "确认轮换" }).click();
  const rotateResponse = await rotatePromise;
  const rotateText = await rotateResponse.text();
  expect(rotateResponse.status(), rotateText).toBe(201);
  expect(rotateResponse.request().headers()["idempotency-key"]).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
  );
  const rotated = JSON.parse(rotateText) as IssuedCredential;
  expect(rotated.key).not.toBe(issued.key);
  expect(rotated.key_prefix).not.toBe(issued.key_prefix);
  logHttp("rotate credential", "POST", rotateResponse.url(), 201);
  await expect(page.getByLabel("一次性 API 密钥")).toHaveValue(rotated.key);
  await page.getByRole("button", { name: "我已安全保存，关闭" }).click();

  const oldCredentialResponse = await request.get(`${API_BASE}/open/v1/scans`, {
    headers: { "X-Api-Key": issued.key },
  });
  expect(oldCredentialResponse.status()).toBe(401);
  const rotatedCredentialResponse = await request.get(
    `${API_BASE}/open/v1/scans`,
    { headers: { "X-Api-Key": rotated.key } }
  );
  expect(rotatedCredentialResponse.status()).toBe(200);

  await suspendBrandTenant();
  const suspendedKeyResponse = await request.get(`${API_BASE}/open/v1/scans`, {
    headers: { "X-Api-Key": rotated.key },
  });
  expect(suspendedKeyResponse.status()).toBe(401);
  const suspendedAdminResponse = await request.get(
    `${API_BASE}/api/v1/webhooks/api-keys`,
    { headers: { Authorization: `Bearer ${admin.accessToken}` } }
  );
  expect(suspendedAdminResponse.status()).toBe(401);
  await activateBrandTenant();

  await expireBrandPlan();
  await page.reload();
  await expect(
    page.getByText("当前套餐已到期，后台已切换为只读")
  ).toBeVisible();
  await page.getByRole("tab", { name: "API 密钥" }).click();
  const rotatedRow = page
    .getByRole("row")
    .filter({ hasText: rotated.key_prefix });

  let blockedIssueRequests = 0;
  let blockedRotateRequests = 0;
  const blockedRequestListener = (outgoing: {
    method(): string;
    url(): string;
  }) => {
    if (
      outgoing.method() === "POST" &&
      outgoing.url() === `${API_BASE}/api/v1/webhooks/api-keys`
    ) {
      blockedIssueRequests += 1;
    }
    if (
      outgoing.method() === "POST" &&
      outgoing.url() ===
        `${API_BASE}/api/v1/webhooks/api-keys/${rotated.id}/rotate`
    ) {
      blockedRotateRequests += 1;
    }
  };
  page.on("request", blockedRequestListener);
  await page.getByRole("button", { name: "新建 API 密钥" }).click();
  await page.getByLabel("用途名称").fill("套餐到期阻断验证");
  await page.getByRole("button", { name: "创建密钥" }).click();
  await expect(page.getByText("API 密钥未创建")).toBeVisible();
  await page.getByRole("button", { name: /取\s*消/ }).click();
  await rotatedRow.getByRole("button", { name: /轮\s*换/ }).click();
  await page.getByRole("button", { name: "确认轮换" }).click();
  await expect(page.getByText("API 密钥操作未完成")).toBeVisible();
  page.off("request", blockedRequestListener);
  expect(blockedIssueRequests).toBe(0);
  expect(blockedRotateRequests).toBe(0);

  await rotatedRow.getByRole("button", { name: /吊\s*销/ }).click();
  const revokePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "DELETE" &&
      response.url() === `${API_BASE}/api/v1/webhooks/api-keys/${rotated.id}`
  );
  await page.getByRole("button", { name: "确认吊销" }).click();
  const revokeResponse = await revokePromise;
  expect(revokeResponse.status()).toBe(200);
  logHttp("expired-plan security revoke", "DELETE", revokeResponse.url(), 200);
  await expect(page.getByText(rotated.key_prefix)).toHaveCount(0);

  const evidence = await readLifecycleEvidence();
  expect(evidence.api_keys).toHaveLength(2);
  expect(evidence.api_keys.every((key) => key.revoked)).toBe(true);
  expect(evidence.api_keys[1]?.rotated_from_id).toBe(issued.id);
  expect(evidence.api_keys[1]?.created_by).toBe(admin.accountId);
  expect(
    evidence.api_keys.every((key) => /^[0-9a-f]{64}$/.test(key.key_digest))
  ).toBe(true);
  expect(evidence.audits.map((audit) => audit.action)).toEqual([
    "api_key_issued",
    "api_key_rotated",
    "api_key_revoked",
  ]);
  expect(
    evidence.audits.every((audit) => audit.operator_id === admin.accountId)
  ).toBe(true);
  const evidenceText = JSON.stringify(evidence);
  expect(evidenceText).not.toContain(issued.key);
  expect(evidenceText).not.toContain(rotated.key);
  expect(evidenceText).not.toContain("requested_key_digest");
});
