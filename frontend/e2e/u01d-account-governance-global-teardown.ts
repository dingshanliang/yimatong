import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import {
  readLease,
  removeOwnedDatabase,
} from "./platform-lifecycle-database.mjs";

const execFileAsync = promisify(execFile);

export default async function u01dAccountGovernanceGlobalTeardown() {
  const leaseFile = process.env.YIMATONG_U01D_LEASE_FILE;
  const controlRole = process.env.YIMATONG_U01D_CONTROL_ROLE;
  if (
    !leaseFile ||
    !controlRole ||
    !/^u01d_control_[a-f0-9]{12}$/.test(controlRole)
  ) {
    throw new Error(
      "Refusing U01D cleanup without a valid lease and control role"
    );
  }

  const composeFile = path.resolve(
    __dirname,
    "..",
    "..",
    "docker-compose.infra.yml"
  );
  const lease = await readLease(leaseFile);
  const removed = await removeOwnedDatabase({ composeFile, leaseFile, lease });
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
      "postgres",
      "-c",
      `REVOKE SET ON PARAMETER "app.bypass_rls" FROM "${controlRole}";
       DROP ROLE IF EXISTS "${controlRole}";`,
    ],
    { maxBuffer: 1024 * 1024 }
  );
  console.log(
    `[u01d] Teardown ${removed ? "removed" : "confirmed absent"} owned database ${lease.databaseName}; cluster=${lease.clusterSystemIdentifier}; marker=${lease.marker}; control_role=${controlRole}`
  );
}
