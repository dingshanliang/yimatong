"use client";

import { useState } from "react";

interface BrandHeaderProps {
  name: string;
  logoUrl: string;
  primaryColor?: string;
}

export function BrandHeader({ name, logoUrl, primaryColor }: BrandHeaderProps) {
  const bgColor = primaryColor || "#2563eb";
  const [logoError, setLogoError] = useState(false);

  return (
    <div className="flex items-center gap-3 px-4 py-4 text-white" style={{ backgroundColor: bgColor }}>
      {logoUrl && !logoError ? (
        <img
          src={logoUrl}
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
