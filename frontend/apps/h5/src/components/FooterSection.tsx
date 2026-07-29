interface FooterSectionProps {
  branding?: { name?: string; logo_url?: string };
  /** 租户槽位 hide_yimatong_brand：隐藏「由一码通提供技术支持」背书 */
  hideEndorsement?: boolean;
}

export function FooterSection({
  branding,
  hideEndorsement,
}: FooterSectionProps) {
  // 隐藏背书且没有租户品牌信息时，整个页脚不渲染
  if (hideEndorsement && !branding?.name && !branding?.logo_url) {
    return null;
  }

  return (
    <div className="mx-4 mb-6 mt-3 rounded-2xl bg-white p-4 shadow-sm">
      <div className="flex items-center gap-2">
        {branding?.logo_url ? (
          <img
            src={branding.logo_url}
            alt={branding.name ? `${branding.name} logo` : "一码通"}
            className="h-6 w-6 rounded object-cover"
          />
        ) : (
          <div className="flex h-6 w-6 items-center justify-center rounded bg-gray-100 text-xs font-bold text-gray-500">
            {(branding?.name || "Y").charAt(0)}
          </div>
        )}
        <span className="text-sm text-gray-500">
          {hideEndorsement ? branding?.name || "" : "由一码通提供技术支持"}
        </span>
      </div>
    </div>
  );
}
