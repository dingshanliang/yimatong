import { randomBytes } from "node:crypto";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

const apiPort = 18220;
const adminPort = 13220;
const apiBase = `http://127.0.0.1:${apiPort}`;
const adminBase = `http://127.0.0.1:${adminPort}`;
const databaseName =
  process.env.YIMATONG_U02A_DB ||
  `yimatong_acceptance_u02a_${Date.now().toString(36)}_${process.pid}`;
const ownerToken =
  process.env.YIMATONG_U02A_OWNER_TOKEN || randomBytes(16).toString("hex");
const controlRole = `u02a_control_${ownerToken.slice(0, 12)}`;
const leaseFile = path.resolve(
  process.cwd(),
  "e2e",
  ".auth",
  `u02a-catalog-${ownerToken}.json`
);

process.env.YIMATONG_U02A_DB = databaseName;
process.env.YIMATONG_U02A_OWNER_TOKEN = ownerToken;
process.env.YIMATONG_U02A_LEASE_FILE = leaseFile;
process.env.YIMATONG_U02A_CONTROL_ROLE = controlRole;
process.env.YIMATONG_U02A_API_PORT = String(apiPort);
process.env.YIMATONG_U02A_API_BASE = apiBase;
process.env.YIMATONG_U02A_ADMIN_ORIGIN = adminBase;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "u02a-catalog.spec.ts",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  workers: 1,
  outputDir: "test-results/u02a-catalog",
  reporter: [
    ["list"],
    ["json", { outputFile: "test-results/u02a-catalog/results.json" }],
  ],
  globalTeardown: require.resolve("./e2e/u02a-catalog-global-teardown"),
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
      command: "node e2e/u02a-catalog-backend.mjs",
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
