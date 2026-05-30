import axios from "axios";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

/**
 * API 客户端 — 走 /api/v1/ 前缀
 * 需要 Bearer Token（从 scanToken store 或 localStorage 获取）
 */
const apiClient = axios.create({
  baseURL: `${API_BASE}/api/v1`,
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
});

apiClient.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    // 优先从 localStorage 读取 scan_token，其次读 access_token
    const scanToken = localStorage.getItem("scan_token");
    const accessToken = localStorage.getItem("access_token");
    const token = scanToken || accessToken;
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});

apiClient.interceptors.response.use(
  (res) => res,
  (error) => {
    // 401 时清除本地 token
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      if (typeof window !== "undefined") {
        localStorage.removeItem("scan_token");
      }
    }
    return Promise.reject(error);
  },
);

/**
 * 码解析客户端 — 走根路径，不需要 JWT
 * 用于 /c/{public_id} 码解析（公开路由）
 */
const resolverClient = axios.create({
  baseURL: API_BASE,
  timeout: 10000,
});

export { apiClient, resolverClient, API_BASE };
