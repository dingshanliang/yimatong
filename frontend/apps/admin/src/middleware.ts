import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PUBLIC_PATHS = ["/login"];

// Route access rules by tenant_type
const AGENCY_ONLY_ROUTES = ["/agency"];
const BRAND_ONLY_ROUTES = [
  "/brands", "/products", "/skus", "/batches",
  "/codes", "/pages", "/campaigns", "/benefits", "/members",
  "/channels", "/regional", "/accounts",
  "/integrations", "/connectors", "/risk", "/launch-checklist",
];

function matchesRoute(pathname: string, routes: string[]): boolean {
  return routes.some((r) => pathname === r || pathname.startsWith(r + "/"));
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  // 从 cookie 读取 access_token（登录时同步写入）
  const token = request.cookies.get("access_token")?.value;

  if (!token) {
    const loginUrl = new URL("/login", request.url);
    return NextResponse.redirect(loginUrl);
  }

  // Decode JWT to get tenant_type (without verification - just for routing decisions)
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    const tenantType = payload.tenant_type || "brand";
    const role = (payload.role || "").toLowerCase();

    // Portal users can only access their portal + dashboard root
    if (role === "distributor") {
      if (pathname !== "/" && pathname !== "/channel-portal" && !pathname.startsWith("/channel-portal/")) {
        return NextResponse.redirect(new URL("/channel-portal", request.url));
      }
    }
    if (role === "store_guide") {
      if (pathname !== "/" && pathname !== "/store-portal" && !pathname.startsWith("/store-portal/")) {
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
  } catch {
    // Invalid token, redirect to login
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|react-grab.js).*)"],
};
