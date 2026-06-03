import { create } from "zustand";
import api from "./api";

interface AuthUser {
  account_id: string;
  tenant_id: string;
  role: string;
  tenant_type: string;
  email: string;
  name: string;
  // Agency context
  acting_tenant_id: string | null;
  agency_scope: string[] | null;
}

interface AuthState {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  hydrate: () => void;
  silentRefresh: () => Promise<string | null>;
  switchAgencyContext: (clientTenantId: string) => Promise<void>;
  exitAgencyContext: () => Promise<void>;
}

let refreshPromise: Promise<string | null> | null = null;

/** 确保 AuthUser 对象包含 tenant_type 和 agency context 字段（向后兼容旧 localStorage 数据） */
function ensureTenantType(user: AuthUser): AuthUser {
  if (!user.tenant_type) user.tenant_type = "brand";
  if (user.acting_tenant_id === undefined) user.acting_tenant_id = null;
  if (user.agency_scope === undefined) user.agency_scope = null;
  return user;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: null,
  loading: false,

  login: async (email, password) => {
    set({ loading: true });
    try {
      const { data } = await api.post("/auth/login", { email, password });
      const { access_token, refresh_token, expires_in } = data;
      _persistTokens(access_token, refresh_token, expires_in);

      // Decode JWT to extract user info
      const payload = JSON.parse(atob(access_token.split(".")[1]));
      const user: AuthUser = {
        account_id: payload.sub,
        tenant_id: payload.tenant_id,
        role: payload.role,
        tenant_type: payload.tenant_type || "brand",
        email,
        name: payload.name || email,
        acting_tenant_id: payload.acting_tenant_id || null,
        agency_scope: payload.scope || null,
      };
      localStorage.setItem("auth_store", JSON.stringify(user));
      set({ user, token: access_token, loading: false });
    } catch {
      set({ loading: false });
      throw new Error("登录失败，请检查邮箱和密码");
    }
  },

  logout: () => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    localStorage.removeItem("auth_store");
    document.cookie = "access_token=; path=/; max-age=0";
    set({ user: null, token: null });
  },

  switchAgencyContext: async (clientTenantId: string) => {
    const { data } = await api.post("/ops/authorizations/switch-context", { client_tenant_id: clientTenantId });
    const stored = localStorage.getItem("auth_store");
    const base = stored ? JSON.parse(stored) : {};
    const updatedUser: AuthUser = {
      ...base,
      acting_tenant_id: data.acting_tenant_id || clientTenantId,
      agency_scope: data.scope || null,
    };
    _applyTokenUpdate(data.access_token, updatedUser);
  },

  exitAgencyContext: async () => {
    const { data } = await api.post("/ops/authorizations/exit-context");
    const stored = localStorage.getItem("auth_store");
    const base = stored ? JSON.parse(stored) : {};
    const updatedUser: AuthUser = {
      ...base,
      acting_tenant_id: null,
      agency_scope: null,
    };
    _applyTokenUpdate(data.access_token, updatedUser);
  },

  hydrate: () => {
    if (typeof window === "undefined") return;
    const stored = localStorage.getItem("auth_store");
    const token = localStorage.getItem("access_token");
    if (stored && token) {
      try {
        set({ user: ensureTenantType(JSON.parse(stored)), token });
      } catch {
        localStorage.removeItem("auth_store");
        localStorage.removeItem("access_token");
      }
    }
  },

  silentRefresh: async () => {
    // 防止并发刷新
    if (refreshPromise) return refreshPromise;

    refreshPromise = _doSilentRefresh();
    try {
      return await refreshPromise;
    } finally {
      refreshPromise = null;
    }
  },
}));

/** 将 access_token 和 refresh_token 持久化到 localStorage + cookie */
function _persistTokens(accessToken: string, refreshToken: string, _expiresIn?: number) {
  localStorage.setItem("access_token", accessToken);
  localStorage.setItem("refresh_token", refreshToken);
  // cookie 供 Next.js middleware 读取，max-age 用 refresh token 的有效期（30天）
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `access_token=${accessToken}; path=/; max-age=${30 * 24 * 3600}; SameSite=Lax${secure}`;
}

/** 切换 agency 上下文时更新 token 和用户状态 */
function _applyTokenUpdate(accessToken: string, updatedUser: AuthUser) {
  localStorage.setItem("auth_store", JSON.stringify(updatedUser));
  localStorage.setItem("access_token", accessToken);
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `access_token=${accessToken}; path=/; max-age=${30 * 24 * 3600}; SameSite=Lax${secure}`;
  useAuthStore.setState({ user: updatedUser, token: accessToken });
}

async function _doSilentRefresh(): Promise<string | null> {
  const refreshToken = localStorage.getItem("refresh_token");
  if (!refreshToken) return null;

  try {
    const { data } = await api.post("/auth/refresh", { refresh_token: refreshToken });
    const { access_token, refresh_token: newRefreshToken, expires_in } = data;
    _persistTokens(access_token, newRefreshToken, expires_in);

    // 更新 zustand state
    const stored = localStorage.getItem("auth_store");
    if (stored) {
      const payload = JSON.parse(atob(access_token.split(".")[1]));
      const parsedStored = JSON.parse(stored);
      const user: AuthUser = {
        account_id: payload.sub,
        tenant_id: payload.tenant_id,
        role: payload.role,
        tenant_type: payload.tenant_type || "brand",
        email: parsedStored.email,
        name: parsedStored.name || parsedStored.email,
        acting_tenant_id: payload.acting_tenant_id || parsedStored.acting_tenant_id || null,
        agency_scope: payload.scope || parsedStored.agency_scope || null,
      };
      localStorage.setItem("auth_store", JSON.stringify(user));
      useAuthStore.setState({ user, token: access_token });
    } else {
      useAuthStore.setState({ token: access_token });
    }

    return access_token;
  } catch {
    // refresh 失败，清除登录态
    useAuthStore.getState().logout();
    return null;
  }
}

// 同步自动 hydrate：模块加载时立即从 localStorage 恢复状态，
// 确保任何组件首次读取 store 时就能拿到 user 和 token
if (typeof window !== "undefined") {
  const token = localStorage.getItem("access_token");
  const stored = localStorage.getItem("auth_store");
  if (token && stored) {
    try {
      useAuthStore.setState({ user: ensureTenantType(JSON.parse(stored)), token });
    } catch {
      // 自动 hydrate 失败静默忽略，留给运行时 hydrate 处理
    }
  }
}
