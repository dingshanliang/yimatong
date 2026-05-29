interface BrandHeaderProps {
  name: string;
  logoUrl: string;
  primaryColor?: string;
}

export function BrandHeader({ name, logoUrl, primaryColor }: BrandHeaderProps) {
  const bgColor = primaryColor || "#2563eb";
  return (
    <div className="flex items-center gap-3 px-4 py-4 text-white" style={{ backgroundColor: bgColor }}>
      {logoUrl ? (
        <img src={logoUrl} alt={name} className="h-10 w-10 rounded-full border-2 border-white/30 object-cover" />
      ) : (
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-white/20 text-lg font-bold">
          {name.charAt(0) || "Y"}
        </div>
      )}
      <span className="text-lg font-semibold">{name || "一码通"}</span>
    </div>
  );
}
