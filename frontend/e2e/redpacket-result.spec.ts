/**
 * kc6d.9 — 红包结果页 E2E happy path。
 *
 * 常跑部分（无需真实微信支付凭证，既有配置即可稳定通过）：
 *   - /redpacket/result 对不存在的 claim 显示统一的"暂时无法查询结果"（防枚举语义的真实浏览器证据）；
 *   - 缺 claim_id 参数时不发起轮询直接进入不可查询态。
 *
 * 完整旅程复用 global-setup 创建的真实码、页面和权益；仅在微信支付边界使用
 * Playwright 的确定性响应，避免测试依赖真实 OpenID、商户证书或资金转账。
 *
 * 前置与既有 spec 一致：infra PG (5433) + backend (8000) + h5 (3003)。
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { test, expect } from "@playwright/test";

const H5_BASE = "http://localhost:3003";
const API_BASE = process.env.API_BASE_URL || "http://localhost:8000";

interface TestContext {
  publicId: string;
  benefitId: string;
}

function loadContext(): TestContext {
  const raw = readFileSync(
    path.join(__dirname, ".auth", "context.json"),
    "utf-8"
  );
  return JSON.parse(raw) as TestContext;
}

test.describe("红包结果页（kc6d.9）", () => {
  test("查询资格缺失时如实展示不可查询态与客服路径", async ({ page }) => {
    await page.goto(
      `${H5_BASE}/redpacket/result?claim_id=00000000-0000-0000-0000-000000000000&public_id=pk-e2e`
    );

    await expect(page.getByText("暂时无法查询结果")).toBeVisible({
      timeout: 15_000,
    });
    // 处理中文案不承诺固定到账时长
    await expect(page.getByText("1-3 分钟")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "返回" })).toBeVisible();
  });

  test("缺少 claim_id 时不发起状态查询", async ({ page }) => {
    const requests: string[] = [];
    page.on("request", (req) => {
      if (req.url().includes("/benefit-claims/")) requests.push(req.url());
    });

    await page.goto(`${H5_BASE}/redpacket/result`);

    await expect(page.getByText("暂时无法查询结果")).toBeVisible({
      timeout: 15_000,
    });
    expect(requests).toHaveLength(0);
  });

  test("扫码→领取→结果页轮询到成功终态（完整旅程）", async ({ page }) => {
    const ctx = loadContext();
    const claimId = "00000000-0000-4000-8000-000000000009";
    const revisitCredential = "e2e-redpacket-revisit-credential";
    let statusPolls = 0;

    // 保留真实扫码解析结果，只把本用例的权益卡呈现为现金红包；真实微信
    // 身份绑定与支付不应成为本地 H5 页面旅程的前置条件。
    await page.route(`${API_BASE}/c/${ctx.publicId}`, async (route) => {
      const response = await route.fetch();
      const payload = (await response.json()) as {
        scan_info?: Record<string, unknown>;
        page_config?: {
          modules?: Array<{
            id?: string;
            type?: string;
            config?: Record<string, unknown>;
          }>;
        };
      };
      payload.scan_info = {
        ...payload.scan_info,
        benefit_paused: false,
        paused_reason: null,
      };
      payload.page_config = {
        modules: [
          {
            id: "e2e-redpacket-benefit",
            type: "benefit_card",
            config: {
              benefit_id: ctx.benefitId,
              benefit_type: "cash_red_packet",
              title: "E2E 现金红包",
              description: "Playwright 红包结果页旅程",
            },
          },
        ],
      };
      await route.fulfill({ response, json: payload });
    });

    // 领取 API 的外部支付前置由确定性响应替代；请求本身仍由真实 H5
    // 权益卡发起，并携带当前扫码生成的匿名访客身份。
    await page.route(/\/api\/v1\/benefit-claims$/, async (route) => {
      const request = route.request();
      expect(request.method()).toBe("POST");
      expect(request.postDataJSON()).toEqual({ benefit_id: ctx.benefitId });
      expect(request.headers()["x-visitor-id"]).toMatch(/^\S+$/);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "pending",
          benefit_id: ctx.benefitId,
          claim_id: claimId,
          revisit_credential: revisitCredential,
        }),
      });
    });

    await page.route(
      new RegExp(`/api/v1/benefit-claims/${claimId}/status$`),
      async (route) => {
        statusPolls += 1;
        expect(route.request().headers().authorization).toBe(
          `Bearer ${revisitCredential}`
        );
        // 连续两次 processing，确保浏览器可观察到处理中阶段；第三次终态。
        const terminal = statusPolls >= 3;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(
            terminal
              ? {
                  status: "success",
                  amount_minor: 888,
                  completed_at: "2026-08-16T14:00:00Z",
                  failure_reason: null,
                }
              : {
                  status: "processing",
                  amount_minor: null,
                  completed_at: null,
                  failure_reason: null,
                }
          ),
        });
      }
    );

    await page.goto(`${H5_BASE}/c/${ctx.publicId}`);
    await expect(page.getByText("E2E 现金红包")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "领取红包" }).click();
    await expect(page).toHaveURL(
      new RegExp(`/redpacket/result\\?claim_id=${claimId}`)
    );
    await expect(page.getByText("处理中")).toBeVisible();

    // 终态：到账口径（不得用"领取成功"表述到账）+ 金额（元）
    await expect(page.getByText("红包已到账")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("领取成功")).toHaveCount(0);
    await expect(page.getByText("8.88")).toBeVisible();
    await expect(page.getByText("元", { exact: true })).toBeVisible();
    // 终态后不再轮询
    const pollsAfterTerminal = statusPolls;
    await page.waitForTimeout(3_500);
    expect(statusPolls).toBe(pollsAfterTerminal);
  });
});
