import axios, { type InternalAxiosRequestConfig } from "axios";
import {
  clearActiveScanToken,
  readActiveScanToken,
} from "@/lib/scan-token-store";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const SERVER_API_BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8000";

declare module "axios" {
  export interface InternalAxiosRequestConfig {
    /** 标记该请求的 Bearer 由拦截器隐式注入（当前码的 scan_token） */
    __implicitScanToken?: boolean;
  }
}

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
    // 隐式凭证按码隔离：只注入当前激活码的 scan_token，其次读 access_token；
    // 调用方已显式携带 Authorization（如回访凭证）时不再覆盖。
    const scanToken = readActiveScanToken();
    const accessToken = localStorage.getItem("access_token");
    const token = scanToken || accessToken;
    if (token && !config.headers.Authorization) {
      config.headers.Authorization = `Bearer ${token}`;
      config.__implicitScanToken = true;
    }
    // yimatong-zgb1.10：携带匿名访客 ID（first-party 稳定标识，Decision 22）
    const visitorId = localStorage.getItem("visitor_id");
    if (visitorId) {
      config.headers["X-Visitor-ID"] = visitorId;
    }
  }
  return config;
});

apiClient.interceptors.response.use(
  (res) => res,
  (error) => {
    // 401 时清除本地 token。仅当请求实际使用的是隐式注入的 scan_token
    // 时才清除：调用方显式携带的回访凭证等过期，不应误删另一条链路的凭证。
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      const request = error.config as
        | (InternalAxiosRequestConfig & { __implicitScanToken?: boolean })
        | undefined;
      if (typeof window !== "undefined" && request?.__implicitScanToken) {
        clearActiveScanToken();
      }
    }
    return Promise.reject(error);
  }
);

/**
 * 码解析客户端 — 走根路径，不需要 JWT
 * 用于 /c/{public_id} 码解析（公开路由）
 */
const resolverClient = axios.create({
  baseURL: API_BASE,
  timeout: 10000,
});

export { apiClient, resolverClient, API_BASE, SERVER_API_BASE };

export function getConsumerId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("consumer_id");
}
