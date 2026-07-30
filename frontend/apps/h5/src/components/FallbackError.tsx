import { CircleAlert } from "lucide-react";

export function FallbackError() {
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
      </div>
    </div>
  );
}
