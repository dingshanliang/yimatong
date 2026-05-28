"use client";

interface PrivateDomainButton {
  /** 按钮文案 */
  label: string;
  /** 跳转动作类型 */
  action: "wecom_link" | "mini_program" | "external_shop" | "wechat_official";
  /** 配置 ID（如企微客服 ID、小程序 appid 等） */
  config_id?: string;
  /** 跳转链接 */
  url?: string;
}

interface PrivateDomainButtonsProps {
  /** 按钮配置列表 */
  buttons: PrivateDomainButton[];
}

/** 动作类型对应的图标和颜色 */
const ACTION_STYLES: Record<
  PrivateDomainButton["action"],
  { icon: React.ReactNode; bg: string; text: string }
> = {
  wecom_link: {
    icon: (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
        <path d="M8.691 2.188C3.891 2.188 0 5.476 0 9.53c0 2.212 1.17 4.203 3.002 5.55a.59.59 0 01.213.665l-.39 1.48c-.019.07-.048.141-.048.213 0 .163.13.295.29.295a.326.326 0 00.167-.054l1.903-1.114a.864.864 0 01.717-.098 10.16 10.16 0 002.837.403c.276 0 .543-.027.811-.05a6.42 6.42 0 01-.246-1.79c0-3.558 3.215-6.467 7.177-6.467.246 0 .484.021.724.042C16.622 4.975 13.004 2.188 8.691 2.188zm-2.6 4.408c.56 0 1.015.46 1.015 1.03s-.454 1.03-1.015 1.03c-.56 0-1.014-.46-1.014-1.03s.453-1.03 1.014-1.03zm5.242 0c.56 0 1.015.46 1.015 1.03s-.454 1.03-1.015 1.03c-.56 0-1.014-.46-1.014-1.03s.453-1.03 1.014-1.03zm4.257 3.218c-3.412 0-6.19 2.455-6.19 5.475 0 3.02 2.778 5.474 6.19 5.474a7.79 7.79 0 002.168-.31.678.678 0 01.558.076l1.468.86a.252.252 0 00.13.041.227.227 0 00.224-.227c0-.056-.022-.11-.037-.164l-.3-1.143a.462.462 0 01.165-.514C20.93 18.606 21.89 17.013 21.89 15.29c0-3.02-2.778-5.476-6.3-5.476zm-2.35 3.368c.434 0 .787.357.787.796s-.353.796-.787.796-.787-.357-.787-.796.353-.796.787-.796zm4.7 0c.434 0 .788.357.788.796s-.354.796-.788.796-.786-.357-.786-.796.352-.796.787-.796z" />
      </svg>
    ),
    bg: "bg-green-50",
    text: "text-green-700",
  },
  mini_program: {
    icon: (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
        <path d="M8.9 2c1.1 0 2.3.3 3.3.8C15.8 1.5 19.5 3 21 6.1c1.4 2.9.3 6.3-2.5 8.3l-.1.1c-.2.2-.5.3-.7.5-.4.3-.8.6-1.2 1-.8.8-1.5 1.6-2 2.5-.2.3-.3.6-.5.9-.1.1-.1.2-.2.3-.3.4-.8.6-1.3.5-.5-.1-.9-.5-1-.9-.4-1.5-1-2.9-1.9-4.1-.4-.5-.8-1-1.3-1.4l-.3-.3C5.1 11.8 4 9.4 4.5 7c.5-2.6 2.5-4.5 5-4.9.4-.1.9-.1 1.4-.1zm0 2c-.3 0-.7 0-1 .1-1.8.3-3.2 1.6-3.5 3.4-.4 1.8.5 3.6 2.2 5l.4.3c.6.5 1.1 1.1 1.6 1.7.8 1.1 1.5 2.3 1.9 3.6.5-.8 1.1-1.5 1.8-2.2.5-.5 1-.9 1.5-1.2.3-.2.5-.4.8-.5l.1-.1c2-1.4 2.8-3.7 1.9-5.7-1-2.2-3.6-3.3-5.8-2.6-.3.1-.6.2-.9.3C9.4 4.4 9.1 4 8.9 4z" />
      </svg>
    ),
    bg: "bg-blue-50",
    text: "text-blue-700",
  },
  external_shop: {
    icon: (
      <svg
        className="h-5 w-5"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={2}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"
        />
      </svg>
    ),
    bg: "bg-orange-50",
    text: "text-orange-700",
  },
  wechat_official: {
    icon: (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
        <path d="M7.574 4.3c-3.913 0-7.074 2.6-7.074 5.8 0 1.86 1.05 3.54 2.7 4.66l-.67 2.02a.28.28 0 00.27.37c.05 0 .1-.02.15-.05l2.37-1.4a8.5 8.5 0 002.25.3c.2 0 .39 0 .58-.02a6.07 6.07 0 01-.22-1.58c0-3.35 3.06-6.07 6.82-6.07.22 0 .44.01.66.03C15.17 5.88 11.66 4.3 7.57 4.3zm-2.2 3.45a.95.95 0 110 1.9.95.95 0 010-1.9zm4.4 0a.95.95 0 110 1.9.95.95 0 010-1.9zM15.5 9.5c-3.2 0-5.8 2.24-5.8 5s2.6 5 5.8 5c.65 0 1.28-.09 1.87-.26l1.68.99a.23.23 0 00.13.04.23.23 0 00.22-.3l-.48-1.44C20.2 17.67 21 16.4 21 14.5c0-2.76-2.5-5-5.5-5zm-1.9 2.85a.8.8 0 110 1.6.8.8 0 010-1.6zm3.8 0a.8.8 0 110 1.6.8.8 0 010-1.6z" />
      </svg>
    ),
    bg: "bg-green-50",
    text: "text-green-700",
  },
};

/**
 * 私域跳转按钮组
 *
 * 展示一组私域入口按钮，支持企微客服、小程序、外部商城、公众号等跳转类型。
 * 每种类型对应不同的图标和配色。
 */
export function PrivateDomainButtons({ buttons }: PrivateDomainButtonsProps) {
  if (!buttons.length) return null;

  const handleClick = (btn: PrivateDomainButton) => {
    if (btn.url) {
      window.open(btn.url, "_blank", "noopener,noreferrer");
    }
  };

  return (
    <div className="space-y-3">
      <h3 className="text-base font-semibold text-gray-900">关注我们</h3>
      <div className="flex flex-wrap gap-2.5">
        {buttons.map((btn, idx) => {
          const s = ACTION_STYLES[btn.action];
          return (
            <button
              key={`${btn.action}-${idx}`}
              type="button"
              onClick={() => handleClick(btn)}
              className={`inline-flex items-center gap-2 rounded-xl ${s.bg} ${s.text} px-4 py-2.5 text-sm font-medium transition-colors active:opacity-80`}
            >
              {s.icon}
              {btn.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
