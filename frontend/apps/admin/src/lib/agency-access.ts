import type { AuthUser } from "./auth";

type AgencyAuthorizationPrincipal = Pick<AuthUser, "role" | "tenant_type">;
type TakeoverPrincipal = Pick<
  AuthUser,
  "role" | "tenant_type" | "acting_tenant_id" | "agency_scope"
>;

export interface TakeoverAccess {
  canRead: boolean;
  canPrepare: boolean;
  canApprove: boolean;
  canExecute: boolean;
  canRollback: boolean;
  canAudit: boolean;
}

const NO_TAKEOVER_ACCESS: TakeoverAccess = {
  canRead: false,
  canPrepare: false,
  canApprove: false,
  canExecute: false,
  canRollback: false,
  canAudit: false,
};

const AGENCY_SCOPE_ROUTES: Record<string, readonly string[]> = {
  products: ["/brands", "/products", "/skus", "/batches", "/imports"],
  pages: ["/pages"],
  campaigns: ["/campaigns", "/benefits"],
  codes: ["/codes"],
  analytics: ["/", "/analytics"],
};

const AGENCY_SCOPE_PRIORITY = [
  "analytics",
  "products",
  "pages",
  "campaigns",
  "codes",
] as const;

const AGENCY_SCOPE_DEFAULT_ROUTE: Record<string, string> = {
  analytics: "/",
  products: "/products",
  pages: "/pages",
  campaigns: "/campaigns",
  codes: "/codes",
};

export function canManageAgencyAuthorizations(
  user: AgencyAuthorizationPrincipal | null | undefined
): boolean {
  return user?.tenant_type === "brand" && user.role.toLowerCase() === "admin";
}

export function takeoverAccessForPrincipal(
  user: TakeoverPrincipal | null | undefined
): TakeoverAccess {
  if (!user) return NO_TAKEOVER_ACCESS;
  const role = user.role.toLowerCase();
  const isActingCodesAgency =
    user.tenant_type === "agency" &&
    Boolean(user.acting_tenant_id) &&
    user.agency_scope?.includes("codes") === true;
  if (user.tenant_type === "agency" && !isActingCodesAgency) {
    return NO_TAKEOVER_ACCESS;
  }
  if (user.tenant_type !== "brand" && !isActingCodesAgency) {
    return NO_TAKEOVER_ACCESS;
  }
  if (role === "admin") {
    return {
      canRead: true,
      canPrepare: true,
      canApprove: true,
      canExecute: true,
      canRollback: true,
      canAudit: true,
    };
  }
  if (role === "operator") {
    return {
      canRead: true,
      canPrepare: true,
      canApprove: false,
      canExecute: false,
      canRollback: false,
      canAudit: true,
    };
  }
  return NO_TAKEOVER_ACCESS;
}

function matchesRoute(pathname: string, route: string): boolean {
  return route === "/"
    ? pathname === route
    : pathname === route || pathname.startsWith(`${route}/`);
}

export function isAgencyScopedRouteAllowed(
  pathname: string,
  scopes: readonly string[]
): boolean {
  return scopes.some((scope) =>
    (AGENCY_SCOPE_ROUTES[scope] || []).some((route) =>
      matchesRoute(pathname, route)
    )
  );
}

export function firstAgencyScopedRoute(scopes: readonly string[]): string {
  for (const scope of AGENCY_SCOPE_PRIORITY) {
    if (scopes.includes(scope)) {
      return AGENCY_SCOPE_DEFAULT_ROUTE[scope];
    }
  }
  return "/agency";
}

export function agencyScopeMenuRoutes(scope: string): readonly string[] {
  return AGENCY_SCOPE_ROUTES[scope] || [];
}
