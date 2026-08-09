import { defineConfig, devices } from "@playwright/test";
import { randomBytes } from "node:crypto";
import path from "node:path";

const apiPort = 18160;
const adminPort = 13160;
const apiBase = `http://127.0.0.1:${apiPort}`;
const adminBase = `http://127.0.0.1:${adminPort}`;
const databaseName =
  process.env.YIMATONG_U01F_DB ||
  `yimatong_acceptance_u01f_${Date.now().toString(36)}_${process.pid}`;
const ownerToken =
  process.env.YIMATONG_U01F_OWNER_TOKEN || randomBytes(16).toString("hex");
const leaseFile = path.resolve(
  process.cwd(),
  "e2e",
  ".auth",
  `u01f-plan-expiry-${ownerToken}.json`
);

process.env.YIMATONG_U01F_DB = databaseName;
process.env.YIMATONG_U01F_OWNER_TOKEN = ownerToken;
process.env.YIMATONG_U01F_LEASE_FILE = leaseFile;
process.env.YIMATONG_U01F_API_PORT = String(apiPort);
process.env.YIMATONG_U01F_API_BASE = apiBase;
process.env.YIMATONG_U01F_ADMIN_ORIGIN = adminBase;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "u01f-plan-expiry.spec.ts",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  timeout: 240_000,
  expect: { timeout: 20_000 },
  workers: 1,
  outputDir: "test-results/u01f-plan-expiry",
  reporter: [
    ["list"],
    ["json", { outputFile: "test-results/u01f-plan-expiry/results.json" }],
  ],
  globalTeardown: require.resolve("./e2e/u01f-plan-expiry-global-teardown"),
  use: {
    baseURL: adminBase,
    trace: "on",
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
      command: "node e2e/u01f-plan-expiry-backend.mjs",
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
  ],
});
