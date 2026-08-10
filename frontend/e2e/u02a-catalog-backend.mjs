import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  createOwnedDatabase,
  removeOwnedDatabase,
} from "./platform-lifecycle-database.mjs";

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(currentDir, "..");
const repoRoot = path.resolve(frontendDir, "..");
const backendDir = path.join(repoRoot, "backend");
const composeFile = path.join(repoRoot, "docker-compose.infra.yml");

const databaseName = process.env.YIMATONG_U02A_DB;
const ownerToken = process.env.YIMATONG_U02A_OWNER_TOKEN;
const leaseFile = process.env.YIMATONG_U02A_LEASE_FILE;
const controlRole = process.env.YIMATONG_U02A_CONTROL_ROLE;
const controlRolePassword = randomBytes(32).toString("hex");
const apiPort = process.env.YIMATONG_U02A_API_PORT || "18220";
const adminOrigin =
  process.env.YIMATONG_U02A_ADMIN_ORIGIN || "http://127.0.0.1:13220";

if (!databaseName || !ownerToken || !leaseFile || !controlRole) {
  throw new Error(
    "U02A database name, owner token, lease file, and control role are required"
  );
}
if (!/^u02a_control_[a-f0-9]{12}$/.test(controlRole)) {
  throw new Error("Refusing to use an invalid U02A control role");
}

const ownerDatabaseUrl = `postgresql+asyncpg://yimatong:yimatong@127.0.0.1:5433/${databaseName}`;
const runtimeDatabaseUrl = `postgresql+asyncpg://yimatong_app:yimatong_app@127.0.0.1:5433/${databaseName}`;
const controlDatabaseUrl = `postgresql+asyncpg://${controlRole}:${controlRolePassword}@127.0.0.1:5433/${databaseName}`;
const backendEnvironment = {
  ...process.env,
  environment: "development",
  database_url: runtimeDatabaseUrl,
  migration_database_url: ownerDatabaseUrl,
  control_database_url: controlDatabaseUrl,
  redis_url: "redis://127.0.0.1:6380/10",
  secret_key: "u02a-browser-secret-key-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
  AES_MASTER_KEY_V1: "b2".repeat(32),
  HMAC_PEPPER: "c3".repeat(32),
  IP_HASH_SECRET:
    "u02a-browser-ip-hash-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
  base_url: `http://127.0.0.1:${apiPort}`,
  admin_public_url: adminOrigin,
  platform_public_url: "http://127.0.0.1:13222",
  cors_origins: adminOrigin,
  cookie_secure: "false",
  cookie_samesite: "lax",
};

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd || repoRoot,
      env: options.env || process.env,
      stdio: options.input ? ["pipe", "inherit", "inherit"] : "inherit",
    });
    if (options.input) child.stdin.end(options.input);
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) resolve();
      else {
        reject(
          new Error(
            `${command} ${args.join(" ")} exited with ${code ?? signal}`
          )
        );
      }
    });
  });
}

function composePsql(database, sql, input) {
  const args = [
    "compose",
    "-f",
    composeFile,
    "exec",
    "-T",
    "postgres",
    "psql",
    "-X",
    "-v",
    "ON_ERROR_STOP=1",
    "-U",
    "yimatong",
    "-d",
    database,
  ];
  if (sql) args.push("-c", sql);
  return run("docker", args, input ? { input } : undefined);
}

async function removeControlRole() {
  await composePsql(
    "postgres",
    `DO $$
     BEGIN
       IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${controlRole}') THEN
         EXECUTE 'REVOKE SET ON PARAMETER "app.bypass_rls" FROM "${controlRole}"';
         EXECUTE 'DROP ROLE "${controlRole}"';
       END IF;
     END $$;`
  );
}

async function revokeControlRoleInTargetDatabase() {
  await composePsql(
    databaseName,
    `DO $$
     BEGIN
       IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${controlRole}') THEN
         EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM "${controlRole}"';
         EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM "${controlRole}"';
         EXECUTE 'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM "${controlRole}"';
         EXECUTE 'REVOKE ALL PRIVILEGES ON SCHEMA public FROM "${controlRole}"';
         EXECUTE 'DROP OWNED BY "${controlRole}"';
       END IF;
     END $$;`
  );
}

