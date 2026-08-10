import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";

import {
  expect,
  type APIRequestContext,
  type APIResponse,
  type Page,
  type Request,
  type Response,
  test,
} from "@playwright/test";

const execFileAsync = promisify(execFile);
const API_BASE = process.env.YIMATONG_U02B_API_BASE || "http://127.0.0.1:18230";
const ADMIN_BASE =
  process.env.YIMATONG_U02B_ADMIN_ORIGIN || "http://127.0.0.1:13230";
const H5_BASE = process.env.YIMATONG_U02B_H5_ORIGIN || "http://127.0.0.1:13231";
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

interface LoginIdentity {
  accessToken: string;
  accountId: string;
}

interface CreatedRow {
  id: string;
  [key: string]: unknown;
}

function logHttp(label: string, response: APIResponse) {
  console.log(
    `[u02b-evidence] ${label}: ${response.url()} -> ${response.status()}`
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
  await expect(page.getByTestId("admin-login-form")).toHaveAttribute(
    "data-hydrated",
    "true"
  );
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
  console.log(`[u02b-evidence] ${label}: POST auth/login -> 200`);
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
  const databaseName = process.env.YIMATONG_U02B_DB;
  if (
    !databaseName ||
    !/^yimatong_acceptance_u02b_[a-z0-9_]+$/.test(databaseName)
  ) {
    throw new Error("Refusing U02B evidence access without the owned database");
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

function authorization(token: string) {
  return { Authorization: `Bearer ${token}` };
}

async function expectJson<T>(
  response: APIResponse,
  expectedStatus: number,
  label: string
): Promise<T> {
  const text = await response.text();
  expect(response.status(), text).toBe(expectedStatus);
  logHttp(label, response);
  return JSON.parse(text) as T;
}

async function expectDeniedWithoutBatchRequests(
  page: Page,
  identity: { email: string; password: string; tenantSlug: string },
  expectedPath: string
) {
  const requests: string[] = [];
  const listener = (outgoing: { url(): string }) => {
    if (
      /\/api\/v1\/(production-batches|products|skus)(?:[/?]|$)/.test(
        outgoing.url()
      )
    ) {
      requests.push(outgoing.url());
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
  await page.goto(`${ADMIN_BASE}/batches`);
  await expect(page).toHaveURL(`${ADMIN_BASE}${expectedPath}`);
  await page.waitForLoadState("networkidle");
  page.off("request", listener);
  expect(requests).toEqual([]);
}

async function createCodeBatch(
  request: APIRequestContext,
  token: string,
  ids: { productId: string; skuId: string; productionBatchId: string },
  batchCode: string
) {
  return request.post(`${API_BASE}/api/v1/code-batches`, {
    headers: authorization(token),
    data: {
      product_id: ids.productId,
      sku_id: ids.skuId,
      production_batch_id: ids.productionBatchId,
      batch_code: batchCode,
      quantity: 1,
      code_type: "single",
      generation_mode: "item_level",
    },
  });
}

test("U02B keeps one authoritative batch across Admin, codes, audits, and consumer H5", async ({
  page,
  request,
}) => {
  await expectDeniedWithoutBatchRequests(
    page,
    {
      email: "viewer.u02b@demo.com",
      password: "Viewer1234",
      tenantSlug: "demo",
    },
    "/"
  );
  await expect(page.getByRole("menuitem", { name: "生产批次" })).toHaveCount(0);

  await expectDeniedWithoutBatchRequests(
    page,
    {
      email: "agency_admin@demo.com",
      password: "demopass",
      tenantSlug: "demo-agency",
    },
    "/agency"
  );
  const actingBatchResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().startsWith(`${API_BASE}/api/v1/production-batches`)
  );
  const switchResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() === `${API_BASE}/api/v1/agency/switch-context`
  );
  await page.getByRole("button", { name: "进入管理" }).first().click();
  expect((await switchResponse).status()).toBe(200);
  await page.goto(`${ADMIN_BASE}/batches`);
  expect((await actingBatchResponse).status()).toBe(200);
  await expect(
    page.getByRole("heading", { name: "生产批次管理" })
  ).toBeVisible();

  await login(page, "ops@demo.com", "Ops123456", "demo", "operator login");
  await page.goto(`${ADMIN_BASE}/batches`);
  await expect(
    page.getByRole("heading", { name: "生产批次管理" })
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /新建批次/ })).toBeEnabled();
  await expect(page.getByRole("button", { name: "召回批次" })).toHaveCount(0);

  const admin = await login(
    page,
    "admin@demo.com",
    "Admin1234",
    "demo",
    "brand admin login"
  );
  await dismissOnboardingIfVisible(page);
  const suffix = Date.now();

  const foreignBatchCode = `U02B-FOREIGN-${suffix}`;
  const foreignParent = await request.post(
    `${API_BASE}/api/v1/production-batches`,
    {
      headers: authorization(admin.accessToken),
      data: {
        product_id: "00000000-0000-7000-8000-0000000002b2",
        sku_id: "00000000-0000-7000-8000-0000000002b3",
        batch_code: foreignBatchCode,
        production_date: "2026-08-10",
        expiry_date: "2027-08-10",
      },
    }
  );
  expect([404, 422]).toContain(foreignParent.status());
  expect(
    Number(
      await psql(
        `SELECT count(*) FROM production_batches WHERE batch_code = '${foreignBatchCode}';`
      )
    )
  ).toBe(0);

  const brand = await expectJson<CreatedRow>(
    await request.post(`${API_BASE}/api/v1/brands`, {
      headers: authorization(admin.accessToken),
      data: { name: `U02B 权威批次品牌 ${suffix}` },
    }),
    201,
    "create brand"
  );
  const productOrigin = "产品档案默认产地";
  const product = await expectJson<CreatedRow>(
    await request.post(`${API_BASE}/api/v1/products`, {
      headers: authorization(admin.accessToken),
      data: {
        brand_id: brand.id,
        name: `U02B 权威批次产品 ${suffix}`,
        origin: productOrigin,
      },
    }),
    201,
    "create product"
  );
  const sku = await expectJson<CreatedRow>(
    await request.post(`${API_BASE}/api/v1/skus`, {
      headers: authorization(admin.accessToken),
      data: {
        product_id: product.id,
        code: `U02B-${suffix}`,
        name: "U02B 500g",
      },
    }),
    201,
    "create SKU"
  );

  const reversed = await request.post(`${API_BASE}/api/v1/production-batches`, {
    headers: authorization(admin.accessToken),
    data: {
      product_id: product.id,
      sku_id: sku.id,
      batch_code: `U02B-REVERSED-${suffix}`,
      production_date: "2026-08-10",
      expiry_date: "2026-08-09",
    },
  });
  expect(reversed.status()).toBe(422);

  const insecureEvidence = await request.post(
    `${API_BASE}/api/v1/products/${product.id}/assets`,
    {
      headers: authorization(admin.accessToken),
      data: {
        asset_type: "test_report",
        name: "U02B 不安全检测报告",
        valid_until: "2099-12-31",
        file_url: "http://cdn.example.com/u02b/report.pdf",
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
        name: "U02B 过期证书",
        valid_until: "2020-01-01",
        file_url: "https://cdn.example.com/u02b/expired.pdf",
      },
    }
  );
  expect(expiredEvidence.status()).toBe(422);

  const batchOrigin = "本批次黑龙江五常基地";
  const batch = await expectJson<CreatedRow>(
    await request.post(`${API_BASE}/api/v1/production-batches`, {
      headers: authorization(admin.accessToken),
      data: {
        product_id: product.id,
        sku_id: sku.id,
        batch_code: `U02B-AUTH-${suffix}`,
        production_date: "2026-08-10",
        expiry_date: "2027-08-10",
        origin: batchOrigin,
      },
    }),
    201,
    "create authoritative production batch"
  );

  const template = await expectJson<CreatedRow>(
    await request.post(`${API_BASE}/api/v1/page-templates`, {
      headers: authorization(admin.accessToken),
      data: {
        name: `U02B 批次消费者页 ${suffix}`,
        template_type: "traceability",
        product_id: product.id,
      },
    }),
    201,
    "create consumer page"
  );
  const version = await expectJson<CreatedRow>(
    await request.post(
      `${API_BASE}/api/v1/page-templates/${template.id}/versions`,
      {
        headers: authorization(admin.accessToken),
        data: {
          config_json: {
            modules: [
              {
                id: "trace",
                type: "light_traceability",
                enabled: true,
                config: {
                  fields: [
                    "origin",
                    "production_date",
                    "expiry_date",
                    "batch_code",
                  ],
                },
              },
              {
                id: "benefit",
                type: "benefit_card",
                enabled: true,
                config: { title: "U02B 可领取权益", benefit_type: "coupon" },
              },
            ],
          },
        },
      }
    ),
    201,
    "create consumer page version"
  );
  await expectJson<CreatedRow>(
    await request.post(
      `${API_BASE}/api/v1/page-versions/${version.id}/publish`,
      {
        headers: authorization(admin.accessToken),
      }
    ),
    200,
    "publish consumer page"
  );

  const codeIds = {
    productId: product.id,
    skuId: sku.id,
    productionBatchId: batch.id,
  };
  const activeCodeBatch = await expectJson<CreatedRow>(
    await createCodeBatch(
      request,
      admin.accessToken,
      codeIds,
      `U02B-LIVE-${suffix}`
    ),
    201,
    "create active-path code batch"
  );
  const pendingCodeBatch = await expectJson<CreatedRow>(
    await createCodeBatch(
      request,
      admin.accessToken,
      codeIds,
      `U02B-PENDING-${suffix}`
    ),
    201,
    "create pending code batch"
  );
  await expectJson<Record<string, unknown>>(
    await request.post(
      `${API_BASE}/api/v1/code-batches/${activeCodeBatch.id}/activate`,
      {
        headers: authorization(admin.accessToken),
      }
    ),
    200,
    "activate first code batch"
  );
  const codeItems = await expectJson<{ items: Array<{ public_id: string }> }>(
    await request.get(`${API_BASE}/api/v1/code-items`, {
      headers: authorization(admin.accessToken),
      params: { code_batch_id: activeCodeBatch.id, page_size: 100 },
    }),
    200,
    "read generated code"
  );
  const publicId = codeItems.items[0]?.public_id;
  expect(publicId).toBeTruthy();

  await page.goto(`${H5_BASE}/c/${publicId}`);
  await expect(page.getByText(batchOrigin, { exact: true })).toBeVisible();
  await expect(page.getByText(productOrigin, { exact: true })).toHaveCount(0);
  await expect(
    page.getByText("U02B 可领取权益", { exact: true })
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "立即领取" })).toBeVisible();

  await page.goto(`${ADMIN_BASE}/batches`);
  const row = page
    .getByRole("row")
    .filter({ hasText: String(batch.batch_code) });
  await expect(row).toBeVisible();
  const recallResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url() ===
        `${API_BASE}/api/v1/production-batches/${batch.id}/recall`
  );
  await row.getByRole("button", { name: "召回批次" }).click();
  const recallReason = "U02B 抽检发现质量指标异常，请停止使用";
  await page.getByLabel("召回原因").fill(recallReason);
  await page.getByRole("button", { name: "确认召回" }).click();
  expect((await recallResponse).status()).toBe(200);

  const createAfterRecall = await createCodeBatch(
    request,
    admin.accessToken,
    codeIds,
    `U02B-RECALLED-${suffix}`
  );
  expect(createAfterRecall.status()).toBe(409);
  const activateAfterRecall = await request.post(
    `${API_BASE}/api/v1/code-batches/${pendingCodeBatch.id}/activate`,
    { headers: authorization(admin.accessToken) }
  );
  expect(activateAfterRecall.status()).toBe(409);

  const recalledResolve = await request.get(`${API_BASE}/c/${publicId}`, {
    headers: { Accept: "application/json" },
  });
  expect(recalledResolve.status()).toBe(200);
  const recalledPayload = (await recalledResolve.json()) as {
    scan_token?: string | null;
    code_data?: {
      batch?: {
        status?: string;
        origin?: string;
        recall_reason?: string;
        recalled_at?: string;
        recalled_by?: string;
      };
    };
    scan_info?: { recall_warning?: { reason?: string; recalled_at?: string } };
  };
  expect(recalledPayload.scan_token).toBeFalsy();
  expect(recalledPayload.code_data?.batch).toMatchObject({
    status: "recalled",
    origin: batchOrigin,
    recall_reason: recallReason,
  });
  expect(recalledPayload.code_data?.batch).not.toHaveProperty("recalled_by");
  expect(recalledPayload.scan_info?.recall_warning?.reason).toBe(recallReason);

  const blockedConsumerRequests: string[] = [];
  const blockedListener = (outgoing: { method(): string; url(): string }) => {
    if (
      outgoing.url().includes("/api/v1/scan-events") ||
      /\/api\/v1\/(benefits|claims|points|leads)(?:[/?]|$)/.test(outgoing.url())
    ) {
      blockedConsumerRequests.push(`${outgoing.method()} ${outgoing.url()}`);
    }
  };
  page.on("request", blockedListener);
  await page.goto(`${H5_BASE}/c/${publicId}`);
  await expect(
    page.getByRole("alert", { name: "该生产批次已召回" })
  ).toBeVisible();
  await expect(page.getByText(recallReason, { exact: false })).toBeVisible();
  await expect(page.getByText(batchOrigin, { exact: true })).toBeVisible();
  await expect(page.getByText("U02B 可领取权益", { exact: true })).toHaveCount(
    0
  );
  await expect(page.getByRole("button", { name: "立即领取" })).toHaveCount(0);
  await page.waitForTimeout(300);
  page.off("request", blockedListener);
  expect(blockedConsumerRequests).toEqual([]);

  await page.goto(`${ADMIN_BASE}/batches`);
  const batchListStatuses: number[] = [];
  const batchListFailures: string[] = [];
  const batchListResponseListener = (response: Response) => {
    if (
      response.request().method() === "GET" &&
      response.url().startsWith(`${API_BASE}/api/v1/production-batches`)
    ) {
      batchListStatuses.push(response.status());
    }
  };
  const batchListRequestFailedListener = (request: Request) => {
    if (
      request.method() === "GET" &&
      request.url().startsWith(`${API_BASE}/api/v1/production-batches`)
    ) {
      batchListFailures.push(request.failure()?.errorText || "requestfailed");
    }
  };
  const batchListError = page
    .getByRole("alert")
    .filter({ hasText: "生产批次列表加载失败" });
  page.on("response", batchListResponseListener);
  page.on("requestfailed", batchListRequestFailedListener);
  try {
    await psql("REVOKE SELECT ON TABLE production_batches FROM yimatong_app;");
    try {
      await page.reload();
      await expect
        .poll(
          () =>
            batchListStatuses.some((status) => status >= 500) ||
            batchListFailures.length > 0
        )
        .toBe(true);
      await expect(batchListError).toBeVisible();
      await expect(page.getByText("暂无生产批次")).toHaveCount(0);
    } finally {
      await psql("GRANT SELECT ON TABLE production_batches TO yimatong_app;");
    }
    const retriedList = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        response.url().startsWith(`${API_BASE}/api/v1/production-batches`)
    );
    await batchListError
      .getByRole("button", { name: /重\s*试/ })
      .click({ timeout: 4_000 });
    expect((await retriedList).status()).toBe(200);
  } finally {
    page.off("response", batchListResponseListener);
    page.off("requestfailed", batchListRequestFailedListener);
  }

  await psql(
    "UPDATE tenants SET plan_expires_at = now() - interval '1 day' WHERE slug = 'demo' AND tenant_type = 'brand';"
  );
  await page.reload();
  await expect(
    page.getByText("当前套餐已到期，后台已切换为只读")
  ).toBeVisible();
  await expect(
    page.getByText(String(batch.batch_code), { exact: true })
  ).toBeVisible();
  const expiredPlanWrites: string[] = [];
  const expiredListener = (outgoing: { method(): string; url(): string }) => {
    if (
      ["POST", "PATCH", "DELETE"].includes(outgoing.method()) &&
      outgoing.url().includes("/api/v1/production-batches")
    ) {
      expiredPlanWrites.push(`${outgoing.method()} ${outgoing.url()}`);
    }
  };
  page.on("request", expiredListener);
  const createButton = page.getByRole("button", { name: /新建批次/ });
  await expect(createButton).toBeDisabled();
  await createButton.evaluate((button: HTMLButtonElement) => button.click());
  await page.goto(`${ADMIN_BASE}/products/${product.id}`);
  await page.getByRole("tab", { name: "批次" }).click();
  const importButton = page.getByRole("button", { name: "批量导入" });
  await expect(importButton).toBeDisabled();
  await importButton.evaluate((button: HTMLButtonElement) => button.click());
  await page.waitForTimeout(300);
  page.off("request", expiredListener);
  expect(expiredPlanWrites).toEqual([]);

  for (const id of [brand.id, product.id, sku.id, batch.id]) {
    expect(id).toMatch(UUID_PATTERN);
  }
  const auditRaw = await psql(
    `SELECT json_agg(json_build_object(
       'action', action,
       'operator_id', operator_id,
       'resource', resource,
       'reason', details->>'reason',
       'result', details->>'result'
     ) ORDER BY timestamp, id)::text
       FROM platform_audit_log
      WHERE resource = 'production_batch:${batch.id}';`
  );
  const audits = JSON.parse(auditRaw) as Array<{
    action: string;
    operator_id: string;
    resource: string;
    reason: string | null;
    result: string;
  }>;
  expect(audits.map((audit) => audit.action)).toEqual([
    "production_batch_created",
    "production_batch_recalled",
  ]);
  expect(audits.every((audit) => audit.operator_id === admin.accountId)).toBe(
    true
  );
  expect(audits.every((audit) => audit.result === "success")).toBe(true);
  expect(audits.at(-1)?.reason).toBe(recallReason);
});
