export interface RiskPrincipal {
  tenant_type?: string | null;
  role?: string | null;
  acting_tenant_id?: string | null;
}

export interface RiskAccess {
  canRead: boolean;
  canManage: boolean;
  canEvaluate: boolean;
}

const NO_RISK_ACCESS: RiskAccess = {
  canRead: false,
  canManage: false,
  canEvaluate: false,
};

export function riskAccessForPrincipal(
  principal: RiskPrincipal | null | undefined
): RiskAccess {
  if (
    !principal ||
    principal.tenant_type !== "brand" ||
    principal.acting_tenant_id
  ) {
    return NO_RISK_ACCESS;
  }
  const role = principal.role?.toLowerCase();
  if (role === "admin" || role === "operator") {
    return { canRead: true, canManage: true, canEvaluate: true };
  }
  return NO_RISK_ACCESS;
}
