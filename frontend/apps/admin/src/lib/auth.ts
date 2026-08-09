import { create } from "zustand";
import axios from "axios";
import { parseJwtPayload } from "@yimatong/shared";
import api, {
  AgencyContextRevalidationError,
  registerAuthInterceptorHandlers,
} from "./api";

export interface AuthUser {
  account_id: string;
  tenant_id: string;
  role: string;
  tenant_type: string;
  email: string;
  name: string;
  // Agency context
  acting_tenant_id: string | null;
  agency_scope: string[] | null;
  must_change_password: boolean;
}

interface AuthState {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  login: (
    email: string,
    password: string,
    options?: { tenantSlug?: string }
  ) => Promise<void>;
  logout: () => Promise<void>;
  clearSession: () => void;
  hydrate: () => void;
  silentRefresh: () => Promise<string | null>;
  switchAgencyContext: (clientTenantId: string) => Promise<void>;
  exitAgencyContext: () => Promise<void>;
}

let refreshPromise: Promise<string | null> | null = null;
const lastLoginRef = { current: 0 };

/** 确保 AuthUser 对象包含 tenant_type 和 agency context 字段（向后兼容旧 localStorage 数据） */
function ensureTenantType(user: AuthUser): AuthUser {
  if (!user.tenant_type) user.tenant_type = "brand";
  if (user.acting_tenant_id === undefined) user.acting_tenant_id = null;
  if (user.agency_scope === undefined) user.agency_scope = null;
  if (user.must_change_password === undefined)
    user.must_change_password = false;
  return user;
}

