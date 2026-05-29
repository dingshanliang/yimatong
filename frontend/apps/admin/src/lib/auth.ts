import { create } from "zustand";
import api from "./api";

interface AuthUser {
  account_id: string;
  tenant_id: string;
  role: string;
  email: string;
  name: string;
}

interface AuthState {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  hydrate: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: null,
  loading: false,

  login: async (email, password) => {
    set({ loading: true });
    try {
      const { data } = await api.post("/auth/login", { email, password });
      const { access_token, expires_in } = data;
      localStorage.setItem("access_token", access_token);
      // 同时写入 cookie，供 Next.js middleware 在 RSC 导航时读取
      document.cookie = `access_token=${access_token}; path=/; max-age=${expires_in || 900}; SameSite=Lax`;

      // Decode JWT to extract user info
      const payload = JSON.parse(atob(access_token.split(".")[1]));
      const user: AuthUser = {
        account_id: payload.sub,
        tenant_id: payload.tenant_id,
        role: payload.role,
        email,
        name: payload.name || email,
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
    localStorage.removeItem("auth_store");
    document.cookie = "access_token=; path=/; max-age=0";
    set({ user: null, token: null });
  },

  hydrate: () => {
    if (typeof window === "undefined") return;
    const stored = localStorage.getItem("auth_store");
    const token = localStorage.getItem("access_token");
    if (stored && token) {
      try {
        set({ user: JSON.parse(stored), token });
      } catch {
        localStorage.removeItem("auth_store");
        localStorage.removeItem("access_token");
      }
    }
  },
}));

// 同步自动 hydrate：模块加载时立即从 localStorage 恢复状态，
// 确保任何组件首次读取 store 时就能拿到 user 和 token
if (typeof window !== "undefined") {
  const token = localStorage.getItem("access_token");
  const stored = localStorage.getItem("auth_store");
  if (token && stored) {
    try {
      useAuthStore.setState({ user: JSON.parse(stored), token });
    } catch {
      // 自动 hydrate 失败静默忽略，留给运行时 hydrate 处理
    }
  }
}
