export function buildConnectSrc(
  nodeEnv: string | undefined,
  rawPublicApiUrl: string | undefined
): string {
  const sources = new Set<string>(["'self'"]);
  const isProduction = nodeEnv === "production";

  if (!isProduction) {
    sources.add("http://localhost:*");
    sources.add("http://127.0.0.1:*");
  }

  const configured = rawPublicApiUrl?.trim();
  if (isProduction && !configured) {
    throw new Error("NEXT_PUBLIC_API_URL is required in production");
  }
  if (configured) {
    let apiUrl: URL;
    try {
      apiUrl = new URL(configured);
    } catch {
      throw new Error("NEXT_PUBLIC_API_URL must be an absolute URL");
    }
    if (apiUrl.username || apiUrl.password) {
      throw new Error("NEXT_PUBLIC_API_URL must not contain credentials");
    }
    if (apiUrl.pathname !== "/" || apiUrl.search || apiUrl.hash) {
      throw new Error("NEXT_PUBLIC_API_URL must contain only an origin");
    }
    if (
      apiUrl.protocol !== "https:" &&
      (isProduction || apiUrl.protocol !== "http:")
    ) {
      throw new Error("NEXT_PUBLIC_API_URL must use HTTPS in production");
    }
    sources.add(apiUrl.origin);
  }

  return [...sources].join(" ");
}
