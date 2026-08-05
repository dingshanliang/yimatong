import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { parseJwtPayload } from "@yimatong/shared";

const PUBLIC_PATHS = ["/login"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  // 平台后台只接受独立的 HttpOnly platform cookie。
  const token = request.cookies.get("platform_access_token")?.value;

  if (!token) {
    const loginUrl = new URL("/login", request.url);
    return NextResponse.redirect(loginUrl);
  }

  // 基本验证：检查 token 格式和过期
  const payload = parseJwtPayload(token);
  if (!payload || (payload.exp && payload.exp * 1000 < Date.now())) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  if (
    payload.role !== "platform_admin" ||
    payload.tenant_type !== "platform" ||
    payload.sub !== "platform-admin" ||
    payload.tenant_id !== "platform"
  ) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
