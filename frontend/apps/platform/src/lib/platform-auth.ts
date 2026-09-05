import { isAxiosError } from "axios";
import { create } from "zustand";
import api, { extractErrorMessage, registerPlatformLogoutHandler } from "./api";

const LOGIN_FALLBACK_MESSAGE = "登录失败，请检查邮箱和密码";

function readRetryAfterHeader(headers: unknown): string | undefined {
  if (!headers || typeof headers !== "object") return undefined;
  const record = headers as Record<string, unknown>;
  const value =
    record["retry-after"] ?? record["Retry-After"] ?? record["RETRY-AFTER"];
  return value === undefined || value === null ? undefined : String(value);
}

/**
 * 透传后端登录错误 detail（429/503/500 等）；
 * 429 且带 Retry-After 时拼入可重试时间提示。
 */
export function buildPlatformLoginErrorMessage(err: unknown): string {
  const base = extractErrorMessage(err, LOGIN_FALLBACK_MESSAGE);
  if (isAxiosError(err) && err.response?.status === 429) {
    const retryAfterSeconds = Number.parseInt(
      readRetryAfterHeader(err.response.headers) ?? "",
      10
    );
    if (Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0) {
      return `${base}，约 ${retryAfterSeconds} 秒后可重试`;
    }
  }
  return base;
}

interface PlatformAuthState {
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  clearSession: () => void;
}

export const usePlatformAuth = create<PlatformAuthState>((set) => ({
  loading: false,

  login: async (email, password) => {
    set({ loading: true });
    try {
      await api.post("/platform/auth/login", { email, password });
      set({ loading: false });
    } catch (err) {
      set({ loading: false });
      throw new Error(buildPlatformLoginErrorMessage(err));
    }
  },

  logout: async () => {
    set({ loading: true });
    try {
      await api.post("/platform/auth/logout");
    } finally {
      set({ loading: false });
    }
  },

  clearSession: () => {
    set({ loading: false });
  },
}));

registerPlatformLogoutHandler(() => usePlatformAuth.getState().clearSession());
