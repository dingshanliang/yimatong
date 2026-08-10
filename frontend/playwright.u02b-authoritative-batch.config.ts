import { randomBytes } from "node:crypto";
import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

const apiPort = 18230;
const adminPort = 13230;
const h5Port = 13231;
const apiBase = `http://127.0.0.1:${apiPort}`;
const adminBase = `http://127.0.0.1:${adminPort}`;
const h5Base = `http://127.0.0.1:${h5Port}`;
const databaseName =
  process.env.YIMATONG_U02B_DB ||
  `yimatong_acceptance_u02b_${Date.now().toString(36)}_${process.pid}`;
const ownerToken =
  process.env.YIMATONG_U02B_OWNER_TOKEN || randomBytes(16).toString("hex");
const controlRole = `u02b_control_${ownerToken.slice(0, 12)}`;
const leaseFile = path.resolve(
  process.cwd(),
  "e2e",
  ".auth",
  `u02b-authoritative-batch-${ownerToken}.json`
);

process.env.YIMATONG_U02B_DB = databaseName;
process.env.YIMATONG_U02B_OWNER_TOKEN = ownerToken;
process.env.YIMATONG_U02B_LEASE_FILE = leaseFile;
process.env.YIMATONG_U02B_CONTROL_ROLE = controlRole;
process.env.YIMATONG_U02B_API_PORT = String(apiPort);
process.env.YIMATONG_U02B_API_BASE = apiBase;
process.env.YIMATONG_U02B_ADMIN_ORIGIN = adminBase;
process.env.YIMATONG_U02B_H5_ORIGIN = h5Base;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "u02b-authoritative-batch.spec.ts",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  workers: 1,
  outputDir: "test-results/u02b-authoritative-batch",
  reporter: [
    ["list"],
    [
      "json",
      { outputFile: "test-results/u02b-authoritative-batch/results.json" },
    ],
  ],
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
    {
      command: `NEXT_PUBLIC_API_URL=${apiBase} BACKEND_URL=${apiBase} pnpm --filter @yimatong/h5 exec next dev --port ${h5Port}`,
      url: `${h5Base}/c/U02B-READINESS`,
      reuseExistingServer: false,
      timeout: 180_000,
    },
  ],
});
