"use client";

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import zhCN from "./zh-CN";
import enUS from "./en-US";

export type Locale = "zh-CN" | "en-US";

type TranslationMap = Record<string, string>;

const translations: Record<Locale, TranslationMap> = {
  "zh-CN": zhCN,
  "en-US": enUS,
};

interface I18nContextValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (key: string, fallback?: string) => string;
}

const I18nContext = createContext<I18nContextValue>({
  locale: "zh-CN",
  setLocale: () => {},
  t: (key) => key,
});

const LOCALE_KEY = "ymt_locale";

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("zh-CN");

  useEffect(() => {
    const saved = localStorage.getItem(LOCALE_KEY) as Locale | null;
    if (saved && translations[saved]) {
      setLocaleState(saved);
    }
  }, []);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    localStorage.setItem(LOCALE_KEY, l);
  }, []);

  const t = useCallback(
    (key: string, fallback?: string) => {
      return translations[locale]?.[key] ?? fallback ?? key;
    },
    [locale]
  );

  return (
    <I18nContext.Provider value={{ locale, setLocale, t }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  return useContext(I18nContext);
}
