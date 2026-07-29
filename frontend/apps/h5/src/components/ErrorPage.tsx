"use client";

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
      <svg
        className="h-12 w-12"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={1.5}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M9.75 9.75l4.5 4.5m0-4.5l-4.5 4.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
        />
      </svg>
    ),
    title: "查无此码",
    description: "未找到对应的产品信息，请确认二维码是否正确。",
    bg: "bg-gray-50",
    text: "text-gray-600",
    iconBg: "bg-gray-100 text-gray-400",
  },
  not_activated: {
    icon: (
      <svg
        className="h-12 w-12"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={1.5}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z"
        />
      </svg>
    ),
    title: "码未激活",
    description:
      "该产品码尚未激活，产品信息暂未开放查询。如有疑问请联系商家或客服。",
    bg: "bg-amber-50",
    text: "text-amber-700",
    iconBg: "bg-amber-100 text-amber-500",
  },
  frozen: {
    icon: (
      <svg
        className="h-12 w-12"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={1.5}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z"
        />
      </svg>
    ),
    title: "码已冻结",
    description: "该码已被冻结，暂时无法查看。如有疑问请联系客服。",
    bg: "bg-amber-50",
    text: "text-amber-700",
    iconBg: "bg-amber-100 text-amber-500",
  },
  revoked: {
    icon: (
      <svg
        className="h-12 w-12"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={1.5}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M12 9v3.75m0-10.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z"
        />
      </svg>
    ),
    title: "码已作废",
    description: "该码已被作废，相关产品信息已失效。",
    bg: "bg-red-50",
    text: "text-red-700",
    iconBg: "bg-red-100 text-red-500",
  },
  risk_detected: {
    icon: (
      <svg
        className="h-12 w-12"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={1.5}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
        />
      </svg>
    ),
    title: "疑似风险",
    description: "系统检测到异常操作，已暂时限制访问。请联系客服核实。",
    bg: "bg-red-50",
    text: "text-red-700",
    iconBg: "bg-red-100 text-red-500",
  },
};

/**
 * 异常客服页面组件
 *
 * 针对不同错误状态（查无此码/已冻结/已作废/疑似风险）展示对应的错误提示，
 * 包含图标、文案、客服联系方式和重试按钮。
 */
export function ErrorPage({ errorCode, publicId, onRetry }: ErrorPageProps) {
  const config = ERROR_CONFIG[errorCode] ?? ERROR_CONFIG.not_found;

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center px-6 py-12">
      {/* 错误图标 */}
      <div
        className={`flex h-24 w-24 items-center justify-center rounded-full ${config.iconBg}`}
      >
        {config.icon}
      </div>

      {/* 错误标题 */}
      <h2 className="mt-6 text-xl font-bold text-gray-900">{config.title}</h2>

      {/* 错误描述 */}
      <p className={`mt-2 text-center text-sm ${config.text}`}>
        {config.description}
      </p>

      {/* 码 ID（如有） */}
      {publicId && (
        <div className="mt-4 rounded-lg bg-gray-50 px-4 py-2">
          <span className="text-xs text-gray-400">码编号：</span>
          <span className="ml-1 font-mono text-sm text-gray-600">
            {publicId}
          </span>
        </div>
      )}

      {/* 操作按钮 */}
      <div className="mt-8 flex w-full flex-col gap-3">
        {/* 重试按钮 */}
        {onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="w-full rounded-xl bg-blue-600 py-3 text-sm font-semibold text-white transition-colors active:bg-blue-700"
          >
            重新扫码
          </button>
        ) : (
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="w-full rounded-xl bg-blue-600 py-3 text-sm font-semibold text-white transition-colors active:bg-blue-700"
          >
            重新扫码
          </button>
        )}

        {/* 客服联系方式 */}
        <div className="rounded-xl bg-gray-50 p-4">
          <p className="text-center text-xs text-gray-400">
            如需帮助，请联系客服
          </p>
          <div className="mt-2 flex items-center justify-center gap-4">
            <a
              href="tel:400-000-0000"
              className="inline-flex items-center gap-1.5 text-sm text-blue-600"
            >
              <svg
                className="h-4 w-4"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M2.25 6.75c0 8.284 6.716 15 15 15h2.25a2.25 2.25 0 002.25-2.25v-1.372c0-.516-.351-.966-.852-1.091l-4.423-1.106c-.44-.11-.902.055-1.173.417l-.97 1.293c-.282.376-.769.542-1.21.38a12.035 12.035 0 01-7.143-7.143c-.162-.441.004-.928.38-1.21l1.293-.97c.363-.271.527-.734.417-1.173L6.963 3.102a1.125 1.125 0 00-1.091-.852H4.5A2.25 2.25 0 002.25 4.5v2.25z"
                />
              </svg>
              电话咨询
            </a>
            <a
              href="#"
              className="inline-flex items-center gap-1.5 text-sm text-green-600"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor">
                <path d="M7.574 4.3c-3.913 0-7.074 2.6-7.074 5.8 0 1.86 1.05 3.54 2.7 4.66l-.67 2.02a.28.28 0 00.27.37c.05 0 .1-.02.15-.05l2.37-1.4a8.5 8.5 0 002.25.3c.2 0 .39 0 .58-.02a6.07 6.07 0 01-.22-1.58c0-3.35 3.06-6.07 6.82-6.07.22 0 .44.01.66.03C15.17 5.88 11.66 4.3 7.57 4.3z" />
              </svg>
              在线客服
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
