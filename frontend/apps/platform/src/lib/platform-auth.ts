import { create } from "zustand";
import api, { registerPlatformLogoutHandler } from "./api";

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
    } catch {
      set({ loading: false });
      throw new Error("登录失败，请检查邮箱和密码");
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
