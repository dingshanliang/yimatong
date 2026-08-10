import { safePublicUrl } from "../lib/public-url";

interface FooterSectionProps {
  branding?: { name?: string; logo_url?: string };
  /** 租户槽位 hide_yimatong_brand：隐藏「由一码通提供技术支持」背书 */
  hideEndorsement?: boolean;
}

export function FooterSection({
  branding,
  hideEndorsement,
}: FooterSectionProps) {
  const persistedLogoUrl = branding?.logo_url;
  const logoUrl =
    typeof persistedLogoUrl === "string"
      ? safePublicUrl(persistedLogoUrl)
      : null;
  const brandName = branding?.name;

  // 隐藏背书且没有租户品牌信息时，整个页脚不渲染
  if (hideEndorsement && !brandName && !logoUrl) {
    return null;
  }

  return (
    <div className="mx-4 mb-6 mt-3 rounded-2xl bg-surface p-4 shadow-sm">
      <div className="flex items-center gap-2">
        {logoUrl ? (
          <img
            src={logoUrl}
            alt={brandName ? `${brandName} logo` : "一码通"}
            className="h-6 w-6 rounded object-cover"
          />
        ) : (
          <div className="flex h-6 w-6 items-center justify-center rounded bg-muted text-xs font-bold text-foreground-secondary">
            {(brandName || "Y").charAt(0)}
          </div>
        )}
        <span className="text-sm text-foreground-secondary">
          {hideEndorsement ? brandName || "" : "由一码通提供技术支持"}
        </span>
      </div>
    </div>
  );
}
