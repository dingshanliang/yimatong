"use client";

import { ExternalLink } from "lucide-react";

import { MiniProgramIcon, WeChatIcon } from "@/components/icons/WeChatGlyphs";

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
    icon: <WeChatIcon className="h-5 w-5" size={20} />,
    bg: "bg-success-bg",
    text: "text-success",
  },
  mini_program: {
    icon: <MiniProgramIcon className="h-5 w-5" size={20} />,
    bg: "bg-info-bg",
    text: "text-info",
  },
  external_shop: {
    icon: (
      <ExternalLink className="h-5 w-5" strokeWidth={2} aria-hidden="true" />
    ),
    bg: "bg-warning-bg",
    text: "text-warning",
  },
  wechat_official: {
    icon: <WeChatIcon className="h-5 w-5" size={20} />,
    bg: "bg-success-bg",
    text: "text-success",
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

  const isWeChat =
    typeof navigator !== "undefined" &&
    /MicroMessenger/i.test(navigator.userAgent);

  const handleClick = (btn: PrivateDomainButton) => {
    const url = btn.url;
    if (!url || !/^https?:\/\//i.test(url)) return;
    if (isWeChat) {
      // 微信内打开外链提示用户复制到浏览器
      if (navigator.clipboard) {
        navigator.clipboard.writeText(url).then(() => {
          alert("链接已复制，请在浏览器中打开");
        });
      }
      return;
    }
    window.open(url, "_blank", "noopener,noreferrer");
  };

  return (
    <div className="space-y-3">
      <h3 className="text-base font-semibold text-foreground">关注我们</h3>
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
