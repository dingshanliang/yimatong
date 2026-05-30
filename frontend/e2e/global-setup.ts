/**
 * Playwright global setup — seed tenant + admin + base entities via backend API.
 *
 * This runs once before all test files. It creates:
 *   - a tenant with an admin account
 *   - brand, product, SKU
 *   - code batch (sync generation) + activation
 *   - page template + published version
 *   - campaign + benefit
 *
 * Auth state and entity IDs are written to e2e/.auth/ so tests can reuse them.
 */

import { mkdir, writeFile } from "fs/promises";
import path from "path";

const API_BASE = process.env.API_BASE_URL || "http://localhost:8000";
const TEST_PASSWORD = "E2ETest1234";

function generateUniqueSlug(): string {
  return `e2e-test-${Date.now()}`;
}

function generateUniqueEmail(): string {
  return `e2e-admin-${Date.now()}@example.com`;
}

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

async function apiPost(
  endpoint: string,
  body: unknown,
  token?: string
): Promise<Record<string, unknown>> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  const text = await res.text();
  const json = text ? JSON.parse(text) : {};
  if (!res.ok) {
    throw new Error(
      `POST ${endpoint} failed: ${res.status} ${res.statusText} — ${text.slice(0, 200)}`
    );
  }
  return json;
}

async function apiGet(
  endpoint: string,
  token: string
): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}${endpoint}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
    },
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : {};
  if (!res.ok) {
    throw new Error(
      `GET ${endpoint} failed: ${res.status} ${res.statusText} — ${text.slice(0, 200)}`
    );
  }
  return json;
}

async function apiPatch(
  endpoint: string,
  body: unknown,
  token: string
): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
    },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : {};
  if (!res.ok) {
    throw new Error(
      `PATCH ${endpoint} failed: ${res.status} ${res.statusText} — ${text.slice(0, 200)}`
    );
  }
  return json;
}

async function waitForBackend(maxRetries = 30, delayMs = 1000): Promise<void> {
  for (let i = 0; i < maxRetries; i++) {
    try {
      const res = await fetch(`${API_BASE}/health`);
      if (res.ok) {
        console.log(`[global-setup] Backend ready after ${i + 1} attempts`);
        return;
      }
    } catch {
      // Backend not up yet
    }
    await new Promise((r) => setTimeout(r, delayMs));
  }
  throw new Error(`Backend not ready after ${maxRetries} attempts`);
}

