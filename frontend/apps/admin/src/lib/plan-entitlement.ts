export const TENANT_PLAN_EXPIRED_EVENT = "yimatong:tenant-plan-expired";
let tenantPlanReadOnly = false;

export class TenantPlanReadOnlyError extends Error {
  code = "TENANT_PLAN_EXPIRED";

  constructor() {
    super("当前套餐已到期，写操作暂不可用；请联系平台管理员续期");
    this.name = "TenantPlanReadOnlyError";
  }
}

export function setTenantPlanReadOnly(active: boolean): void {
  tenantPlanReadOnly = active;
}

export function tenantPlanBlocksMethod(method?: string): boolean {
  return (
    tenantPlanReadOnly &&
    !["get", "head", "options"].includes((method || "get").toLowerCase())
  );
}

export function isTenantPlanExpired(
  planExpiresAt: string | null | undefined,
  now = Date.now()
): boolean {
  if (!planExpiresAt) return false;
  const expiry = Date.parse(planExpiresAt);
  return Number.isFinite(expiry) && expiry <= now;
}

export function tenantEntitlementKey(actingTenantId?: string | null): string {
  return `/tenants/me/entitlement?context=${encodeURIComponent(actingTenantId ?? "self")}`;
}

export function reportTenantPlanExpired(): void {
  setTenantPlanReadOnly(true);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(TENANT_PLAN_EXPIRED_EVENT));
  }
}
