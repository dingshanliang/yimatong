import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E configuration for 一码通 (yimatong)
 *
 * Targets:
 * - Admin: http://localhost:3000
 * - H5:    http://localhost:3003  (3001 is occupied by mock-sms in docker-compose)
 * - API:   http://localhost:8000
 */

export default defineConfig({
  testDir: "./e2e",
  // The Platform control plane has its own bootstrap, server lifecycle, and
  // cookie boundary. It is executed through playwright.platform.config.ts.
  testIgnore: "platform-auth.spec.ts",
  fullyParallel: false, // core flow tests must run serially
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  timeout: 60_000, // CI dev server compilation + hydration needs more time
  workers: 1, // serial execution for shared backend state
  reporter: process.env.CI
    ? [["html", { open: "never" }], ["list"]]
    : [["html", { open: "on-failure" }], ["list"]],

  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "on-first-retry",
  },

  globalSetup: require.resolve("./e2e/global-setup"),

  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 720 },
      },
    },
  ],

  webServer: [
    {
      command: "NEXT_PUBLIC_H5_URL=http://localhost:3003 pnpm dev:admin",
      url: "http://localhost:3000/login",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command:
        "cd apps/h5 && NEXT_PUBLIC_API_URL=http://localhost:8000 BACKEND_URL=http://localhost:8000 PORT=3003 pnpm dev",
      url: "http://localhost:3003",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
