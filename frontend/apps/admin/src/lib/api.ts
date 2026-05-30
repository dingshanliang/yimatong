import axios from "axios";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

const api = axios.create({
  baseURL: `${API_BASE}/api/v1`,
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("access_token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (error) => {
    if (error.response?.status === 401 && typeof window !== "undefined") {
      // 委托给 auth store 的 logout，确保 localStorage + cookie 同步清除
      import("./auth").then(({ useAuthStore }) => {
        useAuthStore.getState().logout();
        window.location.href = "/login";
      });
    }
    return Promise.reject(error);
  }
);

export function extractErrorMessage(err: unknown, fallback = "操作失败"): string {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data as { detail?: string } | undefined;
    return data?.detail || fallback;
  }
  if (err instanceof Error) return err.message || fallback;
  return fallback;
}

export default api;
