"use client";

interface RiskAlertProps {
  alertType: string;
  detail?: string;
  scanCount?: number;
  detectedCity?: string;
  contactUrl?: string;
}

const RECOMMENDATIONS: Record<string, string> = {
  multi_location: "如非本人操作，建议联系品牌客服核实。",
  suspected_copy: "此码可能被复制，请核对产品包装完整性。",
  frequency: "如非本人操作，可忽略此提示。",
};

export function RiskAlert({
  alertType,
  detail,
  scanCount,
  detectedCity,
  contactUrl,
}: RiskAlertProps) {
  const isHighRisk =
    alertType === "multi_location" || alertType === "suspected_copy";
  const recommendation = RECOMMENDATIONS[alertType];

  return (
    <div
      className={`rounded-2xl border p-4 shadow-sm ${
        isHighRisk
          ? "border-danger bg-danger-bg"
          : "border-warning bg-warning-bg"
      }`}
    >
      <div className="flex items-start gap-2">
        <span className="text-xl">{isHighRisk ? "🚨" : "⚠️"}</span>
        <div className="flex-1">
          <p
            className={`text-sm font-semibold ${
              isHighRisk ? "text-danger" : "text-warning"
            }`}
          >
            {alertType === "multi_location" && "多地扫码预警"}
            {alertType === "suspected_copy" && "疑似复制码"}
            {alertType === "frequency" && "频繁扫码预警"}
            {!["multi_location", "suspected_copy", "frequency"].includes(
              alertType
            ) && "安全提醒"}
          </p>
          {detail && (
            <p
              className={`mt-1 text-xs ${isHighRisk ? "text-danger" : "text-warning"}`}
            >
              {detail}
            </p>
          )}
          {scanCount !== undefined && (
            <p className="mt-1 text-xs text-foreground-secondary">
              此码已被扫 {scanCount} 次
            </p>
          )}
          {detectedCity && (
            <p className="mt-1 text-xs text-foreground-secondary">
              异常扫码地点: {detectedCity}
            </p>
          )}
        </div>
      </div>

      {/* 推荐操作 */}
      {recommendation && (
        <div
          className={`mt-3 rounded-xl border p-3 ${
            isHighRisk
              ? "border-danger bg-white/60"
              : "border-warning bg-white/60"
          }`}
        >
          <p className="text-xs text-foreground-secondary">
            <span className="font-medium">建议：</span>
            {recommendation}
          </p>
        </div>
      )}

      {contactUrl && (
        <a
          href={contactUrl}
          className="mt-3 block w-full rounded-xl border border-base bg-surface py-2.5 text-center text-sm font-medium text-foreground-secondary active:bg-muted"
        >
          联系客服
        </a>
      )}
    </div>
  );
}
