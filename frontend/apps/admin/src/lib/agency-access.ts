import type { AuthUser } from "./auth";

type AgencyAuthorizationPrincipal = Pick<AuthUser, "role" | "tenant_type">;

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
