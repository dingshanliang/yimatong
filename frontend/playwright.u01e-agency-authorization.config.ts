import { defineConfig, devices } from "@playwright/test";
import { randomBytes } from "node:crypto";
import path from "node:path";

const apiPort = 18180;
const adminPort = 13180;
const apiBase = `http://127.0.0.1:${apiPort}`;
const adminBase = `http://127.0.0.1:${adminPort}`;
const databaseName =
  process.env.YIMATONG_U01E_DB ||
  `yimatong_acceptance_u01e_${Date.now().toString(36)}_${process.pid}`;
const ownerToken =
  process.env.YIMATONG_U01E_OWNER_TOKEN || randomBytes(16).toString("hex");
const controlRole = `u01e_control_${ownerToken.slice(0, 12)}`;
const leaseFile = path.resolve(
  process.cwd(),
  "e2e",
  ".auth",
  `u01e-agency-authorization-${ownerToken}.json`
);

process.env.YIMATONG_U01E_DB = databaseName;
process.env.YIMATONG_U01E_OWNER_TOKEN = ownerToken;
process.env.YIMATONG_U01E_LEASE_FILE = leaseFile;
process.env.YIMATONG_U01E_CONTROL_ROLE = controlRole;
process.env.YIMATONG_U01E_API_PORT = String(apiPort);
process.env.YIMATONG_U01E_API_BASE = apiBase;
process.env.YIMATONG_U01E_ADMIN_ORIGIN = adminBase;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "u01e-agency-authorization.spec.ts",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  workers: 1,
  outputDir: "test-results/u01e-agency-authorization",
  reporter: [
    ["list"],
    [
      "json",
      { outputFile: "test-results/u01e-agency-authorization/results.json" },
    ],
  ],
  globalTeardown:
    require.resolve("./e2e/u01e-agency-authorization-global-teardown"),
  use: {
    baseURL: adminBase,
    actionTimeout: 20_000,
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
      command: "node e2e/u01e-agency-authorization-backend.mjs",
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
