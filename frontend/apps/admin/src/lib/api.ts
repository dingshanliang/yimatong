import axios from "axios";

function getDefaultApiBase() {
  if (typeof window !== "undefined" && window.location.hostname === "127.0.0.1") {
    return "http://127.0.0.1:8000";
  }
  return "http://localhost:8000";
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || getDefaultApiBase();

const api = axios.create({
  baseURL: `${API_BASE}/api/v1`,
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
  withCredentials: true,
});

api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    // HttpOnly cookie 自动携带，回退到 localStorage
    const cookieToken = document.cookie.includes("access_token=");
    if (!cookieToken) {
      const token = localStorage.getItem("access_token");
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
    }
  }
  return config;
});

let isRefreshing = false;
let failedQueue: Array<{
  resolve: (token: string) => void;
  reject: (error: unknown) => void;
}> = [];

function processQueue(error: unknown, token: string | null = null) {
  failedQueue.forEach(({ resolve, reject }) => {
    if (token) resolve(token);
    else reject(error);
  });
  failedQueue = [];
}

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const originalRequest = error.config;

    // 非 401 或已重试过，直接拒绝
    if (error.response?.status !== 401 || originalRequest._retry) {
      return Promise.reject(error);
    }

    // refresh 接口本身 401，说明 refresh_token 也过期了，直接登出
    if (originalRequest.url?.includes("/auth/refresh")) {
      if (typeof window !== "undefined") {
        import("./auth").then(({ useAuthStore }) => {
          useAuthStore.getState().logout();
          window.location.href = "/login";
        });
      }
      return Promise.reject(error);
    }

    if (isRefreshing) {
      // 已有刷新请求进行中，排队等待
      return new Promise((resolve, reject) => {
        failedQueue.push({ resolve, reject });
      }).then((token) => {
        originalRequest.headers.Authorization = `Bearer ${token}`;
        return api(originalRequest);
      });
    }

    originalRequest._retry = true;
    isRefreshing = true;

    try {
      const { useAuthStore } = await import("./auth");
      const newToken = await useAuthStore.getState().silentRefresh();

      if (!newToken) {
        // refresh 失败，登出
        useAuthStore.getState().logout();
        if (typeof window !== "undefined") {
          window.location.href = "/login";
        }
        return Promise.reject(error);
      }

      processQueue(null, newToken);
      originalRequest.headers.Authorization = `Bearer ${newToken}`;
      return api(originalRequest);
    } catch (refreshError) {
      processQueue(refreshError);
      return Promise.reject(refreshError);
    } finally {
      isRefreshing = false;
    }
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
    if (detail && typeof detail === "object" && "msg" in detail && typeof detail.msg === "string") {
      return detail.msg || fallback;
    }
    return fallback;
  }
  if (err instanceof Error) return err.message || fallback;
  return fallback;
}

export default api;
