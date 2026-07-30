"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

/**
 * 将分转换为元的显示字符串
 * 1 元 = 100 分
 */
function fenToYuan(fen: number): string {
  const yuan = fen / 100;
  if (yuan === Math.floor(yuan)) {
    return yuan.toFixed(0);
  }
  return yuan.toFixed(2);
}

/** 前端展示状态（从后端状态映射） */
type DisplayStatus = "success" | "pending" | "failed";

/** 各状态对应的配置 */
const STATUS_CONFIG: Record<
  DisplayStatus,
  {
    title: string;
    subtitle: string;
    gradient: string;
    amountColor: string;
    icon: string;
    detailText: string;
  }
> = {
  success: {
    title: "领取成功",
    subtitle: "恭喜领取",
    gradient: "from-danger-bg via-warning-bg to-warning-bg",
    amountColor: "text-danger",
    icon: "🧧",
    detailText: "已到微信零钱，请注意查收",
  },
  pending: {
    title: "处理中",
    subtitle: "红包发放中",
    gradient: "from-warning-bg via-warning-bg to-warning-bg",
    amountColor: "text-warning",
    icon: "⏳",
    detailText: "处理中，请稍后查看微信零钱明细",
  },
  failed: {
    title: "领取失败",
    subtitle: "很抱歉",
    gradient: "from-canvas via-muted to-canvas",
    amountColor: "text-foreground-secondary",
    icon: "😿",
    detailText: "领取失败，请稍后再试",
  },
};

function RedPacketResultContent() {
  const searchParams = useSearchParams();

  const amountParam = searchParams.get("amount");
  const statusParam = searchParams.get("status") || "pending";
  const claimId = searchParams.get("claim_id");

  // 安全解析金额（分）
  const amountFen = amountParam ? parseInt(amountParam, 10) : 0;
  const isValidAmount = !isNaN(amountFen) && amountFen > 0;

  // 映射后端状态到展示状态
  let displayStatus: DisplayStatus;
  if (statusParam === "success" || statusParam === "delivered") {
    displayStatus = "success";
  } else if (statusParam === "pending" || statusParam === "claimed") {
    displayStatus = "pending";
  } else {
    displayStatus = "failed";
  }

  const config = STATUS_CONFIG[displayStatus];

  return (
    <div
      className={`flex min-h-screen flex-col items-center bg-gradient-to-b ${config.gradient} px-6 pt-20`}
    >
      {/* 图标 */}
      <div className="flex h-24 w-24 items-center justify-center rounded-full bg-surface shadow-lg">
        <span className="text-5xl">{config.icon}</span>
      </div>

      {/* 状态标题 */}
      <h1 className="mt-6 text-xl font-bold text-foreground">{config.title}</h1>

      {/* 金额展示（仅成功和处理中显示） */}
      {displayStatus !== "failed" && isValidAmount && (
        <div className="mt-6 text-center">
          <p className="text-sm text-foreground-secondary">{config.subtitle}</p>
          <p className={`mt-1 text-5xl font-bold ${config.amountColor}`}>
            {fenToYuan(amountFen)}
            <span className="ml-1 text-lg font-medium">元</span>
          </p>
        </div>
      )}

      {/* 详情文字 */}
      <p className="mt-4 text-sm text-foreground-secondary">
        {config.detailText}
      </p>

      {/* 订单号（调试用，可选显示） */}
      {claimId && (
        <p className="mt-2 text-xs text-foreground-tertiary">
          订单号：{claimId}
        </p>
      )}

      {/* 底部装饰和按钮 */}
      <div className="mt-auto w-full pb-10">
        {displayStatus === "pending" && (
          <p className="mb-4 text-center text-xs text-foreground-tertiary">
            一般 1-3 分钟内到账
          </p>
        )}
        <button
          type="button"
          onClick={() => {
            // 关闭当前页面（微信内用 WeixinJSBridge，普通浏览器用 history）
            if (typeof window !== "undefined") {
              // 尝试微信内关闭
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              const wx = (window as any).WeixinJSBridge;
              if (wx) {
                wx.invoke("closeWindow", {}, () => {});
              } else if (window.history.length > 1) {
                window.history.back();
              } else {
                window.close();
              }
            }
          }}
          className="w-full rounded-xl border border-base bg-surface py-3 text-sm font-medium text-foreground-secondary active:bg-muted"
        >
          {displayStatus === "success" ? "完成" : "返回"}
        </button>
      </div>
    </div>
  );
}

/**
 * 红包领取结果页
 *
 * 微信 OAuth 回调后重定向到此页面，通过 URL 查询参数展示结果：
 * - amount: 金额（分）
 * - status: success / pending / failed
 * - claim_id: 领取记录 ID
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
      <RedPacketResultContent />
    </Suspense>
  );
}
