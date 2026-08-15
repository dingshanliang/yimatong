import { Suspense } from "react";

import { RedPacketResultClient } from "./ResultClient";

/**
 * 红包领取结果页：领取受理后跳转进入，轮询发放状态直至终态。
 */
export default function RedPacketResultPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-canvas">
          <p className="text-sm text-foreground-tertiary">加载中...</p>
        </div>
      }
    >
      <RedPacketResultClient />
    </Suspense>
  );
}
