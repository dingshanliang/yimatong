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

const databaseName = process.env.YIMATONG_U01E_DB;
const ownerToken = process.env.YIMATONG_U01E_OWNER_TOKEN;
const leaseFile = process.env.YIMATONG_U01E_LEASE_FILE;
const controlRole = process.env.YIMATONG_U01E_CONTROL_ROLE;
const apiPort = process.env.YIMATONG_U01E_API_PORT || "18180";
const adminOrigin =
  process.env.YIMATONG_U01E_ADMIN_ORIGIN || "http://127.0.0.1:13180";

if (!databaseName || !ownerToken || !leaseFile || !controlRole) {
  throw new Error(
    "U01E database name, owner token, lease file, and control role are required"
  );
}
if (!/^u01e_control_[a-f0-9]{12}$/.test(controlRole)) {
  throw new Error("Refusing to use an invalid U01E control role");
}

const ownerDatabaseUrl = `postgresql+asyncpg://yimatong:yimatong@127.0.0.1:5433/${databaseName}`;
const runtimeDatabaseUrl = `postgresql+asyncpg://yimatong_app:yimatong_app@127.0.0.1:5433/${databaseName}`;
const controlDatabaseUrl = `postgresql+asyncpg://${controlRole}:u01e_control_pwd@127.0.0.1:5433/${databaseName}`;
const backendEnvironment = {
  ...process.env,
  environment: "development",
  database_url: runtimeDatabaseUrl,
  migration_database_url: ownerDatabaseUrl,
  control_database_url: controlDatabaseUrl,
  redis_url: "redis://127.0.0.1:6380/12",
  secret_key: "u01e-browser-secret-key-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
  AES_MASTER_KEY_V1: "71".repeat(32),
  HMAC_PEPPER: "82".repeat(32),
  IP_HASH_SECRET:
    "u01e-browser-ip-hash-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
  base_url: `http://127.0.0.1:${apiPort}`,
  admin_public_url: adminOrigin,
  platform_public_url: "http://127.0.0.1:13182",
  cors_origins: adminOrigin,
  platform_admin_email: "platform@yimatong.cn",
  platform_admin_password_hash:
    "$2b$12$SerdnBjOttEIIry2900ELOiJYuZb.tVykMjV3fV6T2SIIHOOAkMre",
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
    `CREATE ROLE "${controlRole}" LOGIN PASSWORD 'u01e_control_pwd'
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

  // The product journey must grant access through the real brand UI. Remove
  // only the demo seed's relationship before runtime starts; the database is
  // dedicated to this run and ownership is proven by its lease marker.
  await composePsql(
    databaseName,
    `DELETE FROM agency_authorizations
      WHERE agency_tenant_id = (SELECT id FROM tenants WHERE slug = 'demo-agency')
        AND client_tenant_id = (SELECT id FROM tenants WHERE slug = 'demo');
     DO $$
     BEGIN
       IF NOT EXISTS (
         SELECT 1 FROM tenants
          WHERE slug = 'demo' AND tenant_type = 'brand' AND status = 'active'
       ) THEN
         RAISE EXCEPTION 'U01E active demo brand is missing';
       END IF;
       IF NOT EXISTS (
         SELECT 1 FROM tenants
          WHERE slug = 'demo-agency' AND tenant_type = 'agency' AND status = 'active'
       ) THEN
         RAISE EXCEPTION 'U01E active demo agency is missing';
       END IF;
       IF EXISTS (
         SELECT 1 FROM agency_authorizations
          WHERE agency_tenant_id = (SELECT id FROM tenants WHERE slug = 'demo-agency')
            AND client_tenant_id = (SELECT id FROM tenants WHERE slug = 'demo')
            AND status = 'active'
       ) THEN
         RAISE EXCEPTION 'U01E fixture must start without an active authorization';
       END IF;
       IF NOT EXISTS (
         SELECT 1
           FROM accounts
           JOIN tenants ON tenants.id = accounts.tenant_id
           JOIN account_roles ON account_roles.account_id = accounts.id
           JOIN roles ON roles.id = account_roles.role_id
          WHERE tenants.slug = 'demo-agency'
            AND accounts.email = 'agency_admin@demo.com'
            AND roles.name = 'admin'
       ) THEN
         RAISE EXCEPTION 'U01E agency administrator seed is incomplete';
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
        `[u01e] ${removed ? "Removed" : "Confirmed absent"} owned database ${databaseName}; cluster=${lease.clusterSystemIdentifier}; marker=${lease.marker}`
      );
    }
    if (controlRoleCreated) {
      await removeControlRole();
      console.log(`[u01e] Removed per-run control role ${controlRole}`);
    }
  } catch (error) {
    console.error(`[u01e] Cleanup failed for ${databaseName}`, error);
    process.exitCode = 1;
  }
  process.exit(exitCode);
}

process.once("SIGINT", () => void cleanup(130));
process.once("SIGTERM", () => void cleanup(0));

try {
  await prepareDatabase();
  console.log(
    `[u01e] Prepared dedicated agency-authorization database ${databaseName}`
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
    console.error("[u01e] Backend failed to start", error);
    void cleanup(1);
  });
  backendProcess.once("exit", (code, signal) => {
    if (!cleaningUp) {
      console.error(
        `[u01e] Backend exited unexpectedly with ${code ?? signal}`
      );
      void cleanup(code || 1);
    }
  });
} catch (error) {
  console.error("[u01e] Browser backend setup failed", error);
  await cleanup(1);
}
