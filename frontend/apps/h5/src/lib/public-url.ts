const MANAGED_PUBLIC_PATH =
  /^\/api\/v1\/files\/public\/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@-]*(?:\/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@-]*)*$/;

function isPrivateIpv4(host: string): boolean {
  const octets = host.split(".").map(Number);
  if (
    octets.length !== 4 ||
    octets.some((value) => !Number.isInteger(value) || value < 0 || value > 255)
  ) {
    return false;
  }
  const [a, b] = octets;
  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    a >= 224
  );
}

/** Defense-in-depth for persisted URLs rendered on the public consumer page. */
export function safePublicUrl(value: string | null | undefined): string | null {
  const normalized = value?.trim();
  if (!normalized) return null;
  if (MANAGED_PUBLIC_PATH.test(normalized)) return normalized;

  try {
    const parsed = new URL(normalized);
    const host = parsed.hostname.replace(/\.$/, "").toLowerCase();
    if (parsed.protocol !== "https:" || parsed.username || parsed.password)
      return null;
    if (
      !host.includes(".") ||
      host === "localhost" ||
      host.endsWith(".localhost") ||
      host.endsWith(".local")
    ) {
      return null;
    }
    if (
      isPrivateIpv4(host) ||
      host === "::1" ||
      host.startsWith("fe80:") ||
      host.startsWith("fc") ||
      host.startsWith("fd")
    ) {
      return null;
    }
    return normalized;
  } catch {
    return null;
  }
}
