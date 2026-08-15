import { CircleAlert } from "lucide-react";

export function FallbackError({
  onRetry,
  retrying = false,
}: {
  /** 人工重试入口：瞬时故障（网络/服务端）场景传入；业务终态不传 */
  onRetry?: () => void;
  retrying?: boolean;
}) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-canvas px-4">
      <div className="rounded-2xl bg-surface p-8 text-center shadow-sm">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-danger-bg">
          <CircleAlert className="h-8 w-8 text-danger" aria-hidden="true" />
        </div>
        <h2 className="text-lg font-semibold text-foreground">暂时无法加载</h2>
        <p className="mt-1 text-sm text-foreground-secondary">
          网络异常或服务暂不可用，请稍后重试
        </p>
        {onRetry && (
          <button
            type="button"
            disabled={retrying}
            onClick={onRetry}
            className="mt-5 w-full rounded-xl bg-action py-2.5 text-sm font-semibold text-white transition-colors active:bg-action-active disabled:cursor-not-allowed disabled:bg-action/60"
          >
            {retrying ? "正在查验…" : "重新查验"}
          </button>
        )}
      </div>
    </div>
  );
}
