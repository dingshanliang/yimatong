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

const READ_ONLY_RECOVERY_REQUESTS = new Set([
  "post /auth/refresh",
  "post /auth/logout",
  "post /auth/change-password",
  "post /agency/exit-context",
]);

function normalizedApiPath(url?: string): string {
  if (!url) return "";
  if (/^[a-z][a-z\d+.-]*:\/\//i.test(url)) return "";
  let path = url.split(/[?#]/, 1)[0] ?? "";
  path = path.replace(/^\/api\/v1(?=\/|$)/, "");
  return path.length > 1 ? path.replace(/\/+$/, "") : path;
}

export function tenantPlanBlocksRequest(
  method?: string,
  url?: string
): boolean {
  if (!tenantPlanReadOnly) return false;

  const normalizedMethod = (method || "get").toLowerCase();
  if (["get", "head", "options"].includes(normalizedMethod)) return false;

  return !READ_ONLY_RECOVERY_REQUESTS.has(
    `${normalizedMethod} ${normalizedApiPath(url)}`
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

export type CanonicalTenantFeature =
  "ai_assistant" | "risk_module" | "channel_portal" | "white_label";

export function tenantFeatureEnabled(
  enabledFeatures: Record<string, boolean> | null | undefined,
  feature: CanonicalTenantFeature
): boolean {
  return enabledFeatures?.[feature] === true;
}

export function reportTenantPlanExpired(): void {
  setTenantPlanReadOnly(true);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(TENANT_PLAN_EXPIRED_EVENT));
  }
}