async function removeDatabaseWithRecovery() {
  try {
    return await removeOwnedDatabase({ composeFile, leaseFile, lease });
  } catch (firstError) {
    let dependencyError;
    try {
      await revokeControlRoleInTargetDatabase();
    } catch (error) {
      dependencyError = error;
    }

    try {
      const removed = await removeOwnedDatabase({
        composeFile,
        leaseFile,
        lease,
      });
      console.warn(
        `[u02a] Recovered owned database cleanup after the first attempt failed: ${firstError}`
      );
      return removed;
    } catch (retryError) {
      throw new AggregateError(
        [firstError, ...(dependencyError ? [dependencyError] : []), retryError],
        `U02A owned database cleanup failed for ${databaseName}`
      );
    }
  }
}

async function prepareDatabase() {
  await createOwnedDatabase({
    composeFile,
    databaseName,
    ownerToken,
    leaseFile,
    onLease: (currentLease) => {
      lease = currentLease;
    },
  });
  await composePsql(
    "postgres",
    `CREATE ROLE "${controlRole}" LOGIN PASSWORD '${controlRolePassword}'
       NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
     GRANT CONNECT ON DATABASE "${databaseName}" TO "${controlRole}";`
  );
  await run("uv", ["run", "alembic", "upgrade", "head"], {
    cwd: backendDir,
    env: backendEnvironment,
  });
  const runtimeRoleSql = await readFile(
    path.join(backendDir, "scripts", "init_runtime_role.sql"),
    "utf8"
  );
  await composePsql(databaseName, undefined, runtimeRoleSql);
  await composePsql(
    databaseName,
    `GRANT USAGE ON SCHEMA public TO "${controlRole}";
     GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "${controlRole}";
     GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO "${controlRole}";
     GRANT SET ON PARAMETER "app.bypass_rls" TO "${controlRole}";`
  );
  await run(
    "uv",
    ["run", "python", "scripts/seed_demo.py", "generate", "--target", "demo"],
    { cwd: backendDir, env: backendEnvironment }
  );
  await composePsql(
    databaseName,
    `INSERT INTO accounts (
       id, tenant_id, organization_id, email, hashed_password, name,
       is_active, auth_version, must_change_password, failed_login_attempts,
       created_at, updated_at
     )
     SELECT gen_random_uuid(), tenants.id, organizations.id,
            'viewer.u02a@demo.com',
            '$2b$12$9NDxiHGDV3rKjpJhahj/cOLr6NSSPIu2SkmA27McGjTh4.N3MCE4i',
            'U02A 目录观察员', true, 0, false, 0, now(), now()
       FROM tenants
       JOIN LATERAL (
         SELECT id FROM organizations
          WHERE organizations.tenant_id = tenants.id
          ORDER BY created_at, id LIMIT 1
       ) organizations ON true
      WHERE tenants.slug = 'demo'
        AND NOT EXISTS (
          SELECT 1 FROM accounts
           WHERE accounts.tenant_id = tenants.id
             AND accounts.email = 'viewer.u02a@demo.com'
        );
     INSERT INTO account_roles (tenant_id, account_id, role_id)
     SELECT accounts.tenant_id, accounts.id, roles.id
       FROM accounts
       JOIN roles ON roles.tenant_id = accounts.tenant_id AND roles.name = 'viewer'
      WHERE accounts.email = 'viewer.u02a@demo.com'
     ON CONFLICT DO NOTHING;
     INSERT INTO brands (id, tenant_id, name, status, created_at, updated_at)
     SELECT '00000000-0000-7000-8000-0000000002a0'::uuid,
            tenants.id, 'U02A 外租户品牌', 'active', now(), now()
       FROM tenants
      WHERE tenants.slug = 'demo-agency'
     ON CONFLICT DO NOTHING;
     INSERT INTO products (
       id, tenant_id, brand_id, name, status, created_at, updated_at
     )
     SELECT '00000000-0000-7000-8000-0000000002a2'::uuid,
            tenants.id, '00000000-0000-7000-8000-0000000002a0'::uuid,
            'U02A 外租户产品', 'active', now(), now()
       FROM tenants
      WHERE tenants.slug = 'demo-agency'
     ON CONFLICT DO NOTHING;
     INSERT INTO skus (
       id, tenant_id, product_id, code, name, status, created_at, updated_at
     )
     SELECT '00000000-0000-7000-8000-0000000002a3'::uuid,
            tenants.id, '00000000-0000-7000-8000-0000000002a2'::uuid,
            'U02A-FOREIGN', 'U02A 外租户 SKU', 'active', now(), now()
       FROM tenants
      WHERE tenants.slug = 'demo-agency'
     ON CONFLICT DO NOTHING;
     DELETE FROM agency_authorizations
      WHERE agency_tenant_id = (SELECT id FROM tenants WHERE slug = 'demo-agency')
        AND client_tenant_id = (SELECT id FROM tenants WHERE slug = 'demo');
     INSERT INTO agency_authorizations (
       id, agency_tenant_id, client_tenant_id, scope, status, granted_by,
       granted_at, expires_at, created_at, updated_at
     )
     SELECT '00000000-0000-7000-8000-0000000002a1'::uuid,
            agency.id, client.id, '["products"]'::json,
            'active', admin.id, now(), now() + interval '30 days', now(), now()
       FROM tenants agency
       CROSS JOIN tenants client
       JOIN accounts admin ON admin.tenant_id = client.id
      WHERE agency.slug = 'demo-agency'
        AND client.slug = 'demo'
        AND admin.email = 'admin@demo.com';
     UPDATE tenants
        SET status = 'active', plan_expires_at = now() + interval '365 days'
      WHERE slug IN ('demo', 'demo-agency');
     DO $$
     BEGIN
       IF NOT EXISTS (
         SELECT 1
           FROM accounts
           JOIN account_roles ON account_roles.account_id = accounts.id
                              AND account_roles.tenant_id = accounts.tenant_id
           JOIN roles ON roles.id = account_roles.role_id
                     AND roles.tenant_id = account_roles.tenant_id
          WHERE accounts.email = 'viewer.u02a@demo.com'
            AND roles.name = 'viewer'
       ) THEN
         RAISE EXCEPTION 'U02A viewer seed is incomplete';
       END IF;
       IF NOT EXISTS (
         SELECT 1 FROM agency_authorizations
          WHERE agency_tenant_id = (SELECT id FROM tenants WHERE slug = 'demo-agency')
            AND client_tenant_id = (SELECT id FROM tenants WHERE slug = 'demo')
            AND status = 'active'
            AND scope::jsonb @> '["products"]'::jsonb
       ) THEN
         RAISE EXCEPTION 'U02A product-scoped agency authorization is missing';
       END IF;
       IF NOT EXISTS (
         SELECT 1
           FROM code_items
           JOIN code_batches
             ON code_batches.id = code_items.code_batch_id
            AND code_batches.tenant_id = code_items.tenant_id
          WHERE code_items.tenant_id = (SELECT id FROM tenants WHERE slug = 'demo')
            AND code_items.status = 'activated'
            AND code_batches.product_id IS NOT NULL
       ) THEN
         RAISE EXCEPTION 'U02A active public resolver fixture is missing';
       END IF;
     END $$;`
  );
}

