/**
 * Channel closed-loop E2E smoke.
 *
 * Flow: 品牌方创建渠道主数据 -> 绑定渠道账号 -> 登记已赋码货品流向 -> 渠道/门店入口查看范围内数据。
 */

import { expect, test } from "@playwright/test";
import { readFileSync } from "fs";
import path from "path";

const API_BASE = process.env.API_BASE_URL || "http://localhost:8000";
const TEST_PASSWORD = "Channel1234";

interface TestContext {
  token: string;
  tenantId: string;
  codeBatchId: string;
}

interface AuthUser {
  account_id: string;
  tenant_id: string;
  role: string;
  email: string;
  name: string;
}

function loadContext(): TestContext {
  const raw = readFileSync(path.join(__dirname, ".auth", "context.json"), "utf-8");
  return JSON.parse(raw) as TestContext;
}

async function apiRequest(
  method: "GET" | "POST",
  endpoint: string,
  token: string,
  body?: unknown
): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : {};
  if (!res.ok) {
    throw new Error(`${method} ${endpoint} failed: ${res.status} ${res.statusText} - ${text.slice(0, 200)}`);
  }
  return json;
}

async function login(email: string, password: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : {};
  if (!res.ok) {
    throw new Error(`login failed: ${res.status} ${text.slice(0, 200)}`);
  }
  return json.access_token as string;
}

function storageState(token: string, user: AuthUser) {
  return {
    cookies: [
      {
        name: "access_token",
        value: token,
        domain: "localhost",
        path: "/",
        expires: Math.floor(Date.now() / 1000) + 3600,
        httpOnly: false,
        secure: false,
        sameSite: "Lax" as const,
      },
    ],
    origins: [
      {
        origin: "http://localhost:3000",
        localStorage: [
          { name: "access_token", value: token },
          { name: "auth_store", value: JSON.stringify(user) },
        ],
      },
    ],
  };
}

const ctx = loadContext();

test.use({ storageState: path.join(__dirname, ".auth", "state.json") });

test.describe.serial("渠道管理闭环", () => {
  test("品牌方配置渠道后，经销商和门店入口可查看范围内数据", async ({ browser, page }) => {
    const suffix = Date.now();

    const distributor = await apiRequest("POST", "/api/v1/channels/distributors", ctx.token, {
      name: `E2E 经销商 ${suffix}`,
      contact_name: "渠道负责人",
      contact_phone: "13800000000",
    });
    const region = await apiRequest("POST", "/api/v1/channels/regions", ctx.token, {
      name: `E2E 区域 ${suffix}`,
      province: "上海",
      city: "上海",
      distributor_id: distributor.id,
    });
    const store = await apiRequest("POST", "/api/v1/channels/stores", ctx.token, {
      name: `E2E 门店 ${suffix}`,
      region_id: region.id,
      distributor_id: distributor.id,
      address: "上海市黄浦区测试路 1 号",
    });
    await apiRequest("POST", "/api/v1/channels/code-allocations", ctx.token, {
      batch_id: ctx.codeBatchId,
      target_type: "region",
      region_id: region.id,
      quantity: 1,
    });

    const org = await apiRequest("POST", "/api/v1/organizations", ctx.token, {
      name: `E2E 渠道组织 ${suffix}`,
    });
    const distEmail = `e2e-dist-${suffix}@example.com`;
    const storeEmail = `e2e-store-${suffix}@example.com`;
    const distAccount = await apiRequest("POST", "/api/v1/accounts", ctx.token, {
      email: distEmail,
      name: "E2E 经销商账号",
      password: TEST_PASSWORD,
      organization_id: org.id,
      role_ids: [],
    });
    const storeAccount = await apiRequest("POST", "/api/v1/accounts", ctx.token, {
      email: storeEmail,
      name: "E2E 门店账号",
      password: TEST_PASSWORD,
      organization_id: org.id,
      role_ids: [],
    });
    await apiRequest("POST", "/api/v1/channels/account-scopes", ctx.token, {
      account_id: distAccount.id,
      scope_type: "distributor",
      distributor_id: distributor.id,
    });
    await apiRequest("POST", "/api/v1/channels/account-scopes", ctx.token, {
      account_id: storeAccount.id,
      scope_type: "store",
      store_id: store.id,
    });

    await page.goto("/channels");
    await expect(page.getByRole("heading", { name: "渠道管理" })).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(distributor.name as string)).toBeVisible();
    await page.getByRole("tab", { name: "流向登记" }).click();
    await expect(page.getByText(region.name as string)).toBeVisible();
    await page.getByRole("tab", { name: "账号授权" }).click();
    await expect(page.getByText("E2E 经销商账号")).toBeVisible();
    await expect(page.getByText("E2E 门店账号")).toBeVisible();

    const distToken = await login(distEmail, TEST_PASSWORD);
    const distContext = await browser.newContext({
      storageState: storageState(distToken, {
        account_id: distAccount.id as string,
        tenant_id: ctx.tenantId,
        role: "distributor",
        email: distEmail,
        name: "E2E 经销商账号",
      }),
    });
    const distPage = await distContext.newPage();
    await distPage.goto("/channel-portal");
    await expect(distPage.getByRole("heading", { name: "经销商工作台" })).toBeVisible({ timeout: 15000 });
    await expect(distPage.getByText(distributor.name as string)).toBeVisible();
    await expect(distPage.getByText(region.name as string).first()).toBeVisible();
    await distContext.close();

    const storeToken = await login(storeEmail, TEST_PASSWORD);
    const storeContext = await browser.newContext({
      storageState: storageState(storeToken, {
        account_id: storeAccount.id as string,
        tenant_id: ctx.tenantId,
        role: "store_guide",
        email: storeEmail,
        name: "E2E 门店账号",
      }),
    });
    const storePage = await storeContext.newPage();
    await storePage.goto("/store-portal");
    await expect(storePage.getByRole("heading", { name: "门店工作台" })).toBeVisible({ timeout: 15000 });
    await expect(storePage.getByText(store.name as string)).toBeVisible();
    await expect(storePage.getByText("上海市黄浦区测试路 1 号")).toBeVisible();
    await storeContext.close();
  });
});
