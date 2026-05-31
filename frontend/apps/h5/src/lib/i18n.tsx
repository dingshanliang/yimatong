"use client";

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";

export type H5Locale = "zh" | "en";

type TranslationMap = Record<string, string>;

const zh: TranslationMap = {
  "scan.title": "扫码验证",
  "scan.authentic": "正品验证通过",
  "scan.product": "产品信息",
  "scan.scan_count": "扫码次数",
  "scan.first_scan": "首次扫码",
  "scan.claim": "立即领取",
  "scan.claimed": "已领取",
  "scan.points": "我的积分",
  "scan.history": "积分明细",
  "scan.exchange": "立即兑换",
  "scan.exchanged": "兑换成功",
  "scan.insufficient": "积分不足",
  "scan.shop": "积分商城",
  "scan.empty_shop": "暂无可兑换商品",
  "scan.loading": "加载中...",
  "scan.error": "加载失败",
  "points.earn": "收入",
  "points.spend": "支出",
  "points.no_records": "暂无积分记录",
  "points.load_more": "加载更多",
  "points.remaining": "余",
};

const en: TranslationMap = {
  "scan.title": "Scan Verification",
  "scan.authentic": "Authentic Product Verified",
  "scan.product": "Product Info",
  "scan.scan_count": "Scan Count",
  "scan.first_scan": "First Scan",
  "scan.claim": "Claim Now",
  "scan.claimed": "Claimed",
  "scan.points": "My Points",
  "scan.history": "Points History",
  "scan.exchange": "Exchange Now",
  "scan.exchanged": "Exchanged",
  "scan.insufficient": "Insufficient Points",
  "scan.shop": "Points Shop",
  "scan.empty_shop": "No items available",
  "scan.loading": "Loading...",
  "scan.error": "Failed to load",
  "points.earn": "Earned",
  "points.spend": "Spent",
  "points.no_records": "No records yet",
  "points.load_more": "Load More",
  "points.remaining": "Bal",
};

const translations: Record<H5Locale, TranslationMap> = { zh, en };

interface H5I18nValue {
  locale: H5Locale;
  t: (key: string, fallback?: string) => string;
}

const H5I18nContext = createContext<H5I18nValue>({
  locale: "zh",
  t: (key) => key,
});

function detectLocale(): H5Locale {
  if (typeof window === "undefined") return "zh";
  const params = new URLSearchParams(window.location.search);
  const lang = params.get("lang");
  if (lang === "en") return "en";
  const nav = navigator.language || "";
  if (nav.startsWith("zh")) return "zh";
  return "en";
}

export function H5I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocale] = useState<H5Locale>("zh");

  useEffect(() => {
    setLocale(detectLocale());
  }, []);

  const t = useCallback(
    (key: string, fallback?: string) => {
      return translations[locale]?.[key] ?? fallback ?? key;
    },
    [locale]
  );

  return (
    <H5I18nContext.Provider value={{ locale, t }}>
      {children}
    </H5I18nContext.Provider>
  );
}

export function useH5I18n() {
  return useContext(H5I18nContext);
}
