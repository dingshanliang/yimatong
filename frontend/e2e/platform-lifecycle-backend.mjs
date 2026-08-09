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

const databaseName = process.env.YIMATONG_LIFECYCLE_DB;
const ownerToken = process.env.YIMATONG_LIFECYCLE_OWNER_TOKEN;
const leaseFile = process.env.YIMATONG_LIFECYCLE_LEASE_FILE;
const apiPort = process.env.YIMATONG_LIFECYCLE_API_PORT || "18100";
const adminOrigin =
  process.env.YIMATONG_LIFECYCLE_ADMIN_ORIGIN || "http://127.0.0.1:13100";
const platformOrigin =
  process.env.YIMATONG_LIFECYCLE_PLATFORM_ORIGIN || "http://127.0.0.1:13102";

if (!databaseName || !ownerToken || !leaseFile) {
  throw new Error(
    "Lifecycle database name, owner token, and lease file are required"
  );
}

const ownerDatabaseUrl = `postgresql+asyncpg://yimatong:yimatong@127.0.0.1:5433/${databaseName}`;
const runtimeDatabaseUrl = `postgresql+asyncpg://yimatong_app:yimatong_app@127.0.0.1:5433/${databaseName}`;
const controlDatabaseUrl = `postgresql+asyncpg://acceptance_control:control_pwd@127.0.0.1:5433/${databaseName}`;
const backendEnvironment = {
  ...process.env,
  environment: "development",
  database_url: runtimeDatabaseUrl,
  migration_database_url: ownerDatabaseUrl,
  control_database_url: controlDatabaseUrl,
  redis_url: "redis://127.0.0.1:6380/13",
  secret_key: "u01c-browser-secret-key-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
  AES_MASTER_KEY_V1: "11".repeat(32),
  HMAC_PEPPER: "22".repeat(32),
  IP_HASH_SECRET:
    "u01c-browser-ip-hash-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
  base_url: `http://127.0.0.1:${apiPort}`,
  admin_public_url: adminOrigin,
  platform_public_url: platformOrigin,
  cors_origins: `${adminOrigin},${platformOrigin}`,
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
      else
        reject(
          new Error(
            `${command} ${args.join(" ")} exited with ${code ?? signal}`
          )
        );
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
    `DO $$
     BEGIN
       IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'acceptance_control') THEN
         CREATE ROLE acceptance_control LOGIN PASSWORD 'control_pwd'
           NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
       ELSE
         ALTER ROLE acceptance_control WITH LOGIN PASSWORD 'control_pwd'
           NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
       END IF;
     END $$;
     GRANT CONNECT ON DATABASE \"${databaseName}\" TO acceptance_control;`
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
    `GRANT USAGE ON SCHEMA public TO acceptance_control;
     GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO acceptance_control;
     GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO acceptance_control;
     GRANT SET ON PARAMETER \"app.bypass_rls\" TO acceptance_control;`
  );
}

let backendProcess;
let cleaningUp = false;
let lease;

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
        `[u01c] ${removed ? "Removed" : "Confirmed absent"} owned database ${databaseName}`
      );
    }
  } catch (error) {
    console.error(`[u01c] Failed to remove ${databaseName}`, error);
    process.exitCode = 1;
  }
  process.exit(exitCode);
}

process.once("SIGINT", () => void cleanup(130));
process.once("SIGTERM", () => void cleanup(0));

try {
  await prepareDatabase();
  console.log(`[u01c] Prepared dedicated database ${databaseName}`);
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
    console.error("[u01c] Backend failed to start", error);
    void cleanup(1);
  });
  backendProcess.once("exit", (code, signal) => {
    if (!cleaningUp) {
      console.error(
        `[u01c] Backend exited unexpectedly with ${code ?? signal}`
      );
      void cleanup(code || 1);
    }
  });
} catch (error) {
  console.error("[u01c] Browser backend setup failed", error);
  await cleanup(1);
}
