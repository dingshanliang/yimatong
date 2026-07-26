import { create } from "zustand";
import api, { registerPlatformLogoutHandler } from "./api";

interface PlatformAuthState {
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  hydrate: () => void;
}

export const usePlatformAuth = create<PlatformAuthState>((set) => ({
  token: null,
  loading: false,

  login: async (email, password) => {
    set({ loading: true });
    try {
      const { data } = await api.post("/platform/auth/login", { email, password });
      const { access_token } = data;

      localStorage.setItem("platform_access_token", access_token);
      // cookie 供 Next.js middleware 读取
      const secure = window.location.protocol === "https:" ? "; Secure" : "";
      document.cookie = `platform_access_token=${access_token}; path=/; max-age=${30 * 24 * 3600}; SameSite=Lax${secure}`;

      set({ token: access_token, loading: false });
    } catch {
      set({ loading: false });
      throw new Error("登录失败，请检查邮箱和密码");
    }
  },

  logout: () => {
    localStorage.removeItem("platform_access_token");
    document.cookie = "platform_access_token=; path=/; max-age=0";
    set({ token: null });
  },

  hydrate: () => {
    if (typeof window === "undefined") return;
    const token = localStorage.getItem("platform_access_token");
    if (token) {
      set({ token });
    }
  },
}));

registerPlatformLogoutHandler(() => usePlatformAuth.getState().logout());

// 同步自动 hydrate
if (typeof window !== "undefined") {
  const token = localStorage.getItem("platform_access_token");
  if (token) {
    usePlatformAuth.setState({ token });
  }
}
