import { randomBytes } from "node:crypto";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

const apiPort = 18190;
const adminPort = 13190;
const apiBase = `http://127.0.0.1:${apiPort}`;
const adminBase = `http://127.0.0.1:${adminPort}`;
const databaseName =
  process.env.YIMATONG_U01H_DB ||
  `yimatong_acceptance_u01h_${Date.now().toString(36)}_${process.pid}`;
const ownerToken =
  process.env.YIMATONG_U01H_OWNER_TOKEN || randomBytes(16).toString("hex");
const controlRole = `u01h_control_${ownerToken.slice(0, 12)}`;
const leaseFile = path.resolve(
  process.cwd(),
  "e2e",
  ".auth",
  `u01h-api-credentials-${ownerToken}.json`
);

process.env.YIMATONG_U01H_DB = databaseName;
process.env.YIMATONG_U01H_OWNER_TOKEN = ownerToken;
process.env.YIMATONG_U01H_LEASE_FILE = leaseFile;
process.env.YIMATONG_U01H_CONTROL_ROLE = controlRole;
process.env.YIMATONG_U01H_API_PORT = String(apiPort);
process.env.YIMATONG_U01H_API_BASE = apiBase;
process.env.YIMATONG_U01H_ADMIN_ORIGIN = adminBase;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "u01h-api-credentials.spec.ts",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  workers: 1,
  outputDir: "test-results/u01h-api-credentials",
  reporter: [
    ["list"],
    ["json", { outputFile: "test-results/u01h-api-credentials/results.json" }],
  ],
  globalTeardown: require.resolve("./e2e/u01h-api-credentials-global-teardown"),
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
      command: "node e2e/u01h-api-credentials-backend.mjs",
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
