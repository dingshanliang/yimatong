import { randomBytes } from "node:crypto";
import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

const apiPort = 18250;
const adminPort = 13250;
const h5Port = 13251;
const apiBase = `http://127.0.0.1:${apiPort}`;
const adminBase = `http://127.0.0.1:${adminPort}`;
const h5Base = `http://127.0.0.1:${h5Port}`;
const ownerToken = randomBytes(16).toString("hex");

process.env.YIMATONG_U02B_DB = `yimatong_acceptance_u02b_u05a_${Date.now().toString(36)}_${process.pid}`;
process.env.YIMATONG_U02B_OWNER_TOKEN = ownerToken;
process.env.YIMATONG_U02B_LEASE_FILE = path.resolve(
  process.cwd(),
  "e2e",
  ".auth",
  `u05a-page-authority-${ownerToken}.json`
);
process.env.YIMATONG_U02B_CONTROL_ROLE = `u02b_control_${ownerToken.slice(0, 12)}`;
process.env.YIMATONG_U02B_API_PORT = String(apiPort);
process.env.YIMATONG_U02B_API_BASE = apiBase;
process.env.YIMATONG_U02B_ADMIN_ORIGIN = adminBase;
process.env.YIMATONG_U02B_H5_ORIGIN = h5Base;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "u05a-page-authority.spec.ts",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  workers: 1,
  outputDir: "test-results/u05a-page-authority",
  reporter: "list",
  globalTeardown:
    require.resolve("./e2e/u02b-authoritative-batch-global-teardown"),
  use: {
    baseURL: adminBase,
    actionTimeout: 20_000,
    trace: "off",
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
      command: "node e2e/u02b-authoritative-batch-backend.mjs",
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