function clearBrowserSession() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  localStorage.removeItem("auth_store");
  document.cookie = "access_token=; path=/; max-age=0";
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: null,
  loading: false,

  login: async (email, password, options) => {
    const now = Date.now();
    if (now - lastLoginRef.current < 2000) {
      throw new Error("请勿频繁点击登录");
    }
    lastLoginRef.current = now;
    set({ loading: true });
    try {
      const { data } = await api.post(
        "/auth/login",
        {
          email,
          password,
          ...(options?.tenantSlug ? { tenant_slug: options.tenantSlug } : {}),
        },
        {
          headers: { "X-Auth-Delivery": "cookie" },
          skipAuthRefresh: true,
        }
      );
      const { access_token } = data;
      _persistAccessToken(access_token);

      // Decode JWT to extract user info
      const payload = parseJwtPayload(access_token);
      if (!payload) throw new Error("登录令牌无效");
      const user: AuthUser = {
        account_id: String(payload.sub),
        tenant_id: String(payload.tenant_id),
        role: String(payload.role),
        tenant_type: String(payload.tenant_type || "brand"),
        email,
        name: typeof payload.name === "string" ? payload.name : email,
        acting_tenant_id:
          typeof payload.acting_tenant_id === "string"
            ? payload.acting_tenant_id
            : null,
        agency_scope: Array.isArray(payload.scope)
          ? payload.scope.filter(
              (scope): scope is string => typeof scope === "string"
            )
          : null,
        must_change_password: payload.must_change_password === true,
      };
      localStorage.setItem("auth_store", JSON.stringify(user));
      set({ user, token: access_token, loading: false });
    } catch (error) {
      set({ loading: false });
      throw error;
    }
  },

  logout: async () => {
    const legacyRefreshToken = localStorage.getItem("refresh_token");
    try {
      await api.post(
        "/auth/logout",
        legacyRefreshToken ? { refresh_token: legacyRefreshToken } : undefined,
        { skipAuthRefresh: true }
      );
      clearBrowserSession();
      set({ user: null, token: null });
    } catch (error) {
      if (
        axios.isAxiosError(error) &&
        error.response?.data?.code === "LOGOUT_PARTIAL"
      ) {
        clearBrowserSession();
        set({ user: null, token: null });
      }
      throw error;
    }
  },

  clearSession: () => {
    clearBrowserSession();
    set({ user: null, token: null });
  },

  switchAgencyContext: async (clientTenantId: string) => {
    const { data } = await api.post("/agency/switch-context", {
      client_tenant_id: clientTenantId,
    });
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
    const { data } = await api.post("/agency/exit-context");
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

registerAuthInterceptorHandlers({
  silentRefresh: () => useAuthStore.getState().silentRefresh(),
  logout: () => useAuthStore.getState().clearSession(),
});

/** Persist only the short-lived access token; refresh stays HttpOnly. */
function _persistAccessToken(accessToken: string) {
  localStorage.setItem("access_token", accessToken);
  localStorage.removeItem("refresh_token");
  // The backend owns the matching HttpOnly cookie used by Next middleware.
}

/** 切换 agency 上下文时更新 token 和用户状态 */
function _applyTokenUpdate(accessToken: string, updatedUser: AuthUser) {
  localStorage.setItem("auth_store", JSON.stringify(updatedUser));
  localStorage.setItem("access_token", accessToken);
  useAuthStore.setState({ user: updatedUser, token: accessToken });
}

async function _doSilentRefresh(): Promise<string | null> {
  try {
    const previousAccessToken = localStorage.getItem("access_token");
    const previousPayload = previousAccessToken
      ? parseJwtPayload(previousAccessToken)
      : null;
    const previousActingTenant =
      typeof previousPayload?.acting_tenant_id === "string"
        ? previousPayload.acting_tenant_id
        : null;
    const currentRefreshToken = localStorage.getItem("refresh_token");
    const { data } = await api.post(
      "/auth/refresh",
      {
        ...(currentRefreshToken ? { refresh_token: currentRefreshToken } : {}),
      },
      {
        headers: { "X-Auth-Delivery": "cookie" },
        skipAuthRefresh: true,
      }
    );
    const { access_token: baseAccessToken } = data;
    const basePayload = parseJwtPayload(baseAccessToken);
    if (!basePayload) throw new Error("刷新令牌响应无效");
    if (typeof basePayload.acting_tenant_id === "string") {
      throw new Error("刷新令牌未退出原代运营客户上下文");
    }
    _persistAccessToken(baseAccessToken);

    // Refresh intentionally returns the base agency token. Apply that safe
    // context first so failed revalidation exits the client workspace without
    // destroying the still-valid agency session.
    const stored = localStorage.getItem("auth_store");
    const existingUser = useAuthStore.getState().user;
    let baseUser: AuthUser | null = null;
    if (stored || existingUser) {
      const parsedStored = stored ? JSON.parse(stored) : existingUser;
      baseUser = {
        account_id: String(basePayload.sub),
        tenant_id: String(basePayload.tenant_id),
        role: String(basePayload.role),
        tenant_type: String(basePayload.tenant_type || "brand"),
        email: parsedStored.email,
        name: parsedStored.name || parsedStored.email,
        acting_tenant_id: null,
        agency_scope: null,
        must_change_password: basePayload.must_change_password === true,
      };
      _applyTokenUpdate(baseAccessToken, baseUser);
    } else {
      useAuthStore.setState({ token: baseAccessToken });
    }

    if (!previousActingTenant) return baseAccessToken;

    try {
      const { data: switched } = await api.post(
        "/agency/switch-context",
        { client_tenant_id: previousActingTenant },
        {
          headers: { Authorization: `Bearer ${baseAccessToken}` },
          skipAuthRefresh: true,
        }
      );
      const actingAccessToken = switched.access_token;
      const actingPayload = parseJwtPayload(actingAccessToken);
      if (actingPayload?.acting_tenant_id !== previousActingTenant) {
        throw new AgencyContextRevalidationError();
      }
      const liveScope = Array.isArray(switched.scope)
        ? switched.scope.filter(
            (scope: unknown): scope is string => typeof scope === "string"
          )
        : [];
      const currentUser = baseUser ?? useAuthStore.getState().user;
      if (currentUser) {
        _applyTokenUpdate(actingAccessToken, {
          ...currentUser,
          acting_tenant_id: previousActingTenant,
          agency_scope: liveScope,
        });
      } else {
        _persistAccessToken(actingAccessToken);
        useAuthStore.setState({ token: actingAccessToken });
      }
      return actingAccessToken;
    } catch (error) {
      if (error instanceof AgencyContextRevalidationError) throw error;
      throw new AgencyContextRevalidationError();
    }
  } catch (error) {
    if (error instanceof AgencyContextRevalidationError) throw error;
    // refresh 失败，清除登录态
    useAuthStore.getState().clearSession();
    return null;
  }
}

// 同步自动 hydrate：模块加载时立即从 localStorage 恢复状态，
// 确保任何组件首次读取 store 时就能拿到 user 和 token
if (typeof window !== "undefined" && window.localStorage) {
  const token = window.localStorage.getItem("access_token");
  const stored = window.localStorage.getItem("auth_store");
  if (token && stored) {
    try {
      useAuthStore.setState({
        user: ensureTenantType(JSON.parse(stored)),
        token,
      });
    } catch {
      // 自动 hydrate 失败静默忽略，留给运行时 hydrate 处理
    }
  }
}
