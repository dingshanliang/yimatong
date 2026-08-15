/**
 * kc6d.9 — 红包结果页 E2E happy path。
 *
 * 常跑部分（无需红包夹具，既有配置即可稳定通过）：
 *   - /redpacket/result 对不存在的 claim 显示统一的"暂时无法查询结果"（防枚举语义的真实浏览器证据）；
 *   - 缺 claim_id 参数时不发起轮询直接进入不可查询态。
 *
 * 完整旅程（扫码→领取→跳结果页→轮询到成功终态）需要完整的现金红包夹具
 * （租户开通 cash_red_packet、wechat_pay_transfer connector、已上线活动与码、
 * 微信授权绑定），通过 E2E_RED_PACKET_RESULT_URL 提供领取受理后的结果页 URL 时启用。
 *
 * 前置与既有 spec 一致：infra PG (5433) + backend (8000) + h5 (3003)。
 */

import { test, expect } from "@playwright/test";

const H5_BASE = "http://localhost:3003";

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

  const resultUrl = process.env.E2E_RED_PACKET_RESULT_URL;
  test.skip(
    !resultUrl,
    "完整 happy path 需要现金红包夹具：设置 E2E_RED_PACKET_RESULT_URL（领取受理后结果页 URL）后启用"
  );
  test("扫码→领取→结果页轮询到成功终态（完整旅程）", async ({ page }) => {
    test.skip(!resultUrl, "缺少夹具");
    await page.goto(resultUrl!);

    // 终态：到账口径（不得用"领取成功"表述到账）+ 金额（元）
    await expect(page.getByText("红包已到账")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("领取成功")).toHaveCount(0);
    await expect(
      page.locator("text=/\\d+(\\.\\d+)?\\s*元/").first()
    ).toBeVisible();
    // 终态后不再轮询
    const countAfterTerminal = await page.evaluate(
      () => performance.getEntriesByType("resource").length
    );
    await page.waitForTimeout(2_000);
    const countLater = await page.evaluate(
      () => performance.getEntriesByType("resource").length
    );
    expect(countLater - countAfterTerminal).toBeLessThanOrEqual(1);
  });
});
