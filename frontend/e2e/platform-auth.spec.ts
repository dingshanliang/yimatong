import { expect, test } from "@playwright/test";
import { readFile } from "fs/promises";
import path from "path";

const API_BASE = process.env.API_BASE_URL || "http://localhost:8000";
const PLATFORM_BASE = process.env.PLATFORM_BASE_URL || "http://localhost:3002";

test("platform cookie session fails closed and logout remains retryable", async ({
  page,
  request,
}) => {
  const context = JSON.parse(
    await readFile(path.join(__dirname, ".auth", "context.json"), "utf8")
  ) as { token: string };

  const tenantBearer = await request.get(
    `${API_BASE}/api/v1/platform/dashboard`,
    { headers: { Authorization: `Bearer ${context.token}` } }
  );
  expect(tenantBearer.status()).toBe(401);

  await page.goto(`${PLATFORM_BASE}/login`);
  await page.getByPlaceholder("管理员邮箱").fill("platform@yimatong.cn");
  await page.getByPlaceholder("密码").fill("platform_admin_2026");
  await page.getByRole("button", { name: "登 录" }).click();

  await expect(page).toHaveURL(`${PLATFORM_BASE}/`);
  await expect(page.getByRole("heading", { name: "平台概览" })).toBeVisible();

  const platformCookies = await page.context().cookies(API_BASE);
  const cookieHeader = platformCookies
    .filter((cookie) => cookie.name.startsWith("platform_"))
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join("; ");
  const missingCsrf = await request.post(
    `${API_BASE}/api/v1/platform/auth/logout`,
    {
      headers: { Cookie: cookieHeader, Origin: PLATFORM_BASE },
      data: {},
    }
  );
  expect(missingCsrf.status()).toBe(403);

  await page.route("**/api/v1/platform/auth/logout", (route) =>
    route.abort("failed")
  );
  await page.getByRole("button", { name: "平台管理员" }).click();
  await page.getByText("退出登录", { exact: true }).click();
  await expect(page.getByText("退出登录失败，请重试")).toBeVisible();
  await expect(page).toHaveURL(`${PLATFORM_BASE}/`);

  await page.unroute("**/api/v1/platform/auth/logout");
  const logoutRequestPromise = page.waitForRequest(
    (candidate) =>
      candidate.method() === "POST" &&
      candidate.url() === `${API_BASE}/api/v1/platform/auth/logout`
  );
  await page.getByRole("button", { name: "平台管理员" }).click();
  await page.getByText("退出登录", { exact: true }).click();
  const logoutRequest = await logoutRequestPromise;
  const logoutHeaders = await logoutRequest.allHeaders();
  expect(logoutHeaders.origin).toBe(PLATFORM_BASE);
  expect(logoutHeaders["x-platform-csrf"]).toBeTruthy();

  await expect(page).toHaveURL(`${PLATFORM_BASE}/login`);
  const afterLogout = await page
    .context()
    .request.get(`${API_BASE}/api/v1/platform/dashboard`);
  expect(afterLogout.status()).toBe(401);
});
