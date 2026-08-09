import { execFile } from "node:child_process";
import { mkdir, readFile, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const DATABASE_PATTERN = /^yimatong_acceptance_[a-z0-9][a-z0-9_]{5,42}$/;
const OWNER_TOKEN_PATTERN = /^[a-f0-9]{32}$/;
const MARKER_PREFIX = "yimatong-acceptance-owner:";

function assertLease(lease) {
  if (!lease || !DATABASE_PATTERN.test(lease.databaseName)) {
    throw new Error("Refusing to operate on a non-acceptance database");
  }
  if (!OWNER_TOKEN_PATTERN.test(lease.ownerToken)) {
    throw new Error("Refusing to operate without a valid per-run owner token");
  }
  if (lease.marker !== `${MARKER_PREFIX}${lease.ownerToken}`) {
    throw new Error("Acceptance database ownership marker is inconsistent");
  }
}

async function psql(composeFile, database, sql) {
  const { stdout } = await execFileAsync(
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
      "-tA",
      "-U",
      "yimatong",
      "-d",
      database,
      "-c",
      sql,
    ],
    { maxBuffer: 1024 * 1024 }
  );
  return stdout.trim();
}

async function databaseState(composeFile, databaseName) {
  const raw = await psql(
    composeFile,
    "postgres",
    `SELECT json_build_object(
       'exists', EXISTS (SELECT 1 FROM pg_database WHERE datname = '${databaseName}'),
       'marker', COALESCE((SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = '${databaseName}'), '')
     )::text;`
  );
  return JSON.parse(raw);
}

export async function writeLease(leaseFile, lease) {
  assertLease(lease);
  await mkdir(path.dirname(leaseFile), { recursive: true });
  await writeFile(leaseFile, `${JSON.stringify(lease, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
}

export async function readLease(leaseFile) {
  const lease = JSON.parse(await readFile(leaseFile, "utf8"));
  assertLease(lease);
  return lease;
}

export async function createOwnedDatabase({
  composeFile,
  databaseName,
  ownerToken,
  leaseFile,
  onLease = () => {},
}) {
  const lease = {
    databaseName,
    ownerToken,
    marker: `${MARKER_PREFIX}${ownerToken}`,
    clusterSystemIdentifier: await psql(
      composeFile,
      "postgres",
      "SELECT system_identifier::text FROM pg_control_system();"
    ),
    created: false,
    markerWritten: false,
  };
  assertLease(lease);

  const existing = await databaseState(composeFile, databaseName);
  if (existing.exists) {
    throw new Error(
      `Refusing to replace pre-existing database ${databaseName}; marker=${existing.marker || "<none>"}`
    );
  }

  await psql(
    composeFile,
    "postgres",
    `CREATE DATABASE "${databaseName}" OWNER yimatong;`
  );
  lease.created = true;
  onLease(lease);
  await writeLease(leaseFile, lease);

  await psql(
    composeFile,
    "postgres",
    `COMMENT ON DATABASE "${databaseName}" IS '${lease.marker}';`
  );
  lease.markerWritten = true;
  onLease(lease);
  await writeLease(leaseFile, lease);

  const targetIdentity = await psql(
    composeFile,
    databaseName,
    "SELECT current_database() || ':' || system_identifier::text FROM pg_control_system();"
  );
  if (targetIdentity !== `${databaseName}:${lease.clusterSystemIdentifier}`) {
    throw new Error(
      `Acceptance database endpoint mismatch: ${targetIdentity || "<empty>"}`
    );
  }
  return lease;
}

export async function removeOwnedDatabase({
  composeFile,
  leaseFile,
  lease: suppliedLease,
}) {
  const lease = suppliedLease || (await readLease(leaseFile));
  assertLease(lease);
  if (!lease.created) {
    throw new Error("Refusing cleanup for a database this run did not create");
  }

  const currentCluster = await psql(
    composeFile,
    "postgres",
    "SELECT system_identifier::text FROM pg_control_system();"
  );
  if (currentCluster !== lease.clusterSystemIdentifier) {
    throw new Error("Refusing cleanup on a different PostgreSQL cluster");
  }

  const current = await databaseState(composeFile, lease.databaseName);
  if (!current.exists) {
    await unlink(leaseFile).catch((error) => {
      if (error.code !== "ENOENT") throw error;
    });
    return false;
  }
  const markerMatchesCompletedLease =
    lease.markerWritten && current.marker === lease.marker;
  const isThisRunsUnmarkedCreate =
    !lease.markerWritten && current.marker === "";
  if (!markerMatchesCompletedLease && !isThisRunsUnmarkedCreate) {
    throw new Error(
      `Refusing cleanup because ownership marker changed: ${current.marker || "<none>"}`
    );
  }

  await psql(
    composeFile,
    "postgres",
    `DROP DATABASE "${lease.databaseName}" WITH (FORCE);`
  );
  const afterDrop = await databaseState(composeFile, lease.databaseName);
  if (afterDrop.exists) {
    throw new Error(
      `Database ${lease.databaseName} still exists after cleanup`
    );
  }
  await unlink(leaseFile).catch((error) => {
    if (error.code !== "ENOENT") throw error;
  });
  return true;
}
