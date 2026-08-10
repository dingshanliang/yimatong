"use client";

import { useState } from "react";

import { safePublicUrl } from "../lib/public-url";

interface BrandHeaderProps {
  name: string;
  logoUrl: string;
  primaryColor?: string;
}

export function BrandHeader({ name, logoUrl, primaryColor }: BrandHeaderProps) {
  // 缺省消费品牌槽位注入的 --ymt-color-action（BrandStyle 根容器提供）
  const bgColor = primaryColor || "var(--ymt-color-action, #15803d)";
  const [logoError, setLogoError] = useState(false);
  const safeLogoUrl =
    typeof logoUrl === "string" ? safePublicUrl(logoUrl) : null;

  return (
    <div
      className="flex items-center gap-3 px-4 py-4 text-white"
      style={{ backgroundColor: bgColor }}
    >
      {safeLogoUrl && !logoError ? (
        <img
          src={safeLogoUrl}
          alt={`${name} logo`}
          className="h-10 w-10 rounded-full border-2 border-white/30 object-cover"
          onError={() => setLogoError(true)}
        />
      ) : (
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-white/20 text-lg font-bold">
          {(name || "品")[0]}
        </div>
      )}
      <span className="text-lg font-semibold">{name || "一码通"}</span>
    </div>
  );
}
