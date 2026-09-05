import axios from "axios";
import { parseJwtPayload } from "@yimatong/shared";
import {
  TenantPlanReadOnlyError,
  reportTenantPlanExpired,
  tenantPlanBlocksRequest,
} from "./plan-entitlement";

declare module "axios" {
  interface AxiosRequestConfig {
    skipAuthRefresh?: boolean;
  }

  interface InternalAxiosRequestConfig {
    skipAuthRefresh?: boolean;
  }
}

export class AgencyContextRevalidationError extends Error {
  constructor() {
    super("代运营客户授权已失效，请重新选择客户");
    this.name = "AgencyContextRevalidationError";
  }
}

function getDefaultApiBase() {
  if (
    typeof window !== "undefined" &&
    window.location.hostname === "127.0.0.1"
  ) {
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

interface AuthInterceptorHandlers {
  silentRefresh: () => Promise<string | null>;
  logout: () => void;
}

let authHandlers: AuthInterceptorHandlers | null = null;

export function registerAuthInterceptorHandlers(
  handlers: AuthInterceptorHandlers
) {
  authHandlers = handlers;
}

api.interceptors.request.use((config) => {
  if (tenantPlanBlocksRequest(config.method, config.url)) {
    return Promise.reject(new TenantPlanReadOnlyError());
  }
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("access_token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
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

function bearerTokenFromRequest(headers: unknown): string | null {
  if (!headers || typeof headers !== "object") return null;
  const candidate = headers as {
    Authorization?: unknown;
    authorization?: unknown;
    get?: (name: string) => unknown;
  };
  const value =
    candidate.get?.("Authorization") ??
    candidate.Authorization ??
    candidate.authorization;
  return typeof value === "string" && value.startsWith("Bearer ")
    ? value.slice(7)
    : null;
}

export function refreshPreservesActingContext(
  originalToken: string | null,
  refreshedToken: string
): boolean {
  if (!originalToken) return true;
  const originalActingTenant = parseJwtPayload(originalToken)?.acting_tenant_id;
  if (typeof originalActingTenant !== "string") return true;
  return (
    parseJwtPayload(refreshedToken)?.acting_tenant_id === originalActingTenant
  );
}

function rejectDroppedActingContext(
  originalToken: string | null,
  refreshedToken: string
) {
  if (refreshPreservesActingContext(originalToken, refreshedToken)) return null;
  const error = new Error("代运营客户上下文已失效，请重新选择客户");
  if (typeof window !== "undefined") {
    window.location.href = "/agency";
  }
  return error;
}

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const originalRequest = error.config;
    const originalToken = bearerTokenFromRequest(originalRequest?.headers);

    if (
      error.response?.status === 403 &&
      error.response?.data?.code === "TENANT_PLAN_EXPIRED"
    ) {
      reportTenantPlanExpired();
      return Promise.reject(error);
    }

    // switch-context revalidation is itself part of the refresh transaction.
    // Retrying it through the interceptor would wait on the same refresh promise.
    if (originalRequest?.skipAuthRefresh) {
      return Promise.reject(error);
    }

    // 非 401 或已重试过，直接拒绝
    if (error.response?.status !== 401 || originalRequest._retry) {
      return Promise.reject(error);
    }

    // refresh 接口本身 401，说明 refresh_token 也过期了，直接登出
    if (originalRequest.url?.includes("/auth/refresh")) {
      if (typeof window !== "undefined") {
        authHandlers?.logout();
        window.location.href = "/login";
      }
      return Promise.reject(error);
    }

    if (isRefreshing) {
      // 已有刷新请求进行中，排队等待
      return new Promise<string>((resolve, reject) => {
        failedQueue.push({ resolve, reject });
      }).then((token) => {
        const contextError = rejectDroppedActingContext(originalToken, token);
        if (contextError) throw contextError;
        originalRequest.headers.Authorization = `Bearer ${token}`;
        return api(originalRequest);
      });
    }

    originalRequest._retry = true;
    isRefreshing = true;

    try {
      const newToken = (await authHandlers?.silentRefresh()) ?? null;

      if (!newToken) {
        // refresh 失败，登出
        authHandlers?.logout();
        if (typeof window !== "undefined") {
          window.location.href = "/login";
        }
        return Promise.reject(error);
      }

      const contextError = rejectDroppedActingContext(originalToken, newToken);
      if (contextError) {
        processQueue(contextError);
        return Promise.reject(contextError);
      }

      processQueue(null, newToken);
      originalRequest.headers.Authorization = `Bearer ${newToken}`;
      return api(originalRequest);
    } catch (refreshError) {
      processQueue(refreshError);
      if (
        refreshError instanceof AgencyContextRevalidationError &&
        typeof window !== "undefined"
      ) {
        window.location.href = "/agency";
      }
      return Promise.reject(refreshError);
    } finally {
      isRefreshing = false;
    }
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
      // pydantic 校验错误原文是英文实现细节，直接透出不可行动；无中文可用时给通用指引
      if (!firstMessage || !/[\u4e00-\u9fff]/.test(firstMessage)) {
        return "提交的信息格式有误，请检查各填写项后重试";
      }
      return firstMessage;
    }
    if (
      detail &&
      typeof detail === "object" &&
      "msg" in detail &&
      typeof detail.msg === "string"
    ) {
      return detail.msg || fallback;
    }
    return fallback;
  }
  if (err instanceof Error) return err.message || fallback;
  return fallback;
}

export default api;
