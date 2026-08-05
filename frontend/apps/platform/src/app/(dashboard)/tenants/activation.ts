export interface InitialAdminActivationState {
  status: "active" | "suspended" | "terminated";
  activation_retryable: boolean;
}

export function canRetryInitialAdminActivation(
  tenant: InitialAdminActivationState
): boolean {
  return tenant.status !== "terminated" && tenant.activation_retryable;
}
