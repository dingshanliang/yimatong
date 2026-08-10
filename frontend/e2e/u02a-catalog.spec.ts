import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import {
  expect,
  type APIRequestContext,
  type Page,
  test,
} from "@playwright/test";

const execFileAsync = promisify(execFile);
const API_BASE = process.env.YIMATONG_U02A_API_BASE || "http://127.0.0.1:18220";
const ADMIN_BASE =
  process.env.YIMATONG_U02A_ADMIN_ORIGIN || "http://127.0.0.1:13220";
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

interface LoginIdentity {
  accessToken: string;
  accountId: string;
}

interface CatalogFixture {
  tenant_id: string;
  foreign_brand_id: string;
  foreign_product_id: string;
  foreign_sku_id: string;
  public_id: string;
  cached_brand_id: string;
}

interface CatalogRow {
  id: string;
  name: string;
  [key: string]: unknown;
}

function logHttp(label: string, method: string, url: string, status: number) {
  console.log(`[u02a-evidence] ${label}: ${method} ${url} -> ${status}`);
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
  const databaseName = process.env.YIMATONG_U02A_DB;
  if (
    !databaseName ||
    !/^yimatong_acceptance_u02a_[a-z0-9_]+$/.test(databaseName)
  ) {
    throw new Error("Refusing U02A evidence access without the owned database");
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

async function readFixture(): Promise<CatalogFixture> {
  const raw = await psql(
    `WITH active_code AS (
       SELECT code_items.public_id, products.brand_id
         FROM code_items
         JOIN code_batches
           ON code_batches.id = code_items.code_batch_id
          AND code_batches.tenant_id = code_items.tenant_id
         JOIN products
           ON products.tenant_id = code_batches.tenant_id
          AND products.id = code_batches.product_id
        WHERE code_items.tenant_id = (SELECT id FROM tenants WHERE slug = 'demo')
          AND code_items.status = 'activated'
        ORDER BY code_items.created_at, code_items.id
        LIMIT 1
     )
     SELECT json_build_object(
       'tenant_id', (SELECT id FROM tenants WHERE slug = 'demo'),
       'foreign_brand_id', '00000000-0000-7000-8000-0000000002a0',
       'foreign_product_id', '00000000-0000-7000-8000-0000000002a2',
       'foreign_sku_id', '00000000-0000-7000-8000-0000000002a3',
       'public_id', active_code.public_id,
       'cached_brand_id', active_code.brand_id
     )::text
     FROM active_code;`
  );
  return JSON.parse(raw) as CatalogFixture;
}

async function setRuntimeBrandSelect(enabled: boolean) {
  await psql(
    `${enabled ? "GRANT" : "REVOKE"} SELECT ON TABLE brands ${
      enabled ? "TO" : "FROM"
    } yimatong_app;`
  );
}

async function expireBrandPlan() {
  await psql(
    "UPDATE tenants SET plan_expires_at = now() - interval '1 day' WHERE slug = 'demo' AND tenant_type = 'brand';"
  );
}

function authorization(token: string) {
  return { Authorization: `Bearer ${token}` };
}

async function expectJson<T>(
  response: Awaited<ReturnType<APIRequestContext["post"]>>,
  expectedStatus: number,
  label: string
): Promise<T> {
  const text = await response.text();
  expect(response.status(), text).toBe(expectedStatus);
  logHttp(label, "MUTATION", response.url(), response.status());
  return JSON.parse(text) as T;
}

async function expectDeniedRouteWithoutCatalogRequest(
  page: Page,
  identity: { email: string; password: string; tenantSlug: string },
  targetPath: string,
  expectedPath: string
) {
  const catalogRequests: string[] = [];
  const listener = (outgoing: { url(): string }) => {
    if (/\/api\/v1\/(brands|products|skus)(?:[/?]|$)/.test(outgoing.url())) {
      catalogRequests.push(outgoing.url());
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
  await page.goto(`${ADMIN_BASE}${targetPath}`);
  await expect(page).toHaveURL(`${ADMIN_BASE}${expectedPath}`);
  await page.waitForLoadState("networkidle");
  page.off("request", listener);
  expect(catalogRequests).toEqual([]);
}

test("U02A catalog remains tenant-isolated, role-scoped, and publicly trustworthy", async ({
  page,
  request,
}) => {
  const fixture = await readFixture();
  expect(fixture.tenant_id).toMatch(UUID_PATTERN);
  expect(fixture.cached_brand_id).toMatch(UUID_PATTERN);

  await expectDeniedRouteWithoutCatalogRequest(
    page,
    {
      email: "viewer.u02a@demo.com",
      password: "Viewer1234",
      tenantSlug: "demo",
    },
    "/products",
    "/"
  );
  await expect(page.getByRole("menuitem", { name: "产品管理" })).toHaveCount(0);

  await expectDeniedRouteWithoutCatalogRequest(
    page,
    {
      email: "agency_admin@demo.com",
      password: "demopass",
      tenantSlug: "demo-agency",
    },
    "/products",
    "/agency"
  );

  await expect(page).toHaveURL(`${ADMIN_BASE}/agency`);
  await expect(
    page.getByRole("heading", { name: "代运营工作台" })
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "进入管理" }).first()
  ).toBeVisible();
  const switchPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/agency/switch-context`
  );
  const actingProductsPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().startsWith(`${API_BASE}/api/v1/products`)
  );
  await page.getByRole("button", { name: "进入管理" }).first().click();
  expect((await switchPromise).status()).toBe(200);
  expect((await actingProductsPromise).status()).toBe(200);
  await expect(page).toHaveURL(`${ADMIN_BASE}/products`);
  await expect(page.getByRole("heading", { name: "产品管理" })).toBeVisible();

  const campaignRequests: string[] = [];
  const campaignListener = (outgoing: { url(): string }) => {
    if (outgoing.url().includes("/api/v1/campaigns")) {
      campaignRequests.push(outgoing.url());
    }
  };
  page.on("request", campaignListener);
  await page.goto(`${ADMIN_BASE}/campaigns`);
  await expect(page).toHaveURL(`${ADMIN_BASE}/products`);
  await page.waitForLoadState("networkidle");
  page.off("request", campaignListener);
  expect(campaignRequests).toEqual([]);

  await login(page, "ops@demo.com", "Ops123456", "demo", "operator login");
  const operatorProductsPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().startsWith(`${API_BASE}/api/v1/products`)
  );
  await page.goto(`${ADMIN_BASE}/products`);
  expect((await operatorProductsPromise).status()).toBe(200);
  await expect(page.getByRole("button", { name: /新建产品/ })).toBeEnabled();
  await expect(page.getByRole("button", { name: /删除/ })).toHaveCount(0);

  const admin = await login(
    page,
    "admin@demo.com",
    "Admin1234",
    "demo",
    "brand admin login"
  );
  await dismissOnboardingIfVisible(page);
  await setRuntimeBrandSelect(false);
  await page.goto(`${ADMIN_BASE}/brands`);
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByText("暂无品牌")).toHaveCount(0);
  await setRuntimeBrandSelect(true);
  const retriedBrandsPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().startsWith(`${API_BASE}/api/v1/brands`)
  );
  await page.getByRole("button", { name: /重\s*试/ }).click();
  expect((await retriedBrandsPromise).status()).toBe(200);

  const foreignProductName = "U02A 跨租户伪造产品";
  const foreignParentResponse = await request.post(
    `${API_BASE}/api/v1/products`,
    {
      headers: authorization(admin.accessToken),
      data: {
        brand_id: fixture.foreign_brand_id,
        name: foreignProductName,
      },
    }
  );
  expect(foreignParentResponse.status()).toBe(404);
  logHttp(
    "cross-tenant brand parent rejected",
    "POST",
    foreignParentResponse.url(),
    foreignParentResponse.status()
  );
  expect(
    Number(
      await psql(
        `SELECT count(*) FROM products WHERE tenant_id = '${fixture.tenant_id}'::uuid AND name = '${foreignProductName}';`
      )
    )
  ).toBe(0);

  const warmResolver = await request.get(`${API_BASE}/c/${fixture.public_id}`, {
    headers: { Accept: "application/json" },
  });
  expect(warmResolver.status()).toBe(200);
  const warmBrand = (await warmResolver.json()) as {
    brand?: { name?: string };
  };
  const updatedCachedBrandName = `U02A 缓存刷新品牌 ${Date.now()}`;
  const cachedBrandUpdate = await request.patch(
    `${API_BASE}/api/v1/brands/${fixture.cached_brand_id}`,
    {
      headers: authorization(admin.accessToken),
      data: { name: updatedCachedBrandName },
    }
  );
  expect(cachedBrandUpdate.status()).toBe(200);
  expect(warmBrand.brand?.name).not.toBe(updatedCachedBrandName);
  const refreshedResolver = await request.get(
    `${API_BASE}/c/${fixture.public_id}`,
    { headers: { Accept: "application/json" } }
  );
  expect(refreshedResolver.status()).toBe(200);
  expect(
    ((await refreshedResolver.json()) as { brand?: { name?: string } }).brand
      ?.name
  ).toBe(updatedCachedBrandName);

  const brand = await expectJson<CatalogRow>(
    await request.post(`${API_BASE}/api/v1/brands`, {
      headers: authorization(admin.accessToken),
      data: {
        name: `U02A 新品牌 ${Date.now()}`,
        logo_url: "https://cdn.example.com/u02a/brand.png",
        description: "待清空品牌描述",
      },
    }),
    201,
    "create brand"
  );
  const clearedBrand = await expectJson<CatalogRow>(
    await request.patch(`${API_BASE}/api/v1/brands/${brand.id}`, {
      headers: authorization(admin.accessToken),
      data: { logo_url: null, description: null },
    }),
    200,
    "clear nullable brand fields"
  );
  expect(clearedBrand.logo_url).toBeNull();
  expect(clearedBrand.description).toBeNull();

  const product = await expectJson<CatalogRow>(
    await request.post(`${API_BASE}/api/v1/products`, {
      headers: authorization(admin.accessToken),
      data: {
        brand_id: brand.id,
        name: `U02A 产品 ${Date.now()}`,
        category: "粮油",
        origin: "黑龙江",
        image_url: "/api/v1/files/public/u02a/product.png",
        story_title: "待清空标题",
        story_content: "待清空故事",
        description: "待清空介绍",
      },
    }),
    201,
    "create product"
  );
  const clearedProduct = await expectJson<CatalogRow>(
    await request.patch(`${API_BASE}/api/v1/products/${product.id}`, {
      headers: authorization(admin.accessToken),
      data: {
        category: null,
        origin: null,
        image_url: null,
        story_title: null,
        story_content: null,
        description: null,
      },
    }),
    200,
    "clear nullable product fields"
  );
  for (const field of [
    "category",
    "origin",
    "image_url",
    "story_title",
    "story_content",
    "description",
  ]) {
    expect(clearedProduct[field]).toBeNull();
  }

  const sku = await expectJson<CatalogRow>(
    await request.post(`${API_BASE}/api/v1/skus`, {
      headers: authorization(admin.accessToken),
      data: {
        product_id: product.id,
        code: `U02A-${Date.now()}`,
        name: "U02A 测试 SKU",
        specifications: { 净含量: "500g" },
        package_type: "袋装",
        barcode: "6901234567890",
        image_url: "https://cdn.example.com/u02a/sku.png",
      },
    }),
    201,
    "create SKU"
  );
  const clearedSku = await expectJson<CatalogRow>(
    await request.patch(`${API_BASE}/api/v1/skus/${sku.id}`, {
      headers: authorization(admin.accessToken),
      data: {
        specifications: null,
        package_type: null,
        barcode: null,
        image_url: null,
      },
    }),
    200,
    "clear nullable SKU fields"
  );
  for (const field of [
    "specifications",
    "package_type",
    "barcode",
    "image_url",
  ]) {
    expect(clearedSku[field]).toBeNull();
  }

  const reversedBatch = await request.post(
    `${API_BASE}/api/v1/production-batches`,
    {
      headers: authorization(admin.accessToken),
      data: {
        product_id: product.id,
        sku_id: sku.id,
        batch_code: `U02A-REVERSED-${Date.now()}`,
        production_date: "2026-08-10",
        expiry_date: "2026-08-09",
      },
    }
  );
  expect(reversedBatch.status()).toBe(422);

  const insecureEvidence = await request.post(
    `${API_BASE}/api/v1/products/${product.id}/assets`,
    {
      headers: authorization(admin.accessToken),
      data: {
        asset_type: "test_report",
        name: "不安全证据",
        issuer: "U02A 检测中心",
        valid_until: "2099-12-31",
        file_url: "http://cdn.example.com/report.pdf",
      },
    }
  );
  expect(insecureEvidence.status()).toBe(422);
  const expiredEvidence = await request.post(
    `${API_BASE}/api/v1/products/${product.id}/assets`,
    {
      headers: authorization(admin.accessToken),
      data: {
        asset_type: "certificate",
        name: "过期证据",
        issuer: "U02A 认证中心",
        valid_until: "2020-01-01",
        file_url: "https://cdn.example.com/expired.pdf",
      },
    }
  );
  expect(expiredEvidence.status()).toBe(422);
  const evidence = await expectJson<CatalogRow>(
    await request.post(`${API_BASE}/api/v1/products/${product.id}/assets`, {
      headers: authorization(admin.accessToken),
      data: {
        asset_type: "test_report",
        name: "有效检测报告",
        issuer: "U02A 检测中心",
        valid_until: "2099-12-31",
        file_url: "https://cdn.example.com/u02a/report.pdf",
      },
    }),
    201,
    "create public trust evidence"
  );

  const adminProductsPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().startsWith(`${API_BASE}/api/v1/products`)
  );
  await page.goto(`${ADMIN_BASE}/products`);
  expect((await adminProductsPromise).status()).toBe(200);
  await expect(page.getByText(product.name, { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: /删除/ }).first()
  ).toBeVisible();

  await expireBrandPlan();
  await page.reload();
  await expect(
    page.getByText("当前套餐已到期，后台已切换为只读")
  ).toBeVisible();
  await expect(page.getByText(product.name, { exact: true })).toBeVisible();
  const expiredPlanWrites: string[] = [];
  const expiredListener = (outgoing: { method(): string; url(): string }) => {
    if (
      ["POST", "PATCH", "DELETE"].includes(outgoing.method()) &&
      /\/api\/v1\/(brands|products|skus|production-batches|product-assets)/.test(
        outgoing.url()
      )
    ) {
      expiredPlanWrites.push(`${outgoing.method()} ${outgoing.url()}`);
    }
  };
  page.on("request", expiredListener);
  const createButton = page.getByRole("button", { name: /新建产品/ });
  await expect(createButton).toBeDisabled();
  await createButton.evaluate((button: HTMLButtonElement) => button.click());
  await page.waitForTimeout(300);
  page.off("request", expiredListener);
  expect(expiredPlanWrites).toEqual([]);
  const expiredApiWrite = await request.post(`${API_BASE}/api/v1/products`, {
    headers: authorization(admin.accessToken),
    data: { brand_id: brand.id, name: "U02A 到期套餐阻断产品" },
  });
  expect(expiredApiWrite.status()).toBe(403);
  expect(
    Number(
      await psql(
        `SELECT count(*) FROM products WHERE tenant_id = '${fixture.tenant_id}'::uuid AND name = 'U02A 到期套餐阻断产品';`
      )
    )
  ).toBe(0);

  for (const id of [brand.id, product.id, sku.id, evidence.id]) {
    expect(id).toMatch(UUID_PATTERN);
  }
  const auditRaw = await psql(
    `SELECT json_agg(json_build_object(
       'action', action,
       'operator_id', operator_id,
       'resource', resource,
       'result', details->>'result'
     ) ORDER BY timestamp, id)::text
       FROM platform_audit_log
      WHERE resource IN (
        'brand:${brand.id}',
        'product:${product.id}',
        'sku:${sku.id}',
        'product_asset:${evidence.id}'
      );`
  );
  const audits = JSON.parse(auditRaw) as Array<{
    action: string;
    operator_id: string;
    resource: string;
    result: string;
  }>;
  expect(audits.map((audit) => audit.action)).toEqual([
    "brand_created",
    "brand_updated",
    "product_created",
    "product_updated",
    "sku_created",
    "sku_updated",
    "product_asset_created",
  ]);
  expect(audits.every((audit) => audit.operator_id === admin.accountId)).toBe(
    true
  );
  expect(audits.every((audit) => audit.result === "success")).toBe(true);
});
