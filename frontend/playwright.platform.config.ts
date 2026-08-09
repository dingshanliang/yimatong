import { defineConfig, devices } from "@playwright/test";

const apiBase = process.env.API_BASE_URL || "http://localhost:8000";
const platformBase = process.env.PLATFORM_BASE_URL || "http://localhost:3002";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "platform-auth.spec.ts",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  timeout: 60_000,
  workers: 1,
  reporter: [["list"]],
  globalSetup: require.resolve("./e2e/platform-global-setup"),
  use: {
    baseURL: platformBase,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 720 },
      },
    },
  ],
  webServer: {
    command: `NEXT_PUBLIC_API_URL=${apiBase} pnpm dev:platform`,
    url: `${platformBase}/login`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
