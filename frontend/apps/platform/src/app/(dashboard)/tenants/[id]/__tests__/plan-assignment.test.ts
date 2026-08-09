import { describe, expect, it } from "vitest";

import {
  ACTIVE_PLAN_SOURCE,
  activePlanOptions,
  buildPlanAssignmentState,
  findActivePlan,
  type ActivePlanDefinition,
} from "../plan-assignment";

const plans: ActivePlanDefinition[] = [
  { id: "free", name: "free", display_name: "免费版", is_active: false },
  {
    id: "starter",
    name: "starter",
    display_name: "入门版",
    is_active: true,
  },
  {
    id: "legacy",
    name: "legacy",
    display_name: "旧套餐",
    is_active: true,
  },
];

describe("tenant plan assignment source", () => {
  it("uses the dedicated active-plan source and excludes inactive definitions", () => {
    expect(ACTIVE_PLAN_SOURCE).toBe("/platform/plans/active");
    expect(
      activePlanOptions(plans, new Set(["free", "starter", "pro"]))
    ).toEqual([{ value: "starter", label: "入门版" }]);
  });

  it("does not resolve an inactive current plan as assignable", () => {
    expect(findActivePlan(plans, "free")).toBeUndefined();
    expect(findActivePlan(plans, "starter")?.id).toBe("starter");
    expect(buildPlanAssignmentState(plans, "free")).toEqual({
      currentPlanIsActive: false,
      initialPlanName: undefined,
      showInactiveCurrentPlan: true,
    });
  });
});
