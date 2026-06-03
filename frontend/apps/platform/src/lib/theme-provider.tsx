"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { App, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { SWRProvider } from "@/lib/swr-provider";
import { platformThemes, type PlatformThemeMode } from "@/lib/theme";

const THEME_STORAGE_KEY = "platform_theme_mode";

interface PlatformThemeContextValue {
  mode: PlatformThemeMode;
  isDark: boolean;
  setMode: (mode: PlatformThemeMode) => void;
  toggleMode: () => void;
}

const PlatformThemeContext = createContext<PlatformThemeContextValue | null>(null);

function resolveStoredMode(): PlatformThemeMode {
  if (typeof window === "undefined") return "light";

  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;

  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyThemeMode(mode: PlatformThemeMode) {
  if (typeof document === "undefined") return;

  document.documentElement.dataset.theme = mode;
  document.documentElement.style.colorScheme = mode;
}

export function PlatformThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<PlatformThemeMode>("light");

  useEffect(() => {
    const nextMode = resolveStoredMode();
    setModeState(nextMode);
    applyThemeMode(nextMode);
  }, []);

  const setMode = useCallback((nextMode: PlatformThemeMode) => {
    setModeState(nextMode);
    applyThemeMode(nextMode);
    window.localStorage.setItem(THEME_STORAGE_KEY, nextMode);
  }, []);

  const toggleMode = useCallback(() => {
    setMode(mode === "dark" ? "light" : "dark");
  }, [mode, setMode]);

  const value = useMemo<PlatformThemeContextValue>(
    () => ({
      mode,
      isDark: mode === "dark",
      setMode,
      toggleMode,
    }),
    [mode, setMode, toggleMode],
  );

  return (
    <PlatformThemeContext.Provider value={value}>
      <ConfigProvider theme={platformThemes[mode]} locale={zhCN}>
        <SWRProvider>
          <App className="platform-app min-h-screen">{children}</App>
        </SWRProvider>
      </ConfigProvider>
    </PlatformThemeContext.Provider>
  );
}

export function usePlatformTheme() {
  const context = useContext(PlatformThemeContext);
  if (!context) {
    throw new Error("usePlatformTheme must be used within PlatformThemeProvider");
  }
  return context;
}
