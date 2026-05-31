"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { App, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { SWRProvider } from "@/lib/swr-provider";
import { adminThemes, type AdminThemeMode } from "@/lib/theme";

const THEME_STORAGE_KEY = "admin_theme_mode";

interface AdminThemeContextValue {
  mode: AdminThemeMode;
  isDark: boolean;
  setMode: (mode: AdminThemeMode) => void;
  toggleMode: () => void;
}

const AdminThemeContext = createContext<AdminThemeContextValue | null>(null);

function resolveStoredMode(): AdminThemeMode {
  if (typeof window === "undefined") return "light";

  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;

  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyThemeMode(mode: AdminThemeMode) {
  if (typeof document === "undefined") return;

  document.documentElement.dataset.theme = mode;
  document.documentElement.style.colorScheme = mode;
}

export function AdminThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<AdminThemeMode>("light");

  useEffect(() => {
    const nextMode = resolveStoredMode();
    setModeState(nextMode);
    applyThemeMode(nextMode);
  }, []);

  const setMode = useCallback((nextMode: AdminThemeMode) => {
    setModeState(nextMode);
    applyThemeMode(nextMode);
    window.localStorage.setItem(THEME_STORAGE_KEY, nextMode);
  }, []);

  const toggleMode = useCallback(() => {
    setMode(mode === "dark" ? "light" : "dark");
  }, [mode, setMode]);

  const value = useMemo<AdminThemeContextValue>(
    () => ({
      mode,
      isDark: mode === "dark",
      setMode,
      toggleMode,
    }),
    [mode, setMode, toggleMode],
  );

  return (
    <AdminThemeContext.Provider value={value}>
      <ConfigProvider theme={adminThemes[mode]} locale={zhCN}>
        <SWRProvider>
          <App className="admin-app min-h-screen">{children}</App>
        </SWRProvider>
      </ConfigProvider>
    </AdminThemeContext.Provider>
  );
}

export function useAdminTheme() {
  const context = useContext(AdminThemeContext);
  if (!context) {
    throw new Error("useAdminTheme must be used within AdminThemeProvider");
  }
  return context;
}
