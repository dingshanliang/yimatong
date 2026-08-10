import { spawn } from "node:child_process";
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

const databaseName = process.env.YIMATONG_U01H_DB;
const ownerToken = process.env.YIMATONG_U01H_OWNER_TOKEN;
const leaseFile = process.env.YIMATONG_U01H_LEASE_FILE;
const controlRole = process.env.YIMATONG_U01H_CONTROL_ROLE;
const apiPort = process.env.YIMATONG_U01H_API_PORT || "18190";
const adminOrigin =
  process.env.YIMATONG_U01H_ADMIN_ORIGIN || "http://127.0.0.1:13190";

if (!databaseName || !ownerToken || !leaseFile || !controlRole) {
  throw new Error(
    "U01H database name, owner token, lease file, and control role are required"
  );
}
if (!/^u01h_control_[a-f0-9]{12}$/.test(controlRole)) {
  throw new Error("Refusing to use an invalid U01H control role");
}

const ownerDatabaseUrl = `postgresql+asyncpg://yimatong:yimatong@127.0.0.1:5433/${databaseName}`;
const runtimeDatabaseUrl = `postgresql+asyncpg://yimatong_app:yimatong_app@127.0.0.1:5433/${databaseName}`;
const controlDatabaseUrl = `postgresql+asyncpg://${controlRole}:u01h_control_pwd@127.0.0.1:5433/${databaseName}`;
const backendEnvironment = {
  ...process.env,
  environment: "development",
  database_url: runtimeDatabaseUrl,
  migration_database_url: ownerDatabaseUrl,
  control_database_url: controlDatabaseUrl,
  redis_url: "redis://127.0.0.1:6380/15",
  secret_key: "u01h-browser-secret-key-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
  AES_MASTER_KEY_V1: "91".repeat(32),
  HMAC_PEPPER: "a2".repeat(32),
  IP_HASH_SECRET:
    "u01h-browser-ip-hash-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
  base_url: `http://127.0.0.1:${apiPort}`,
  admin_public_url: adminOrigin,
  platform_public_url: "http://127.0.0.1:13192",
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
    `CREATE ROLE "${controlRole}" LOGIN PASSWORD 'u01h_control_pwd'
       NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
     GRANT CONNECT ON DATABASE "${databaseName}" TO "${controlRole}";`
  );
  controlRoleCreated = true;
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
            'viewer.u01h@demo.com',
            '$2b$12$9NDxiHGDV3rKjpJhahj/cOLr6NSSPIu2SkmA27McGjTh4.N3MCE4i',
            'U01H 只读观察员', true, 0, false, 0, now(), now()
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
             AND accounts.email = 'viewer.u01h@demo.com'
        );
     INSERT INTO account_roles (account_id, role_id)
     SELECT accounts.id, roles.id
       FROM accounts
       JOIN roles ON roles.tenant_id = accounts.tenant_id AND roles.name = 'viewer'
      WHERE accounts.email = 'viewer.u01h@demo.com'
     ON CONFLICT DO NOTHING;
     UPDATE tenants
        SET status = 'active', plan_expires_at = now() + interval '365 days'
      WHERE slug = 'demo';
     DO $$
     BEGIN
       IF NOT EXISTS (
         SELECT 1
           FROM accounts
           JOIN account_roles ON account_roles.account_id = accounts.id
           JOIN roles ON roles.id = account_roles.role_id
          WHERE accounts.email = 'viewer.u01h@demo.com'
            AND roles.name = 'viewer'
       ) THEN
         RAISE EXCEPTION 'U01H viewer seed is incomplete';
       END IF;
       IF NOT EXISTS (
         SELECT 1 FROM tenants
          WHERE slug = 'demo' AND tenant_type = 'brand' AND status = 'active'
       ) THEN
         RAISE EXCEPTION 'U01H active brand seed is incomplete';
       END IF;
       IF NOT EXISTS (
         SELECT 1
           FROM accounts
           JOIN account_roles ON account_roles.account_id = accounts.id
           JOIN roles ON roles.id = account_roles.role_id
          WHERE accounts.email = 'admin@demo.com' AND roles.name = 'admin'
       ) OR NOT EXISTS (
         SELECT 1
           FROM accounts
           JOIN account_roles ON account_roles.account_id = accounts.id
           JOIN roles ON roles.id = account_roles.role_id
          WHERE accounts.email = 'ops@demo.com' AND roles.name = 'operator'
       ) OR NOT EXISTS (
         SELECT 1
           FROM accounts
           JOIN tenants ON tenants.id = accounts.tenant_id
           JOIN account_roles ON account_roles.account_id = accounts.id
           JOIN roles ON roles.id = account_roles.role_id
          WHERE accounts.email = 'agency_admin@demo.com'
            AND tenants.tenant_type = 'agency'
            AND roles.name = 'admin'
       ) THEN
         RAISE EXCEPTION 'U01H official demo identities are incomplete';
       END IF;
     END $$;`
  );
}

let backendProcess;
let cleaningUp = false;
let lease;
let controlRoleCreated = false;

async function cleanup(exitCode) {
  if (cleaningUp) return;
  cleaningUp = true;
  if (backendProcess && backendProcess.exitCode === null) {
    backendProcess.kill("SIGTERM");
  }
  try {
    if (lease) {
      const removed = await removeOwnedDatabase({
        composeFile,
        leaseFile,
        lease,
      });
      console.log(
        `[u01h] ${removed ? "Removed" : "Confirmed absent"} owned database ` +
          `${databaseName}; cluster=${lease.clusterSystemIdentifier}; marker=${lease.marker}`
      );
    }
    if (controlRoleCreated) {
      await removeControlRole();
      console.log(`[u01h] Removed per-run control role ${controlRole}`);
    }
  } catch (error) {
    console.error(`[u01h] Cleanup failed for ${databaseName}`, error);
    process.exitCode = 1;
  }
  process.exit(exitCode);
}

process.once("SIGINT", () => void cleanup(130));
process.once("SIGTERM", () => void cleanup(0));

try {
  await prepareDatabase();
  console.log(
    `[u01h] Prepared dedicated external API credential database ${databaseName}`
  );
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
    console.error("[u01h] Backend failed to start", error);
    void cleanup(1);
  });
  backendProcess.once("exit", (code, signal) => {
    if (!cleaningUp) {
      console.error(
        `[u01h] Backend exited unexpectedly with ${code ?? signal}`
      );
      void cleanup(code || 1);
    }
  });
} catch (error) {
  console.error("[u01h] Browser backend setup failed", error);
  await cleanup(1);
}
