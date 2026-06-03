"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { App, ConfigProvider as AntdConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { ConfigProvider as ChartsConfigProvider } from "@ant-design/charts";
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

  const chartsTheme = useMemo(
    () =>
      mode === "dark"
        ? {
            theme: {
              type: "dark" as const,
              color: "#4c9dff",
              category10: ["#4c9dff", "#5ad8a6", "#f6bd16", "#e86452", "#6dc8ec", "#945fb9", "#ff9845", "#1e9493", "#ff99c3", "#269a99"],
              axis: {
                labelFill: "#a7b4c8",
                titleFill: "#a7b4c8",
                gridStroke: "#1e2d45",
                lineStroke: "#26364f",
              },
              legend: {
                itemLabelFill: "#a7b4c8",
              },
            },
          }
        : undefined,
    [mode],
  );

  return (
    <AdminThemeContext.Provider value={value}>
      <AntdConfigProvider theme={adminThemes[mode]} locale={zhCN}>
        <ChartsConfigProvider common={chartsTheme}>
          <SWRProvider>
            <App className="admin-app min-h-screen">{children}</App>
          </SWRProvider>
        </ChartsConfigProvider>
      </AntdConfigProvider>
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
