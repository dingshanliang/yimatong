/**
 * JWT payload 解析工具（无签名验证）
 *
 * 注意：仅用于前端路由守卫等场景做过期检查，
 * 不做签名验证 —— 真正的鉴权由后端完成。
 */

export interface JwtPayload {
  sub?: string;
  tenant_id?: string;
  role?: string;
  tenant_type?: string;
  exp?: number;
  iat?: number;
  jti?: string;
  [key: string]: unknown;
}

/** base64url → base64，补全 padding */
function base64urlToBase64(str: string): string {
  let base64 = str.replace(/-/g, "+").replace(/_/g, "/");
  const padding = 4 - (base64.length % 4);
  if (padding !== 4) {
    base64 += "=".repeat(padding);
  }
  return base64;
}

/**
 * 从 JWT token 字符串中提取 payload。
 * @returns 解析后的 payload，或 null（格式错误）
 */
export function parseJwtPayload(token: string): JwtPayload | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;

  try {
    const payloadJson = atob(base64urlToBase64(parts[1]));
    return JSON.parse(payloadJson) as JwtPayload;
  } catch {
    return null;
  }
}

/**
 * 检查 JWT 是否已过期（仅基于 exp claim，不验证签名）。
 * @returns true 如果已过期或无法解析
 */
export function isJwtExpired(token: string): boolean {
  const payload = parseJwtPayload(token);
  if (!payload) return true;
  return payload.exp !== undefined && payload.exp * 1000 < Date.now();
}
