import axios from "axios";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const api = axios.create({
  baseURL: `${API_BASE}/api/v1`,
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
  withCredentials: true,
});

api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("platform_access_token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    if (error.response?.status === 401) {
      if (typeof window !== "undefined") {
        const { usePlatformAuth } = await import("./platform-auth");
        usePlatformAuth.getState().logout();
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  },
);

export function extractErrorMessage(err: unknown, fallback = "操作失败"): string {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data as { detail?: unknown; message?: unknown } | undefined;
    const detail = data?.detail ?? data?.message;
    if (typeof detail === "string") {
      return detail || fallback;
    }
    if (Array.isArray(detail)) {
      const firstMessage = detail
        .map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object" && "msg" in item && typeof item.msg === "string") return item.msg;
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
