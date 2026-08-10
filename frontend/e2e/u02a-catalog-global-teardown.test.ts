import { describe, expect, it } from "vitest";

import { cleanupU02aResources } from "./u02a-catalog-global-teardown";

const lease = {
  databaseName: "yimatong_acceptance_u02a_fault_test",
  clusterSystemIdentifier: "test-cluster",
  marker: "yimatong-acceptance-owner:test",
};

describe("U02A catalog teardown", () => {
  it("still attempts role cleanup after database cleanup and recovery fail", async () => {
    const calls: string[] = [];
    let removeAttempt = 0;

    const error = await cleanupU02aResources({
      readLease: async () => lease,
      removeDatabase: async () => {
        removeAttempt += 1;
        calls.push(`remove-database-${removeAttempt}`);
        throw new Error(`database failure ${removeAttempt}`);
      },
      recoverTargetDatabase: async () => {
        calls.push("recover-target-database");
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

  it("still attempts role cleanup when the lease cannot be read", async () => {
    const calls: string[] = [];

    await expect(
      cleanupU02aResources({
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
