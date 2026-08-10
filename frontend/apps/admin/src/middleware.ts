import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { parseJwtPayload } from "@yimatong/shared";
import {
  firstAgencyScopedRoute,
  isAgencyScopedRouteAllowed,
} from "@/lib/agency-access";
import { catalogAccessForPrincipal } from "@/lib/catalog-access";
import { codeAccessForPrincipal } from "@/lib/code-access";

const PUBLIC_PATHS = ["/login", "/register", "/reset-password"];

// Route access rules by tenant_type
const AGENCY_ONLY_ROUTES = ["/agency"];
const EXPORT_ADMIN_ROUTES = ["/exports"];
const CATALOG_ROUTES = [
  "/brands",
  "/products",
  "/skus",
  "/batches",
  "/imports",
];
const BRAND_ONLY_ROUTES = [
  "/brands",
  "/products",
  "/skus",
  "/batches",
  "/imports",
  "/codes",
  "/pages",
  "/campaigns",
  "/benefits",
  "/members",
  "/channels",
  "/regional",
  "/accounts",
  "/integrations",
  "/connectors",
  "/risk",
  "/launch-checklist",
  "/settings/agency-authorizations",
];

function matchesRoute(pathname: string, routes: string[]): boolean {
  return routes.some((r) => pathname === r || pathname.startsWith(r + "/"));
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const isPublicPath = PUBLIC_PATHS.some((p) => pathname.startsWith(p));

  // 邀请注册必须能从交付链接直接进入，也不能被浏览器里残留的失效登录 cookie 阻断。
  if (pathname === "/register" || pathname.startsWith("/register/")) {
    return NextResponse.next();
  }

  // 从 cookie 读取 access_token（登录时同步写入）
  const token = request.cookies.get("access_token")?.value;

  if (!token) {
    if (isPublicPath) return NextResponse.next();
    const loginUrl = new URL("/login", request.url);
    return NextResponse.redirect(loginUrl);
  }

  // Decode JWT to get tenant_type (without verification - just for routing decisions)
  try {
    const payload = parseJwtPayload(token);
    if (!payload) {
      return NextResponse.redirect(new URL("/login", request.url));
    }
    const tenantType = payload.tenant_type || "brand";
    const role = (payload.role || "").toLowerCase();

    if (payload.must_change_password === true) {
      if (pathname !== "/change-password") {
        return NextResponse.redirect(new URL("/change-password", request.url));
      }
      return NextResponse.next();
    }

    if (pathname === "/change-password") {
      return NextResponse.redirect(new URL("/", request.url));
    }

    if (isPublicPath) return NextResponse.next();

    // Portal users can only access their portal + dashboard root
    if (role === "distributor") {
      if (
        pathname !== "/" &&
        pathname !== "/channel-portal" &&
        !pathname.startsWith("/channel-portal/")
      ) {
        return NextResponse.redirect(new URL("/channel-portal", request.url));
      }
    }
    if (role === "store_guide") {
      if (
        pathname !== "/" &&
        pathname !== "/store-portal" &&
        !pathname.startsWith("/store-portal/")
      ) {
        return NextResponse.redirect(new URL("/store-portal", request.url));
      }
    }

    // Agency-only routes: block non-agency users
    if (tenantType !== "agency" && matchesRoute(pathname, AGENCY_ONLY_ROUTES)) {
      return NextResponse.redirect(new URL("/", request.url));
    }

    // Brand-only routes: block agency users unless they have acting_tenant_id
    if (tenantType === "agency" && !payload.acting_tenant_id) {
      if (matchesRoute(pathname, BRAND_ONLY_ROUTES)) {
        return NextResponse.redirect(new URL("/agency", request.url));
      }
    }

    if (tenantType === "agency" && payload.acting_tenant_id) {
      const scopes = Array.isArray(payload.scope)
        ? payload.scope.filter(
            (scope): scope is string => typeof scope === "string"
          )
        : [];
      if (!isAgencyScopedRouteAllowed(pathname, scopes)) {
        return NextResponse.redirect(
          new URL(firstAgencyScopedRoute(scopes), request.url)
        );
      }
    }

    if (
      matchesRoute(pathname, CATALOG_ROUTES) &&
      !catalogAccessForPrincipal({
        tenant_type: tenantType,
        role,
        acting_tenant_id:
          typeof payload.acting_tenant_id === "string"
            ? payload.acting_tenant_id
            : null,
        agency_scope: Array.isArray(payload.scope)
          ? payload.scope.filter(
              (scope): scope is string => typeof scope === "string"
            )
          : [],
      }).canRead
    ) {
      return NextResponse.redirect(new URL("/", request.url));
    }

    if (matchesRoute(pathname, EXPORT_ADMIN_ROUTES)) {
      const exportAccess = codeAccessForPrincipal({
        tenant_type: tenantType,
        role,
        acting_tenant_id:
          typeof payload.acting_tenant_id === "string"
            ? payload.acting_tenant_id
            : null,
        agency_scope: Array.isArray(payload.scope)
          ? payload.scope.filter(
              (scope): scope is string => typeof scope === "string"
            )
          : [],
      });
      if (tenantType !== "brand" || !exportAccess.canManage) {
        return NextResponse.redirect(new URL("/", request.url));
      }
    }
  } catch {
    // Invalid token, redirect to login
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|react-grab.js).*)"],
};