export default async function globalSetup() {
  console.log("[global-setup] Waiting for backend...");
  await waitForBackend();
  console.log("[global-setup] Seeding E2E test data...");

  const TEST_EMAIL = generateUniqueEmail();
  const slug = generateUniqueSlug();

  // 1. Create tenant (open endpoint — no auth required)
  const tenantRes = await apiPost("/api/v1/tenants", {
    name: "E2E Test Tenant",
    slug,
    plan: "free",
    admin_email: TEST_EMAIL,
    admin_name: "E2E Admin",
    admin_password: TEST_PASSWORD,
  });
  const tenantId = tenantRes.id as string;
  console.log(`[global-setup] Tenant created: ${tenantId} (slug=${slug})`);

  // 2. Login
  const loginRes = await apiPost("/api/v1/auth/login", {
    email: TEST_EMAIL,
    password: TEST_PASSWORD,
  });
  const token = loginRes.access_token as string;
  console.log(`[global-setup] Logged in as ${TEST_EMAIL}, token acquired`);

  // 3. Create brand
  const brandRes = await apiPost("/api/v1/brands", { name: "E2E Brand", industry: "food" }, token);
  const brandId = brandRes.id as string;
  console.log(`[global-setup] Brand created: ${brandId}`);

  // 4. Create product
  const productRes = await apiPost(
    "/api/v1/products",
    { name: "E2E Product", brand_id: brandId, category: "测试品类" },
    token
  );
  const productId = productRes.id as string;
  console.log(`[global-setup] Product created: ${productId}`);

  // 5. Create SKU
  const skuRes = await apiPost(
    "/api/v1/skus",
    { product_id: productId, code: "E2E-SKU-001", name: "E2E SKU" },
    token
  );
  const skuId = skuRes.id as string;
  console.log(`[global-setup] SKU created: ${skuId}`);

  // 6. Create code batch (synchronous generation)
  const batchRes = await apiPost(
    "/api/v1/code-batches",
    {
      product_id: productId,
      sku_id: skuId,
      batch_code: `E2E-BATCH-${Date.now()}`,
      quantity: 10,
      code_type: "single",
    },
    token
  );
  const codeBatchId = batchRes.id as string;
  console.log(`[global-setup] Code batch created: ${codeBatchId}`);

  // 7. Activate code batch
  await apiPost(`/api/v1/code-batches/${codeBatchId}/activate`, {}, token);
  console.log(`[global-setup] Code batch activated`);

  // 8. Get a public_id
  const itemsRes = await apiGet(`/api/v1/code-items?code_batch_id=${codeBatchId}&page_size=1`, token);
  const items = (itemsRes.items || []) as Array<{ public_id: string }>;
  const publicId = items[0]?.public_id;
  if (!publicId) throw new Error("No code items generated");
  console.log(`[global-setup] Public ID: ${publicId}`);

  // 9. Create page template
  const tplRes = await apiPost(
    "/api/v1/page-templates",
    { name: "E2E Page", template_type: "product_info", description: "E2E test page" },
    token
  );
  const pageTemplateId = tplRes.id as string;
  console.log(`[global-setup] Page template created: ${pageTemplateId}`);

  // 10. Create page version with DSL
  const dsl = {
    modules: [
      { id: "hero", type: "product_hero", enabled: true, config: { show_verify_badge: true } },
      { id: "trace", type: "light_traceability", enabled: true, config: { fields: ["origin", "production_date"] } },
      { id: "benefit", type: "benefit_card", enabled: true, config: { benefit_id: "", benefit_type: "coupon", title: "测试权益", description: "E2E测试权益" } },
    ],
    routing: { default_page: true, campaign_periods: [] },
  };
  const verRes = await apiPost(
    `/api/v1/page-templates/${pageTemplateId}/versions`,
    { config_json: dsl },
    token
  );
  const pageVersionId = verRes.id as string;
  console.log(`[global-setup] Page version created: ${pageVersionId}`);

  // 11. Publish page version
  await apiPost(`/api/v1/page-versions/${pageVersionId}/publish`, {}, token);
  console.log(`[global-setup] Page version published`);

  // 12. Create campaign
  const campaignRes = await apiPost(
    "/api/v1/campaigns",
    {
      name: "E2E Campaign",
      campaign_type: "coupon",
      start_at: "2026-01-01T00:00:00Z",
      end_at: "2026-12-31T23:59:59Z",
      rules_json: {
        participation_conditions: "不限",
        claim_limits: "每人限领1次",
        validity_period: "领取后7天有效",
        disclaimer: "最终解释权归品牌方所有",
        minor_notice: "未成年人请在监护人陪同下参与",
        customer_service_contact: "400-123-4567",
      },
    },
    token
  );
  const campaignId = campaignRes.id as string;
  console.log(`[global-setup] Campaign created: ${campaignId}`);

  // 13. Create benefit
  const benefitRes = await apiPost(
    `/api/v1/campaigns/${campaignId}/benefits`,
    {
      name: "E2E Benefit",
      benefit_type: "platform_coupon",
      stock_total: 100,
      per_person_limit: 1,
      config_json: { amount: 10, min_order: 50 },
    },
    token
  );
  const benefitId = benefitRes.id as string;
  console.log(`[global-setup] Benefit created: ${benefitId}`);

  // 14. Update page DSL with actual benefit_id
  const updatedDsl = {
    ...dsl,
    modules: dsl.modules.map((m) =>
      m.type === "benefit_card" ? { ...m, config: { ...m.config, benefit_id: benefitId } } : m
    ),
  };
  await apiPatch(`/api/v1/page-versions/${pageVersionId}`, { config_json: updatedDsl }, token);
  console.log(`[global-setup] Page DSL updated with benefit_id`);

  // 15. Persist auth state + test context
  const authDir = path.join(__dirname, ".auth");
  await mkdir(authDir, { recursive: true });

  // Write a minimal storage state for Playwright
  const storageState = {
    cookies: [
      {
        name: "access_token",
        value: token,
        domain: "localhost",
        path: "/",
        expires: Math.floor(Date.now() / 1000) + 3600,
        httpOnly: false,
        secure: false,
        sameSite: "Lax",
      },
    ],
    origins: [
      {
        origin: "http://localhost:3000",
        localStorage: [
          { name: "access_token", value: token },
          {
            name: "auth_store",
            value: JSON.stringify({
              account_id: "e2e-admin",
              tenant_id: tenantId,
              role: "admin",
              email: TEST_EMAIL,
              name: "E2E Admin",
            }),
          },
        ],
      },
    ],
  };

  const ctx: TestContext = {
    token,
    tenantId,
    brandId,
    productId,
    skuId,
    codeBatchId,
    publicId,
    pageTemplateId,
    pageVersionId,
    campaignId,
    benefitId,
  };

  await writeFile(path.join(authDir, "state.json"), JSON.stringify(storageState, null, 2));
  await writeFile(path.join(authDir, "context.json"), JSON.stringify(ctx, null, 2));

  console.log("[global-setup] Done.");
}
