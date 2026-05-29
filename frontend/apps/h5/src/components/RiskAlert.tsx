"use client";

interface RiskAlertProps {
  alertType: string;
  detail?: string;
  scanCount?: number;
  detectedCity?: string;
}

export function RiskAlert({
  alertType,
  detail,
  scanCount,
  detectedCity,
}: RiskAlertProps) {
  const isHighRisk = alertType === "multi_location" || alertType === "suspected_copy";

  return (
    <div className={`rounded-2xl border p-4 shadow-sm ${
      isHighRisk ? "border-red-200 bg-red-50" : "border-amber-200 bg-amber-50"
    }`}>
      <div className="flex items-start gap-2">
        <span className="text-xl">{isHighRisk ? "🚨" : "⚠️"}</span>
        <div className="flex-1">
          <p className={`text-sm font-semibold ${isHighRisk ? "text-red-800" : "text-amber-800"}`}>
            {alertType === "multi_location" && "多地扫码预警"}
            {alertType === "suspected_copy" && "疑似复制码"}
            {alertType === "frequency" && "频繁扫码预警"}
            {!["multi_location", "suspected_copy", "frequency"].includes(alertType) && "安全提醒"}
          </p>
          {detail && (
            <p className={`mt-1 text-xs ${isHighRisk ? "text-red-600" : "text-amber-600"}`}>
              {detail}
            </p>
          )}
          {scanCount !== undefined && (
            <p className="mt-1 text-xs text-gray-500">
              此码已被扫 {scanCount} 次
            </p>
          )}
          {detectedCity && (
            <p className="mt-1 text-xs text-gray-500">
              异常扫码地点: {detectedCity}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
