/**
 * yimatong-zgb1.1 验收旅程 — Admin + H5 + API + 只读数据库 三层证据。
 *
 * 与 core-flow.spec.ts（一次性数据 + UI 创建旅程）互补：本 spec 验证的是「从干净环境
 * 可重复创建的基准数据」是否能被真实用户入口消费。它不创建业务实体，只消费
 * `app.cli baseline build` 产出的稳定基准数据。
 *
 * 三层证据：
 *   - 浏览器：Admin 登录后能看到 PRODUCT-BASE；H5 /c/{publicId} 能加载并渲染产品与批次；
 *   - API：GET /c/{publicId} (Accept: json) 返回契约结构（仅 200 不算通过）；
 *   - 数据库：H5 访问后该 publicId 在 scan_events 存在持久化记录。
 *
 * 前置：运行中的 infra PG (5433) + backend (8000) + admin (3000) + h5 (3003)。
 * global-setup.ts 仍负责起服务（webServer 配置）；本 spec 在 beforeAll 里跑 baseline build。
 */

import { execSync } from "child_process";
import { exec } from "child_process";
import { promisify } from "util";
import { test, expect, type Page } from "@playwright/test";
import { writeFileSync, existsSync, mkdirSync } from "fs";
import path from "path";

const promisifiedExec = promisify(exec);

const E2E_DIR = __dirname;
const RESULTS_DIR = path.join(E2E_DIR, ".results");

const API_BASE = process.env.API_BASE_URL || "http://localhost:8000";
const ADMIN_BASE = "http://localhost:3000";
const H5_BASE = "http://localhost:3003";
const BACKEND_DIR = path.resolve(__dirname, "../../backend");
const AUTH_DIR = path.join(__dirname, ".auth");
const BASELINE_CTX_FILE = path.join(AUTH_DIR, "baseline-context.json");

interface BaselineContext {
  baselineTenant: {
    id: string;
    slug: string;
    adminEmail: string;
    adminPassword: string;
  };
  firstPublicId: string;
  product: { id: string; name: string };
  productionBatch: { id: string; batchCode: string };
  report?: { id: string; name: string };
  certificate?: { id: string; name: string };
}

/**
 * 运行 `app.cli baseline build`，幂等构建基准数据到运行中的 infra PG。
 * 注意：infra PG 在 5433；CLI 通过环境变量 database_url 覆盖（.env 默认 5432 是 stale 的）。
 */
async function ensureBaseline(): Promise<BaselineContext> {
  const out = execSync("uv run python -m app.cli baseline build --json", {
    cwd: BACKEND_DIR,
    encoding: "utf-8",
    env: {
      ...process.env,
      database_url:
        "postgresql+asyncpg://yimatong:yimatong@localhost:5433/yimatong_dev",
    },
    timeout: 90_000,
  });
  const summary = JSON.parse(out);
  const ctx: BaselineContext = {
    baselineTenant: {
      id: summary.baseline_tenant.id,
      slug: summary.baseline_tenant.slug,
      adminEmail: summary.baseline_tenant.admin_email,
      adminPassword: summary.baseline_tenant.admin_password,
    },
    firstPublicId: summary.first_public_id,
    product: { id: summary.product.id, name: summary.product.name },
    productionBatch: {
      id: summary.productionBatch.id,
      batchCode: summary.productionBatch.batchCode,
    },
    report: summary.report,
    certificate: summary.certificate,
  };
  if (!existsSync(AUTH_DIR)) mkdirSync(AUTH_DIR, { recursive: true });
  writeFileSync(BASELINE_CTX_FILE, JSON.stringify(ctx, null, 2));
  return ctx;
}

async function loginBaselineAdmin(ctx: BaselineContext): Promise<string> {
  const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: ctx.baselineTenant.adminEmail,
      password: ctx.baselineTenant.adminPassword,
      tenant_slug: ctx.baselineTenant.slug,
    }),
  });
  expect(res.status, `login should succeed: ${await res.text()}`).toBe(200);
  const body = await res.json();
  return body.access_token as string;
}

async function countScanEvents(publicId: string): Promise<number> {
  // 只读 pg 直连（与验收 verifier 同源），证明 H5 访问真正持久化。
  const { stdout } = await promisifiedExec(
    `psql -h 127.0.0.1 -p 5433 -U yimatong -d yimatong_dev -t -A -c ` +
      `"SELECT count(*) FROM scan_events WHERE public_id = '${publicId}';"`,
    { env: { ...process.env, PGPASSWORD: "yimatong" } }
  );
  return parseInt(stdout.trim(), 10) || 0;
}

