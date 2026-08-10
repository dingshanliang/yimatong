const MANAGED_PUBLIC_FILE =
  /^\/api\/v1\/files\/public\/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@-]*(?:\/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@-]*)*$/;

function isPublicIpv4(hostname: string): boolean {
  const parts = hostname.split(".").map(Number);
  if (
    parts.length !== 4 ||
    parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)
  ) {
    return true;
  }

  const [a, b, c] = parts;
  return !(
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 0 && (c === 0 || c === 2)) ||
    (a === 192 && b === 168) ||
    (a === 198 && (b === 18 || b === 19)) ||
    (a === 198 && b === 51 && c === 100) ||
    (a === 203 && b === 0 && c === 113) ||
    a >= 224
  );
}

export function isCatalogPublicUrl(value: string): boolean {
  const normalized = value.trim();
  if (!normalized) return true;
  if (MANAGED_PUBLIC_FILE.test(normalized)) return true;

  try {
    const parsed = new URL(normalized);
    if (
      parsed.protocol !== "https:" ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password
    ) {
      return false;
    }

    const host = parsed.hostname
      .replace(/^\[|\]$/g, "")
      .replace(/\.$/, "")
      .toLowerCase();
    if (
      host === "localhost" ||
      host.endsWith(".localhost") ||
      host.endsWith(".local")
    ) {
      return false;
    }
    if (host.includes(":")) {
      return !(
        host === "::" ||
        host === "::1" ||
        host.startsWith("fc") ||
        host.startsWith("fd") ||
        /^fe[89ab]/.test(host) ||
        host.startsWith("2001:db8:")
      );
    }
    if (!isPublicIpv4(host)) return false;
    return host.includes(".");
  } catch {
    return false;
  }
}

export async function validateCatalogPublicUrl(
  _rule: unknown,
  value?: string
): Promise<void> {
  if (!value || isCatalogPublicUrl(value)) return;
  throw new Error("请输入公网 HTTPS 链接或系统公开文件路径");
}
