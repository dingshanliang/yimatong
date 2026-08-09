export const ACTIVE_PLAN_SOURCE = "/platform/plans/active";

export interface ActivePlanDefinition {
  id: string;
  name: string;
  display_name: string;
  is_active: boolean;
}

export function activePlanOptions(
  plans: ActivePlanDefinition[] | undefined,
  supportedPlanNames: ReadonlySet<string>
) {
  return (plans ?? [])
    .filter((plan) => plan.is_active && supportedPlanNames.has(plan.name))
    .map((plan) => ({ value: plan.name, label: plan.display_name }));
}

export function findActivePlan(
  plans: ActivePlanDefinition[] | undefined,
  planName: string
): ActivePlanDefinition | undefined {
  return plans?.find((plan) => plan.is_active && plan.name === planName);
}

export function buildPlanAssignmentState(
  plans: ActivePlanDefinition[] | undefined,
  currentPlanName: string
) {
  const loaded = plans !== undefined;
  const currentPlanIsActive = !!findActivePlan(plans, currentPlanName);
  return {
    currentPlanIsActive,
    initialPlanName: currentPlanIsActive ? currentPlanName : undefined,
    showInactiveCurrentPlan: loaded && !currentPlanIsActive,
  };
}