/**
 * 读取 code_items.first_scanned_at（单一权威首查时间源）。
 * yimatong-zgb1.4：验证 API 响应的 scan_info.first_scan_time 与 DB 一致。
 */
async function readFirstScannedAt(publicId: string): Promise<string | null> {
  const { stdout } = await promisifiedExec(
    `psql -h 127.0.0.1 -p 5433 -U yimatong -d yimatong_dev -t -A -c ` +
      `"SELECT first_scanned_at FROM code_items WHERE public_id = '${publicId}';"`,
    { env: { ...process.env, PGPASSWORD: "yimatong" } }
  );
  const v = stdout.trim();
  return v ? v : null;
}

async function attachAuthCookie(page: Page, token: string) {
  await page.context().addCookies([
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
  ]);
  await page.context().addInitScript((t) => {
    try {
      localStorage.setItem("access_token", t);
    } catch {
      /* ignore */
    }
  }, token);
}

test.describe("yimatong-zgb1 baseline journey (Admin + H5 + API + DB)", () => {
  // yimatong-zgb1.1：Admin + H5 + API + DB 三层证据骨架
  // yimatong-zgb1.2：权威产品/批次/资产资料（test_reports + certificates）
  // yimatong-zgb1.3：code_data.lifecycle 四状态契约
  // yimatong-zgb1.4：scan_info 首次查验契约 + 轻防伪文案 + DB first_scanned_at 一致性
  let ctx: BaselineContext;
  let token: string;

  test.beforeAll(async () => {
    ctx = await ensureBaseline();
    token = await loginBaselineAdmin(ctx);
    if (!existsSync(RESULTS_DIR)) mkdirSync(RESULTS_DIR, { recursive: true });
  });

  test("Admin: baseline admin sees PRODUCT-BASE in product list", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    await attachAuthCookie(page, token);
    // 直接进入产品列表（已通过 API 登录获得 cookie）
    await page.goto(`${ADMIN_BASE}/products`, {
      waitUntil: "domcontentloaded",
    });
    // Ant Design 表格渲染；PRODUCT-BASE 必须出现
    await expect(
      page.getByText(ctx.product.name, { exact: true }).first()
    ).toBeVisible({
      timeout: 30_000,
    });
    await page.screenshot({
      path: path.join(RESULTS_DIR, "baseline-admin-products.png"),
      fullPage: true,
    });
  });

  test("H5: scan /c/{baselinePublicId} renders product and batch", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    await page.goto(`${H5_BASE}/c/${ctx.firstPublicId}`, {
      waitUntil: "domcontentloaded",
    });
    // H5 必须渲染产品名（来自 product_hero 模块，引用权威 Product.name）
    await expect(page.getByText(ctx.product.name).first()).toBeVisible({
      timeout: 30_000,
    });
    // 生产批次码必须渲染（light_traceability 模块引用权威 ProductionBatch.batch_code）
    await expect(
      page.getByText(ctx.productionBatch.batchCode).first()
    ).toBeVisible({ timeout: 30_000 });
    // 权威检测报告 + 资质证书必须渲染（yimatong-zgb1.2：消费者只看权威资料）
    if (ctx.report?.name) {
      await expect(page.getByText(ctx.report.name).first()).toBeVisible({
        timeout: 30_000,
      });
    }
    if (ctx.certificate?.name) {
      await expect(page.getByText(ctx.certificate.name).first()).toBeVisible({
        timeout: 30_000,
      });
    }
    await page.screenshot({
      path: path.join(RESULTS_DIR, "baseline-h5-scan.png"),
      fullPage: true,
    });
  });

  test("API: GET /c/{publicId} returns structured contract (not just 200)", async () => {
    const res = await fetch(`${API_BASE}/c/${ctx.firstPublicId}`, {
      headers: { Accept: "application/json" },
    });
    expect(res.status).toBe(200);
    const body = await res.json();
    // 业务契约断言：仅 200 不算通过
    expect(body.code_data?.public_id).toBe(ctx.firstPublicId);
    expect(body.code_data?.status).toBe("activated");
    // yimatong-zgb1.3：权威四状态生命周期（activated → active）
    expect(body.code_data?.lifecycle).toBe("active");
    expect(body.code_data?.product?.name).toBe(ctx.product.name);
    expect(body.brand?.name).toBeTruthy();
    // yimatong-zgb1.2：batch 必须在 code_data 下（H5 契约位置），顶层 batch 兼容保留
    expect(body.code_data?.batch?.batch_code).toBe(
      ctx.productionBatch.batchCode
    );
    expect(body.batch?.batch_code).toBe(ctx.productionBatch.batchCode);
    // 权威检测报告 + 资质证书（来自 ProductAsset）
    expect(Array.isArray(body.code_data?.test_reports)).toBe(true);
    expect(Array.isArray(body.code_data?.certificates)).toBe(true);
    if (ctx.report?.name) {
      expect(
        body.code_data.test_reports.some(
          (r: { name: string }) => r.name === ctx.report!.name
        )
      ).toBe(true);
    }
    if (ctx.certificate?.name) {
      expect(
        body.code_data.certificates.some(
          (c: { name: string }) => c.name === ctx.certificate!.name
        )
      ).toBe(true);
    }
    // yimatong-zgb1.4：scan_info 首次查验契约（post-insert COUNT，首次 = 1）
    expect(body.scan_info).toBeTruthy();
    expect(typeof body.scan_info.is_first_scan).toBe("boolean");
    expect(typeof body.scan_info.verification_count).toBe("number");
    expect(body.scan_info.verification_count).toBeGreaterThanOrEqual(1);
    expect(body.scan_info.scan_count).toBe(body.scan_info.verification_count); // 兼容别名
    expect(body.scan_info.first_scan_time).toBeTruthy(); // ISO8601 首查时间
    expect(body.scan_info.verification_time).toBeTruthy(); // 本次查验时间
    // yimatong-zgb1.5：最近查验时间（repeat scan 时展示）
    expect(body.scan_info.last_scan_time).toBeTruthy();
  });

  test("DB: H5 visit persisted a scan_events row for the publicId", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const before = await countScanEvents(ctx.firstPublicId);
    await page.goto(`${H5_BASE}/c/${ctx.firstPublicId}`, {
      waitUntil: "domcontentloaded",
    });
    // 等待 resolver 完成扫码记录
    await expect
      .poll(async () => countScanEvents(ctx.firstPublicId), {
        timeout: 15_000,
        message: "scan_events row should be persisted after H5 visit",
      })
      .toBeGreaterThan(before);
  });

  test("yimatong-zgb1.4: H5 shows verification copy + DB first_scanned_at matches API", async ({
    page,
  }) => {
    // yimatong-zgb1.4：首次查验页面文案 + API/DB 首查时间一致性
    test.setTimeout(120_000);

    // 1. 先取 API 响应的 first_scan_time（权威首查时间）
    const apiRes = await fetch(`${API_BASE}/c/${ctx.firstPublicId}`, {
      headers: { Accept: "application/json" },
    });
    expect(apiRes.status).toBe(200);
    const apiBody = await apiRes.json();
    const apiFirstScanTime = apiBody.scan_info?.first_scan_time;
    expect(apiFirstScanTime, "API must return first_scan_time").toBeTruthy();

    // 2. H5 可见文案：首次验证 / 重复查验 + 累计查验 N 次（VerifyStatus 组件）
    await page.goto(`${H5_BASE}/c/${ctx.firstPublicId}`, {
      waitUntil: "domcontentloaded",
    });
    // "首次验证" 或 "重复查验" badge 必须出现（取决于是否首扫，baseline 码可能已被前面测试扫过）
    const verifyBadge = page
      .locator("text=首次验证")
      .or(page.locator("text=重复查验"));
    await expect(verifyBadge.first()).toBeVisible({ timeout: 30_000 });
    // yimatong-zgb1.4/1.5："累计查验" 字样必须出现（VerifyStatus 的 scanCount 区块，文案从"扫码"改为"查验"）
    await expect(page.getByText(/累计查验\s*\d+\s*次/).first()).toBeVisible({
      timeout: 30_000,
    });

    await page.screenshot({
      path: path.join(RESULTS_DIR, "baseline-h5-verification.png"),
      fullPage: true,
    });

    // 3. DB 一致性：code_items.first_scanned_at 与 API first_scan_time 秒级一致
    const dbFirstScannedAt = await readFirstScannedAt(ctx.firstPublicId);
    expect(
      dbFirstScannedAt,
      "DB first_scanned_at must be non-null after verification"
    ).toBeTruthy();
    const dbTime = new Date(dbFirstScannedAt as string).getTime();
    const apiTime = new Date(apiFirstScanTime).getTime();
    const deltaMs = Math.abs(dbTime - apiTime);
    expect(deltaMs, `API/DB first_scan_time drift ${deltaMs}ms`).toBeLessThan(
      2000
    );
  });
});
