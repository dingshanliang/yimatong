import { defineConfig, devices } from "@playwright/test";
import { randomBytes } from "node:crypto";
import path from "node:path";

const apiPort = 18100;
const adminPort = 13100;
const platformPort = 13102;
const apiBase = `http://127.0.0.1:${apiPort}`;
const adminBase = `http://127.0.0.1:${adminPort}`;
const platformBase = `http://127.0.0.1:${platformPort}`;
const databaseName =
  process.env.YIMATONG_LIFECYCLE_DB ||
  `yimatong_acceptance_u01c_browser_${Date.now()}_${process.pid}`;
const ownerToken =
  process.env.YIMATONG_LIFECYCLE_OWNER_TOKEN || randomBytes(16).toString("hex");
const leaseFile = path.resolve(
  process.cwd(),
  "e2e",
  ".auth",
  `platform-lifecycle-${ownerToken}.json`
);

process.env.YIMATONG_LIFECYCLE_DB = databaseName;
process.env.YIMATONG_LIFECYCLE_OWNER_TOKEN = ownerToken;
process.env.YIMATONG_LIFECYCLE_LEASE_FILE = leaseFile;
process.env.YIMATONG_LIFECYCLE_API_PORT = String(apiPort);
process.env.YIMATONG_LIFECYCLE_API_BASE = apiBase;
process.env.YIMATONG_LIFECYCLE_ADMIN_ORIGIN = adminBase;
process.env.YIMATONG_LIFECYCLE_PLATFORM_ORIGIN = platformBase;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "platform-tenant-lifecycle.spec.ts",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  timeout: 240_000,
  expect: { timeout: 15_000 },
  workers: 1,
  outputDir: "test-results/platform-lifecycle",
  reporter: [
    ["list"],
    ["json", { outputFile: "test-results/platform-lifecycle/results.json" }],
  ],
  globalTeardown: require.resolve("./e2e/platform-lifecycle-global-teardown"),
  use: {
    baseURL: platformBase,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
  webServer: [
    {
      command: "node e2e/platform-lifecycle-backend.mjs",
      url: `${apiBase}/health`,
      reuseExistingServer: false,
      timeout: 180_000,
    },
    {
      command: `NEXT_PUBLIC_API_URL=${apiBase} pnpm --filter @yimatong/admin exec next dev --port ${adminPort}`,
      url: `${adminBase}/login`,
      reuseExistingServer: false,
      timeout: 180_000,
    },
    {
      command: `NEXT_PUBLIC_API_URL=${apiBase} pnpm --filter @yimatong/platform exec next dev --port ${platformPort}`,
      url: `${platformBase}/login`,
      reuseExistingServer: false,
      timeout: 180_000,
    },
  ],
});
