import path from "node:path";
import {
  readLease,
  removeOwnedDatabase,
} from "./platform-lifecycle-database.mjs";

export default async function platformLifecycleGlobalTeardown() {
  const leaseFile = process.env.YIMATONG_LIFECYCLE_LEASE_FILE;
  if (!leaseFile) {
    throw new Error("Refusing cleanup without a per-run lease file");
  }

  const composeFile = path.resolve(
    __dirname,
    "..",
    "..",
    "docker-compose.infra.yml"
  );
  const lease = await readLease(leaseFile);
  const removed = await removeOwnedDatabase({ composeFile, leaseFile, lease });
  console.log(
    `[u01c] Teardown ${removed ? "removed" : "confirmed absent"} owned database ${lease.databaseName}`
  );
}
