"use client";

import {
  Clock,
  CircleX,
  Lock,
  Phone,
  ShieldAlert,
  TriangleAlert,
} from "lucide-react";

import { WeChatIcon } from "@/components/icons/WeChatGlyphs";
import {
  DEFAULT_SUPPORT_PHONE,
  DEFAULT_SUPPORT_WECOM_URL,
} from "@/lib/brand-theme";

/** 错误码类型 */
type ErrorCode =
  "not_found" | "not_activated" | "frozen" | "revoked" | "risk_detected";

interface ErrorPageProps {
  /** 错误类型 */
  errorCode: ErrorCode;
  /** 码的公开 ID（用于联系客服时提供） */
  publicId?: string;
  /** 重试回调 */
  onRetry?: () => void;
  /** false 时完全不渲染重试按钮（业务终态场景） */
  showRetry?: boolean;
  /** 客服电话（品牌定制槽位，未传回退平台默认） */
  supportPhone?: string;
  /** 企微客服链接（品牌定制槽位；空值不渲染链接） */
  supportWecomUrl?: string;
}

/** 仅接受 tel: 安全字符，防止注入。 */
function safeTelHref(phone: string): string {
  return `tel:${phone.replace(/[^0-9+\-*()]/g, "")}`;
}

/** 仅接受 https 链接。 */
function safeHttpsHref(url: string): string | null {
  return /^https:\/\/[^\s]+$/i.test(url) ? url : null;
}

/** 错误类型对应的视觉配置 */
const ERROR_CONFIG: Record<
  ErrorCode,
  {
    icon: React.ReactNode;
    title: string;
    description: string;
    bg: string;
    text: string;
    iconBg: string;
  }
> = {
  not_found: {
    icon: (
      <CircleX className="h-12 w-12" strokeWidth={1.5} aria-hidden="true" />
    ),
    title: "查无此码",
    description: "未找到对应的产品信息，请确认二维码是否正确。",
    bg: "bg-muted",
    text: "text-foreground-secondary",
    iconBg: "bg-muted text-foreground-tertiary",
  },
  not_activated: {
    icon: <Clock className="h-12 w-12" strokeWidth={1.5} aria-hidden="true" />,
    title: "码未激活",
    description:
      "该产品码尚未激活，产品信息暂未开放查询。如有疑问请联系商家或客服。",
    bg: "bg-warning-bg",
    text: "text-warning",
    iconBg: "bg-warning text-on-action",
  },
  frozen: {
    icon: <Lock className="h-12 w-12" strokeWidth={1.5} aria-hidden="true" />,
    title: "码已冻结",
    description: "该码已被冻结，暂时无法查看。如有疑问请联系客服。",
    bg: "bg-warning-bg",
    text: "text-warning",
    iconBg: "bg-warning text-on-action",
  },
  revoked: {
    icon: (
      <ShieldAlert className="h-12 w-12" strokeWidth={1.5} aria-hidden="true" />
    ),
    title: "码已作废",
    description: "该码已被作废，相关产品信息已失效。",
    bg: "bg-danger-bg",
    text: "text-danger",
    iconBg: "bg-danger text-on-action",
  },
  risk_detected: {
    icon: (
      <TriangleAlert
        className="h-12 w-12"
        strokeWidth={1.5}
        aria-hidden="true"
      />
    ),
    title: "疑似风险",
    description: "系统检测到异常操作，已暂时限制访问。请联系客服核实。",
    bg: "bg-danger-bg",
    text: "text-danger",
    iconBg: "bg-danger text-on-action",
  },
};

/**
 * 异常客服页面组件
 *
 * 针对不同错误状态（查无此码/已冻结/已作废/疑似风险）展示对应的错误提示，
 * 包含图标、文案、客服联系方式和重试按钮。
 */
export function ErrorPage({
  errorCode,
  publicId,
  onRetry,
  showRetry = true,
  supportPhone = DEFAULT_SUPPORT_PHONE,
  supportWecomUrl = DEFAULT_SUPPORT_WECOM_URL,
}: ErrorPageProps) {
  const config = ERROR_CONFIG[errorCode] ?? ERROR_CONFIG.not_found;
  const telHref = safeTelHref(supportPhone);
  const wecomHref = safeHttpsHref(supportWecomUrl);

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center px-6 py-12">
      {/* 错误图标 */}
      <div
        className={`flex h-24 w-24 items-center justify-center rounded-full ${config.iconBg}`}
      >
        {config.icon}
      </div>

      {/* 错误标题 */}
      <h2 className="mt-6 text-xl font-bold text-foreground">{config.title}</h2>

      {/* 错误描述 */}
      <p className={`mt-2 text-center text-sm ${config.text}`}>
        {config.description}
      </p>

      {/* 码 ID（如有） */}
      {publicId && (
        <div className="mt-4 rounded-lg bg-muted px-4 py-2">
          <span className="text-xs text-foreground-tertiary">码编号：</span>
          <span className="ml-1 font-mono text-sm text-foreground-secondary">
            {publicId}
          </span>
        </div>
      )}

      {/* 操作按钮 */}
      <div className="mt-8 flex w-full flex-col gap-3">
        {/* 重试按钮：业务终态（作废/未激活等）重试无意义，不展示 */}
        {showRetry && onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="w-full rounded-xl bg-action py-3 text-sm font-semibold text-white transition-colors active:bg-action-active"
          >
            重新扫码
          </button>
        ) : showRetry ? (
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="w-full rounded-xl bg-action py-3 text-sm font-semibold text-white transition-colors active:bg-action-active"
          >
            重新扫码
          </button>
        ) : null}

        {/* 客服联系方式：品牌定制槽位（kc6d.4），未配置回退平台默认 */}
        <div className="rounded-xl bg-muted p-4">
          <p className="text-center text-xs text-foreground-tertiary">
            如需帮助，请联系客服
          </p>
          <div className="mt-2 flex items-center justify-center gap-4">
            <a
              href={telHref}
              className="inline-flex items-center gap-1.5 text-sm text-link"
            >
              <Phone className="h-4 w-4" aria-hidden="true" />
              电话咨询
            </a>
            {wecomHref && (
              <a
                href={wecomHref}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 text-sm text-success"
              >
                <WeChatIcon className="h-4 w-4" size={16} />
                在线客服
              </a>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
