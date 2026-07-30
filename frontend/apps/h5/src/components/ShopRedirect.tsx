"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

/** 电商平台类型 */
type Platform = "taobao" | "jd" | "douyin" | "pdd" | "other";

interface ShopItem {
  /** 店铺/平台名称 */
  name: string;
  /** 跳转链接 */
  url: string;
  /** 平台图标 URL（可选，默认使用平台默认图标） */
  icon?: string;
  /** 电商平台 */
  platform: Platform;
}

interface ShopRedirectProps {
  /** 购买渠道列表 */
  shops: ShopItem[];
}

/** 平台对应的默认图标和配色 */
const PLATFORM_STYLES: Record<
  Platform,
  { label: string; bg: string; text: string; border: string }
> = {
  taobao: {
    label: "淘宝",
    bg: "bg-warning-bg",
    text: "text-warning",
    border: "border-warning",
  },
  jd: {
    label: "京东",
    bg: "bg-danger-bg",
    text: "text-danger",
    border: "border-danger",
  },
  douyin: {
    label: "抖音",
    bg: "bg-muted",
    text: "text-foreground-secondary",
    border: "border-base",
  },
  pdd: {
    label: "拼多多",
    bg: "bg-danger-bg",
    text: "text-danger",
    border: "border-danger",
  },
  other: {
    label: "其他",
    bg: "bg-muted",
    text: "text-foreground-secondary",
    border: "border-base",
  },
};

/** 默认显示的平台数量，其余折叠 */
const DEFAULT_VISIBLE_COUNT = 3;

/**
 * 电商跳转组件
 *
 * 展示各电商平台购买入口。默认展示前 3 个平台，
 * 超出部分折叠在"更多购买渠道"中展开。
 */
export function ShopRedirect({ shops }: ShopRedirectProps) {
  const [expanded, setExpanded] = useState(false);

  if (!shops.length) return null;

  const visibleShops = expanded ? shops : shops.slice(0, DEFAULT_VISIBLE_COUNT);
  const hasMore = shops.length > DEFAULT_VISIBLE_COUNT;

  const handleShopClick = (shop: ShopItem) => {
    if (shop.url && /^https?:\/\//i.test(shop.url)) {
      window.open(shop.url, "_blank", "noopener,noreferrer");
    }
  };

  return (
    <section className="space-y-3">
      <h3 className="text-base font-semibold text-foreground">官方购买渠道</h3>

      {/* 平台列表 */}
      <div className="space-y-2">
        {visibleShops.map((shop, idx) => {
          const style = PLATFORM_STYLES[shop.platform] ?? PLATFORM_STYLES.other;
          return (
            <button
              key={`${shop.platform}-${idx}`}
              type="button"
              onClick={() => handleShopClick(shop)}
              className={`flex w-full items-center gap-3 rounded-xl border ${style.border} ${style.bg} p-3 text-left transition-colors active:opacity-80`}
            >
              {/* 平台图标 */}
              {shop.icon ? (
                <img
                  src={shop.icon}
                  alt={shop.name}
                  className="h-8 w-8 shrink-0 rounded-lg object-cover"
                />
              ) : (
                <div
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${style.text} text-xs font-bold`}
                >
                  {style.label.charAt(0)}
                </div>
              )}

              {/* 店铺信息 */}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-foreground">
                    {shop.name}
                  </span>
                  <span
                    className={`rounded-md px-1.5 py-0.5 text-xs font-medium ${style.bg} ${style.text}`}
                  >
                    {style.label}
                  </span>
                </div>
              </div>

              {/* 跳转箭头 */}
              <ChevronRight
                className="h-4 w-4 shrink-0 text-foreground-tertiary"
                aria-hidden="true"
              />
            </button>
          );
        })}
      </div>

      {/* 更多购买渠道折叠 */}
      {hasMore && (
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-base py-2.5 text-sm text-foreground-secondary transition-colors active:bg-muted"
        >
          <span>{expanded ? "收起" : "更多购买渠道"}</span>
          <ChevronDown
            className={`h-4 w-4 transition-transform ${expanded ? "rotate-180" : ""}`}
            aria-hidden="true"
          />
        </button>
      )}
    </section>
  );
}
