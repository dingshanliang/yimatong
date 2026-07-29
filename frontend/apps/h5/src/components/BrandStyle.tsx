"use client";

import type { CSSProperties, ReactNode } from "react";

import type { BrandSlots } from "@/lib/brand-theme";
import { brandCssVars } from "@/lib/brand-theme";

interface BrandStyleProps {
  slots: BrandSlots;
  children: ReactNode;
}

/**
 * 租户品牌槽位 → CSS 变量注入（H5 根容器）。
 * 组件只消费 --ymt-* 变量，不做 JS 样式计算（设计体系 h5-branding.md）。
 */
export function BrandStyle({ slots, children }: BrandStyleProps) {
  const vars = brandCssVars(slots) as CSSProperties;
  return (
    <div
      className="min-h-screen"
      style={{ ...vars, backgroundColor: "var(--ymt-brand-page-bg)" }}
      data-brand-theme
    >
      {children}
    </div>
  );
}
