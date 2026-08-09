import { mkdir, writeFile } from "fs/promises";
import path from "path";

import {
  apiPost,
  platformLogin,
  platformPost,
  waitForBackend,
} from "./global-setup";

const TEST_PASSWORD = "E2ETest1234";

export default async function platformGlobalSetup() {
  await waitForBackend();
  const platformSession = await platformLogin();
  const runId = Date.now();
  const email = `e2e-platform-tenant-${runId}@example.com`;
  const tenant = await platformPost(
    "/api/v1/platform/tenants",
    {
      name: `Platform E2E ${runId}`,
      plan: "free",
      admin_email: email,
      admin_name: "Platform E2E Admin",
    },
    platformSession,
    { "Idempotency-Key": `e2e-platform-opening-${runId}` }
  );

  const activationUrl = tenant.activation_url as string;
  const accountId = tenant.initial_admin_id as string;
  const activationToken = new URL(activationUrl).searchParams.get("token");
  if (!activationToken || !accountId) {
    throw new Error("Platform opening did not return an activation credential");
  }
  await apiPost("/api/v1/auth/confirm-reset-password", {
    token: activationToken,
    account_id: accountId,
    new_password: TEST_PASSWORD,
  });
  const tenantLogin = await apiPost("/api/v1/auth/login", {
    email,
    password: TEST_PASSWORD,
  });
  const token = tenantLogin.access_token as string;
  if (!token) throw new Error("Tenant login did not return an access token");

  const authDir = path.join(__dirname, ".auth");
  await mkdir(authDir, { recursive: true });
  await writeFile(
    path.join(authDir, "context.json"),
    JSON.stringify({ token, tenantId: tenant.id, email }, null, 2)
  );
}
