"use client";

interface DualCodeVerifyProps {
  publicId: string;
  codeType: string;
  isFirstScan: boolean;
  scanCount?: number;
  firstScanTime?: string;
  productVerified?: boolean;
  onScanInner?: () => void;
}

function formatTime(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function DualCodeVerify({
  publicId,
  codeType,
  isFirstScan,
  scanCount,
  firstScanTime,
  productVerified = true,
  onScanInner,
}: DualCodeVerifyProps) {
  const isInner = codeType === "inner";
  const isOuter = codeType === "outer";

  return (
    <div className="rounded-2xl bg-white p-4 shadow-sm">
      {/* 码类型标签 */}
      <div className="flex items-center gap-2 mb-3">
        <span
          className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
            isInner ? "bg-green-100 text-green-700" : "bg-blue-100 text-blue-700"
          }`}
        >
          {isInner ? "内码验真" : "外码引流"}
        </span>
      </div>

      {/* 验证结果 */}
      <div className="flex items-center gap-3">
        <div
          className={`flex h-12 w-12 items-center justify-center rounded-full ${
            isFirstScan && productVerified ? "bg-green-100" : "bg-amber-100"
          }`}
        >
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
              首次扫码时间: {formatTime(firstScanTime)}
            </p>
          )}
        </div>
      </div>

      {/* 码编号 */}
      <div className="mt-3 flex items-center justify-between rounded-xl bg-gray-50 px-3 py-2">
        <span className="text-xs text-gray-500">码编号</span>
        <span className="text-sm font-mono font-medium text-gray-700">{publicId}</span>
      </div>

      {/* 内码专属：引导扫内码验真 */}
      {isInner && isFirstScan && productVerified && (
        <div className="mt-3 rounded-xl bg-green-50 p-3">
          <div className="flex items-start gap-2">
            <span className="text-lg">🔐</span>
            <div>
              <p className="text-sm font-medium text-green-800">验真通过</p>
              <p className="mt-1 text-xs text-green-600">
                此产品内码验证为正品，请放心使用
              </p>
            </div>
          </div>
        </div>
      )}

      {isInner && !isFirstScan && (
        <div className="mt-3 rounded-xl bg-amber-50 p-3">
          <div className="flex items-start gap-2">
            <span className="text-lg">🔄</span>
            <div>
              <p className="text-sm font-medium text-amber-800">重复扫码</p>
              <p className="mt-1 text-xs text-amber-600">
                此内码已被验证过，请确认是否为本人操作
              </p>
            </div>
          </div>
        </div>
      )}

      {/* 外码专属：引导查看产品 + 扫内码 */}
      {isOuter && (
        <div className="mt-3 rounded-xl bg-blue-50 p-3">
          <div className="flex items-start gap-2">
            <span className="text-lg">👆</span>
            <div className="flex-1">
              <p className="text-sm font-medium text-blue-800">查看产品详情</p>
              <p className="mt-1 text-xs text-blue-600">
                找到包装上的内码标签，刮开涂层后扫码即可验真领奖
              </p>
            </div>
          </div>
          {onScanInner && (
            <button
              onClick={onScanInner}
              className="mt-2 w-full rounded-lg bg-blue-600 py-2 text-xs font-medium text-white active:bg-blue-700"
            >
              扫描内码
            </button>
          )}
        </div>
      )}

      {/* 扫码时间轴 */}
      {firstScanTime && (
        <div className="mt-3 flex items-center gap-2 text-xs text-gray-400">
          <div className="flex items-center gap-1">
            <div className="h-1.5 w-1.5 rounded-full bg-green-400" />
            <span>首次 {formatTime(firstScanTime)}</span>
          </div>
          {!isFirstScan && (
            <>
              <div className="h-px flex-1 bg-gray-200" />
              <div className="flex items-center gap-1">
                <div className="h-1.5 w-1.5 rounded-full bg-blue-400" />
                <span>本次</span>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
