// 微信生态品牌图标（微信/企微/小程序）。
// lucide-react 无对应品牌字形，按 components.md「品牌 Logo 除外」豁免，
// 作为 H5 唯一的内联 SVG 收口点；颜色一律 currentColor，由语义 token 着色。

type IconProps = {
  className?: string;
  size?: number;
};

export function WeChatIcon({ className, size = 24 }: IconProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M8.691 2C4.768 2 1.6 4.59 1.6 7.79c0 1.84 1.05 3.47 2.68 4.55L3.6 14.6l2.32-1.16c.62.15 1.27.23 1.94.25a5.6 5.6 0 0 1-.18-1.4c0-3.06 2.96-5.54 6.62-5.54.2 0 .39 0 .58.03C14.2 3.85 11.74 2 8.69 2Zm-2.4 3.2a.9.9 0 1 1 0 1.8.9.9 0 0 1 0-1.8Zm4.8 0a.9.9 0 1 1 0 1.8.9.9 0 0 1 0-1.8Z" />
      <path d="M22.4 12.29c0-2.66-2.66-4.82-5.94-4.82s-5.94 2.16-5.94 4.82 2.66 4.82 5.94 4.82c.7 0 1.36-.1 1.98-.28L20.4 18l-.55-1.7c1.54-.86 2.55-2.28 2.55-3.92Zm-7.94-1.2a.76.76 0 1 1 0 1.52.76.76 0 0 1 0-1.52Zm3.8 0a.76.76 0 1 1 0 1.52.76.76 0 0 1 0-1.52Z" />
    </svg>
  );
}

export function MiniProgramIcon({ className, size = 24 }: IconProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M9 14.5c0 .83.67 1.5 1.5 1.5h2a1.5 1.5 0 0 0 0-3h-2A1.5 1.5 0 0 1 9 9.5C9 8.67 9.67 8 10.5 8h2" />
      <path d="M12 6.5v11" />
    </svg>
  );
}
