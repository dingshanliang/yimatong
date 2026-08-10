import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import {
  readLease,
  removeOwnedDatabase,
} from "./platform-lifecycle-database.mjs";

const execFileAsync = promisify(execFile);

interface U02aLease {
  databaseName: string;
  clusterSystemIdentifier: string;
  marker: string;
}

interface CleanupDependencies {
  readLease: () => Promise<U02aLease>;
  removeDatabase: (lease: U02aLease) => Promise<boolean>;
  recoverTargetDatabase: (lease: U02aLease) => Promise<void>;
  removeControlRole: () => Promise<void>;
  onRecovered?: (firstError: unknown) => void;
}

async function removeDatabaseWithRecovery(
  lease: U02aLease,
  dependencies: CleanupDependencies
): Promise<boolean> {
  try {
    return await dependencies.removeDatabase(lease);
  } catch (firstError) {
    let dependencyError: unknown;
    try {
      await dependencies.recoverTargetDatabase(lease);
    } catch (error) {
      dependencyError = error;
    }

    try {
      const removed = await dependencies.removeDatabase(lease);
      dependencies.onRecovered?.(firstError);
      return removed;
    } catch (retryError) {
      throw new AggregateError(
        [firstError, ...(dependencyError ? [dependencyError] : []), retryError],
        `U02A owned database cleanup failed for ${lease.databaseName}`
      );
    }
  }
}

export async function cleanupU02aResources(
  dependencies: CleanupDependencies
): Promise<{ lease: U02aLease | null; removed: boolean | null }> {
  const errors: unknown[] = [];
  let lease: U02aLease | null = null;
  let removed: boolean | null = null;

  try {
    lease = await dependencies.readLease();
    removed = await removeDatabaseWithRecovery(lease, dependencies);
  } catch (error) {
    errors.push(error);
  }

  try {
    await dependencies.removeControlRole();
  } catch (error) {
    errors.push(error);
  }

  if (errors.length > 0) {
    throw new AggregateError(errors, "U02A resource cleanup was incomplete");
  }
  return { lease, removed };
}

async function runPsql(composeFile: string, database: string, sql: string) {
  await execFileAsync(
    "docker",
    [
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
      "-c",
      sql,
    ],
    { maxBuffer: 1024 * 1024 }
  );
}

export default async function u02aCatalogGlobalTeardown() {
  const leaseFile = process.env.YIMATONG_U02A_LEASE_FILE;
  const controlRole = process.env.YIMATONG_U02A_CONTROL_ROLE;
  const composeFile = path.resolve(
    __dirname,
    "..",
    "..",
    "docker-compose.infra.yml"
  );

  const validRole =
    typeof controlRole === "string" &&
    /^u02a_control_[a-f0-9]{12}$/.test(controlRole);
  const result = await cleanupU02aResources({
    readLease: async () => {
      if (!leaseFile) {
        throw new Error("Refusing U02A database cleanup without a lease file");
      }
      return (await readLease(leaseFile)) as U02aLease;
    },
    removeDatabase: async (lease) => {
      if (!leaseFile) {
        throw new Error("Refusing U02A database cleanup without a lease file");
      }
      return removeOwnedDatabase({ composeFile, leaseFile, lease });
    },
    recoverTargetDatabase: async (lease) => {
      if (!validRole) {
        throw new Error(
          "Refusing U02A target cleanup without a valid control role"
        );
      }
      await runPsql(
        composeFile,
        lease.databaseName,
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
    },
    removeControlRole: async () => {
      if (!validRole) {
        throw new Error(
          "Refusing U02A role cleanup without a valid control role"
        );
      }
      await runPsql(
        composeFile,
        "postgres",
        `DO $$
         BEGIN
           IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${controlRole}') THEN
             EXECUTE 'REVOKE SET ON PARAMETER "app.bypass_rls" FROM "${controlRole}"';
             EXECUTE 'DROP ROLE "${controlRole}"';
           END IF;
         END $$;`
      );
    },
    onRecovered: (firstError) => {
      console.warn(
        `[u02a] Recovered owned database cleanup after the first attempt failed: ${firstError}`
      );
    },
  });

  if (!result.lease) {
    throw new Error("U02A cleanup completed without a validated lease");
  }
  console.log(
    `[u02a] Teardown ${result.removed ? "removed" : "confirmed absent"} owned database ` +
      `${result.lease.databaseName}; cluster=${result.lease.clusterSystemIdentifier}; ` +
      `marker=${result.lease.marker}; control_role=${controlRole}`
  );
}
