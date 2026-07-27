"use client";

/** 验真状态类型 */
type VerifyStatusType = "first_scan" | "repeat_scan" | "invalid";

interface VerifyStatusProps {
  /** 验真状态 */
  status: VerifyStatusType;
  /** 累计扫码次数 */
  scanCount?: number;
  /** 首次扫码时间（ISO 字符串），仅 first_scan 状态展示 */
  firstScanTime?: string;
  /** 最近查验时间（ISO 字符符串），repeat_scan 状态展示（yimatong-zgb1.5 AC1） */
  lastScanTime?: string;
}

/** 状态对应的视觉配置 */
const STATUS_CONFIG: Record<
  VerifyStatusType,
  {
    icon: React.ReactNode;
    title: string;
    description: string;
    bg: string;
    border: string;
    text: string;
    badge: string;
    badgeText: string;
  }
> = {
  first_scan: {
    icon: (
      <svg
        className="h-8 w-8"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={2}
        aria-hidden="true"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z"
        />
      </svg>
    ),
    title: "验证通过",
    // yimatong-zgb1.4：轻防伪结论 + 可信依据。不声称绝对真伪，
    // 只说明这是平台首次记录的查验、产品资料来自品牌方权威备案。
    description:
      "这是本码首次查验。溯源资料来自品牌方权威备案，可作为正品依据。",
    bg: "bg-green-50",
    border: "border-green-200",
    text: "text-green-700",
    badge: "bg-green-600",
    badgeText: "首次验证",
  },
  repeat_scan: {
    icon: (
      <svg
        className="h-8 w-8"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={2}
        aria-hidden="true"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
        />
      </svg>
    ),
    title: "重复查验",
    // yimatong-zgb1.4：重复查验不是异常，不引发恐慌。展示首查时间 + 累计次数即可。
    description: "本码已被查验过，下方为首次查验时间与累计次数。",
    bg: "bg-amber-50",
    border: "border-amber-200",
    text: "text-amber-700",
    badge: "bg-amber-600",
    badgeText: "重复查验",
  },
  invalid: {
    icon: (
      <svg
        className="h-8 w-8"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={2}
        aria-hidden="true"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M9.75 9.75l4.5 4.5m0-4.5l-4.5 4.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
        />
      </svg>
    ),
    title: "验证失败",
    description: "该码无效或已被篡改，请谨慎对待此产品。",
    bg: "bg-red-50",
    border: "border-red-200",
    text: "text-red-700",
    badge: "bg-red-600",
    badgeText: "无效",
  },
};

/**
 * 格式化时间为中文日期
 */
function formatDateTime(isoString?: string): string {
  if (!isoString) return "";
  const d = new Date(isoString);
  if (isNaN(d.getTime())) return isoString;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * 验真状态组件
 *
 * 展示扫码验证结果：首次验证（绿色）、重复扫码（黄色）、无效码（红色）。
 * 显示累计扫码次数和首次扫码时间。
 */
export function VerifyStatus({
  status,
  scanCount,
  firstScanTime,
  lastScanTime,
}: VerifyStatusProps) {
  const config = STATUS_CONFIG[status] ?? STATUS_CONFIG.invalid;

  return (
    <div
      className={`rounded-2xl border ${config.border} ${config.bg} p-4 shadow-sm`}
      role="status"
      aria-label={
        status === "first_scan"
          ? "首次查验验证"
          : status === "repeat_scan"
            ? "重复查验提醒"
            : "验证失败"
      }
    >
      {/* 状态标识 */}
      <div className="flex items-center gap-3">
        <div className={`shrink-0 ${config.text}`}>{config.icon}</div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-base font-semibold text-gray-900">
              {config.title}
            </h3>
            <span
              className={`rounded-md ${config.badge} px-2 py-0.5 text-xs font-medium text-white`}
            >
              {config.badgeText}
            </span>
          </div>
          <p className={`mt-1 text-sm ${config.text}`}>{config.description}</p>
        </div>
      </div>

      {/* 扫码统计信息
       * yimatong-zgb1.5 AC1：重复查验展示"最近"时间，不再展示首次时间。
       * first_scan 状态展示首次时间；repeat_scan 状态展示最近时间。 */}
      {(scanCount !== undefined || firstScanTime || lastScanTime) && (
        <div className="mt-3 flex gap-4 border-t border-white/50 pt-3">
          {scanCount !== undefined && (
            <div className="flex items-center gap-1.5 text-sm text-gray-600">
              <svg
                className="h-4 w-4 text-gray-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M15 12H9m12 0a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
              <span>
                累计查验 <strong>{scanCount}</strong> 次
              </span>
            </div>
          )}
          {/* first_scan 状态：展示首次查验时间 */}
          {status === "first_scan" && firstScanTime && (
            <div className="flex items-center gap-1.5 text-sm text-gray-600">
              <svg
                className="h-4 w-4 text-gray-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
              <span>首次 {formatDateTime(firstScanTime)}</span>
            </div>
          )}
          {/* repeat_scan 状态：展示最近查验时间（不展示首次，AC1） */}
          {status === "repeat_scan" && lastScanTime && (
            <div className="flex items-center gap-1.5 text-sm text-gray-600">
              <svg
                className="h-4 w-4 text-gray-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
              <span>最近 {formatDateTime(lastScanTime)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
