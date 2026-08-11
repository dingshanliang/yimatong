import type { AuthUser } from "./auth";

export interface CampaignAccess {
  canView: boolean;
  canManage: boolean;
}

export function resolveCampaignAccess(user: AuthUser | null): CampaignAccess {
  if (!user || !["admin", "operator"].includes(user.role)) {
    return { canView: false, canManage: false };
  }
  if (user.tenant_type === "brand") {
    return { canView: true, canManage: true };
  }
  const scopedAgency =
    user.tenant_type === "agency" &&
    Boolean(user.acting_tenant_id) &&
    Boolean(user.agency_scope?.includes("campaigns"));
  return { canView: scopedAgency, canManage: scopedAgency };
}
