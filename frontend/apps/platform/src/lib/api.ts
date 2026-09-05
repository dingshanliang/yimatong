import axios from "axios";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const api = axios.create({
  baseURL: `${API_BASE}/api/v1`,
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
  withCredentials: true,
  withXSRFToken: true,
  xsrfCookieName: "platform_csrf_token",
  xsrfHeaderName: "X-Platform-CSRF",
});

let logoutHandler: (() => void) | null = null;

export function registerPlatformLogoutHandler(handler: () => void) {
  logoutHandler = handler;
}

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    if (error.response?.status === 401) {
      // 登录接口自身的 401 是"凭证错误"，必须留给登录页展示错误，
      // 不能整页跳转 /login 冲掉错误提示。
      const requestUrl: unknown = error.config?.url;
      const isLoginRequest =
        typeof requestUrl === "string" &&
        requestUrl.includes("/platform/auth/login");
      if (!isLoginRequest && typeof window !== "undefined") {
        logoutHandler?.();
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

export function extractErrorMessage(
  err: unknown,
  fallback = "操作失败"
): string {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data as
      { detail?: unknown; message?: unknown } | undefined;
    const detail = data?.detail ?? data?.message;
    if (typeof detail === "string") {
      return detail || fallback;
    }
    if (Array.isArray(detail)) {
      const firstMessage = detail
        .map((item) => {
          if (typeof item === "string") return item;
          if (
            item &&
            typeof item === "object" &&
            "msg" in item &&
            typeof item.msg === "string"
          )
            return item.msg;
          return null;
        })
        .find(Boolean);
      return firstMessage || fallback;
    }
    return fallback;
  }
  if (err instanceof Error) return err.message || fallback;
  return fallback;
}

export default api;