let backendProcess;
let lease;
let cleaningUp = false;
let cleanupPromise;
let requestedExitCode = 0;

async function performCleanup() {
  cleaningUp = true;
  if (backendProcess && backendProcess.exitCode === null) {
    backendProcess.kill("SIGTERM");
  }

  const errors = [];
  try {
    if (lease) {
      const removed = await removeDatabaseWithRecovery();
      console.log(
        `[u02a] ${removed ? "Removed" : "Confirmed absent"} owned database ` +
          `${databaseName}; cluster=${lease.clusterSystemIdentifier}; marker=${lease.marker}`
      );
    }
  } catch (error) {
    errors.push(error);
  }

  try {
    await removeControlRole();
    console.log(
      `[u02a] Removed or confirmed absent control role ${controlRole}`
    );
  } catch (error) {
    errors.push(error);
  }

  if (errors.length > 0) {
    console.error(
      `[u02a] Cleanup failed for ${databaseName}`,
      new AggregateError(errors, "U02A resource cleanup was incomplete")
    );
    process.exitCode = 1;
  } else {
    process.exitCode = Math.max(process.exitCode || 0, requestedExitCode);
  }
}

function cleanup(exitCode) {
  requestedExitCode = Math.max(requestedExitCode, exitCode);
  cleanupPromise ||= performCleanup();
  return cleanupPromise;
}

process.once("SIGINT", () => void cleanup(130));
process.once("SIGTERM", () => void cleanup(0));

try {
  await prepareDatabase();
  console.log(`[u02a] Prepared dedicated catalog database ${databaseName}`);
  backendProcess = spawn(
    "uv",
    [
      "run",
      "uvicorn",
      "app.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      apiPort,
    ],
    { cwd: backendDir, env: backendEnvironment, stdio: "inherit" }
  );
  backendProcess.once("error", (error) => {
    console.error("[u02a] Backend failed to start", error);
    void cleanup(1);
  });
  backendProcess.once("exit", (code, signal) => {
    if (!cleaningUp) {
      console.error(
        `[u02a] Backend exited unexpectedly with ${code ?? signal}`
      );
      void cleanup(code || 1);
    }
  });
} catch (error) {
  console.error("[u02a] Browser backend setup failed", error);
  await cleanup(1);
}
