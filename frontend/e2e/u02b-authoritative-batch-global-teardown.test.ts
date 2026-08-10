import { describe, expect, it } from "vitest";

import { cleanupU02bResources } from "./u02b-authoritative-batch-global-teardown";

const lease = {
  databaseName: "yimatong_acceptance_u02b_fault_test",
  clusterSystemIdentifier: "test-cluster",
  marker: "yimatong-acceptance-owner:test",
};

describe("U02B authoritative batch teardown", () => {
  it("attempts role cleanup after database cleanup and recovery both fail", async () => {
    const calls: string[] = [];
    let removeAttempt = 0;

    const error = await cleanupU02bResources({
      readLease: async () => lease,
      removeDatabase: async () => {
        removeAttempt += 1;
        calls.push(`remove-database-${removeAttempt}`);
        throw new Error(`database failure ${removeAttempt}`);
      },
      recoverTargetDatabase: async () => {
        calls.push("recover-target-database");
        throw new Error("recovery failure");
      },
      removeControlRole: async () => {
        calls.push("remove-control-role");
        throw new Error("role failure");
      },
    })
      .then(() => null)
      .catch((cleanupError: unknown) => cleanupError);

    expect(error).toBeInstanceOf(AggregateError);
    expect((error as AggregateError).errors).toHaveLength(2);
    expect(calls).toEqual([
      "remove-database-1",
      "recover-target-database",
      "remove-database-2",
      "remove-control-role",
    ]);
  });

  it("attempts role cleanup even when the lease cannot be read", async () => {
    const calls: string[] = [];

    await expect(
      cleanupU02bResources({
        readLease: async () => {
          calls.push("read-lease");
          throw new Error("lease failure");
        },
        removeDatabase: async () => false,
        recoverTargetDatabase: async () => undefined,
        removeControlRole: async () => {
          calls.push("remove-control-role");
        },
      })
    ).rejects.toBeInstanceOf(AggregateError);

    expect(calls).toEqual(["read-lease", "remove-control-role"]);
  });
});
