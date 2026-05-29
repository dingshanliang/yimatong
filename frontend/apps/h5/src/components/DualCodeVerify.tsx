"use client";

interface DualCodeVerifyProps {
  publicId: string;
  codeType: string;
  isFirstScan: boolean;
  scanCount?: number;
  firstScanTime?: string;
  productVerified?: boolean;
}

export function DualCodeVerify({
  publicId,
  codeType,
  isFirstScan,
  scanCount,
  firstScanTime,
  productVerified = true,
}: DualCodeVerifyProps) {
  const isInner = codeType === "inner";
  const isOuter = codeType === "outer";

  return (
    <div className="rounded-2xl bg-white p-4 shadow-sm">
      {/* 码类型标签 */}
      <div className="flex items-center gap-2 mb-3">
        <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
          isInner ? "bg-green-100 text-green-700" : "bg-blue-100 text-blue-700"
        }`}>
          {isInner ? "内码验真" : "外码引流"}
        </span>
      </div>

      {/* 验证结果 */}
      <div className="flex items-center gap-3">
        <div className={`flex h-12 w-12 items-center justify-center rounded-full ${
          isFirstScan && productVerified
            ? "bg-green-100"
            : "bg-amber-100"
        }`}>
          <span className="text-2xl">
            {isFirstScan && productVerified ? "✅" : "🔍"}
          </span>
        </div>
        <div>
          <p className="text-base font-semibold text-gray-900">
            {isFirstScan && productVerified
              ? "首次验证 — 正品确认"
              : isFirstScan
              ? "首次扫码"
              : `第 ${scanCount || "?"} 次扫码`}
          </p>
          {firstScanTime && !isFirstScan && (
            <p className="text-xs text-gray-500">
              首次扫码时间: {new Date(firstScanTime).toLocaleString("zh-CN")}
            </p>
          )}
        </div>
      </div>

      {/* 码编号 */}
      <div className="mt-3 flex items-center justify-between rounded-xl bg-gray-50 px-3 py-2">
        <span className="text-xs text-gray-500">码编号</span>
        <span className="text-sm font-mono font-medium text-gray-700">{publicId}</span>
      </div>
    </div>
  );
}
