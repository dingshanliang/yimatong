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
    <div className="rounded-2xl bg-surface p-4 shadow-sm">
      {/* 码类型标签 */}
      <div className="flex items-center gap-2 mb-3">
        <span
          className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
            isInner ? "bg-success-bg text-success" : "bg-info-bg text-info"
          }`}
        >
          {isInner ? "内码验真" : "外码引流"}
        </span>
      </div>

      {/* 验证结果 */}
      <div className="flex items-center gap-3">
        <div
          className={`flex h-12 w-12 items-center justify-center rounded-full ${
            isFirstScan && productVerified ? "bg-success-bg" : "bg-warning-bg"
          }`}
        >
          <span className="text-2xl">
            {isFirstScan && productVerified ? "✅" : "🔍"}
          </span>
        </div>
        <div>
          <p className="text-base font-semibold text-foreground">
            {isFirstScan && productVerified
              ? "首次验证 — 正品确认"
              : isFirstScan
                ? "首次扫码"
                : `第 ${scanCount || "?"} 次扫码`}
          </p>
          {firstScanTime && !isFirstScan && (
            <p className="text-xs text-foreground-secondary">
              首次扫码时间: {formatTime(firstScanTime)}
            </p>
          )}
        </div>
      </div>

      {/* 码编号 */}
      <div className="mt-3 flex items-center justify-between rounded-xl bg-muted px-3 py-2">
        <span className="text-xs text-foreground-secondary">码编号</span>
        <span className="text-sm font-mono font-medium text-foreground-secondary">
          {publicId}
        </span>
      </div>

      {/* 内码专属：引导扫内码验真 */}
      {isInner && isFirstScan && productVerified && (
        <div className="mt-3 rounded-xl bg-success-bg p-3">
          <div className="flex items-start gap-2">
            <span className="text-lg">🔐</span>
            <div>
              <p className="text-sm font-medium text-success">验真通过</p>
              <p className="mt-1 text-xs text-success">
                此产品内码验证为正品，请放心使用
              </p>
            </div>
          </div>
        </div>
      )}

      {isInner && !isFirstScan && (
        <div className="mt-3 rounded-xl bg-warning-bg p-3">
          <div className="flex items-start gap-2">
            <span className="text-lg">🔄</span>
            <div>
              <p className="text-sm font-medium text-warning">重复扫码</p>
              <p className="mt-1 text-xs text-warning">
                此内码已被验证过，请确认是否为本人操作
              </p>
            </div>
          </div>
        </div>
      )}

      {/* 外码专属：引导查看产品 + 扫内码 */}
      {isOuter && (
        <div className="mt-3 rounded-xl bg-info-bg p-3">
          <div className="flex items-start gap-2">
            <span className="text-lg">👆</span>
            <div className="flex-1">
              <p className="text-sm font-medium text-info">查看产品详情</p>
              <p className="mt-1 text-xs text-info">
                找到包装上的内码标签，刮开涂层后扫码即可验真领奖
              </p>
            </div>
          </div>
          {onScanInner && (
            <button
              onClick={onScanInner}
              className="mt-2 w-full rounded-lg bg-action py-2 text-xs font-medium text-white active:bg-action-active"
            >
              扫描内码
            </button>
          )}
        </div>
      )}

      {/* 扫码时间轴 */}
      {firstScanTime && (
        <div className="mt-3 flex items-center gap-2 text-xs text-foreground-tertiary">
          <div className="flex items-center gap-1">
            <div className="h-1.5 w-1.5 rounded-full bg-success" />
            <span>首次 {formatTime(firstScanTime)}</span>
          </div>
          {!isFirstScan && (
            <>
              <div className="h-px flex-1 bg-base" />
              <div className="flex items-center gap-1">
                <div className="h-1.5 w-1.5 rounded-full bg-action" />
                <span>本次</span>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
