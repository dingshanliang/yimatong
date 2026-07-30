import { CircleAlert } from "lucide-react";

export function FallbackError() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-gray-50 px-4">
      <div className="rounded-2xl bg-white p-8 text-center shadow-sm">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-red-50">
          <CircleAlert className="h-8 w-8 text-red-500" aria-hidden="true" />
        </div>
        <h2 className="text-lg font-semibold text-gray-900">暂时无法加载</h2>
        <p className="mt-1 text-sm text-gray-500">
          网络异常或服务暂不可用，请稍后重试
        </p>
      </div>
    </div>
  );
}
