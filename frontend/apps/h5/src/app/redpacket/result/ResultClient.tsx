"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { apiClient } from "@/lib/api";
import { readClaimRevisitCredential } from "@/lib/claim-revisit";

/** 消费者可见状态（与后端发放状态端点三态一致） */
type DisplayPhase =
  "resolving" | "processing" | "success" | "failed" | "unavailable";

interface ClaimStatusResponse {
  status: "processing" | "success" | "failed";
  amount_minor: number | null;
  completed_at: string | null;
  failure_reason: string | null;
}

/** 轮询节奏：短间隔起步、指数退避、有上限；总时长封顶后停止但不伪造终态。 */
const POLL_INITIAL_MS = 3000;
const POLL_BACKOFF_FACTOR = 1.6;
const POLL_MAX_INTERVAL_MS = 15000;
const POLL_TOTAL_CAP_MS = 15 * 60 * 1000;

/** 处理中文案不承诺固定到账时长（发放重试链路最长可达小时级）。 */
const PROCESSING_DETAIL =
  "红包发放需要一点时间，通常几分钟内完成；具体到账以微信零钱为准";
const PROCESSING_CAPPED_DETAIL =
  "仍在处理中，可稍后在微信零钱查看；长时间未到账请联系活动客服";
const UNAVAILABLE_DETAIL =
  "当前无法查询这笔领取的结果，如长时间未到账请联系活动客服";

const FAILURE_REASON_TEXT: Record<string, string> = {
  channel_failure: "发放通道暂时不可用，未到账资金将由系统退回或客服跟进",
  recipient_missing: "微信授权信息缺失，红包未能发出",
  risk_paused: "触发风控暂停，请联系活动客服核实",
  system_error: "系统异常，红包未能发出",
};

/** 将分转换为元的显示字符串（1 元 = 100 分） */
function fenToYuan(fen: number): string {
  const yuan = fen / 100;
  if (yuan === Math.floor(yuan)) {
    return yuan.toFixed(0);
  }
  return yuan.toFixed(2);
}

function errorMessageStatus(error: unknown): number | null {
  if (typeof error === "object" && error !== null && "response" in error) {
    const status = (error as { response?: { status?: number } }).response
      ?.status;
    return typeof status === "number" ? status : null;
  }
  return null;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * 红包领取结果页（客户端）
 *
 * 领取受理后由领取卡跳转进入；按回访凭证（或 scan_token）轮询发放状态：
 * 处理中 → 成功（含金额）/ 失败（含原因分类与客服路径）。
 * 轮询封顶仍在处理时如实展示"仍在处理中 + 客服路径"，不伪装终态。
 */
export function RedPacketResultClient() {
  const searchParams = useSearchParams();
  const claimId = searchParams.get("claim_id");
  const urlCredential = searchParams.get("credential");

  const [phase, setPhase] = useState<DisplayPhase>("resolving");
  const [capped, setCapped] = useState(false);
  const [amountMinor, setAmountMinor] = useState<number | null>(null);
  const [failureReason, setFailureReason] = useState<string | null>(null);

  useEffect(() => {
    if (!claimId) {
      setPhase("unavailable");
      return;
    }
    let cancelled = false;
    const startedAt = Date.now();
    let interval = POLL_INITIAL_MS;
    const storedCredential = readClaimRevisitCredential(claimId);
    const credential = urlCredential || storedCredential;
    const headers = credential
      ? { Authorization: `Bearer ${credential}` }
      : undefined;

    setPhase("processing");

    const poll = async () => {
      let firstAttempt = true;
      while (!cancelled) {
        if (!firstAttempt) {
          const wait = interval;
          interval = Math.min(
            interval * POLL_BACKOFF_FACTOR,
            POLL_MAX_INTERVAL_MS
          );
          await sleep(wait);
          if (cancelled) return;
        }
        firstAttempt = false;
        if (Date.now() - startedAt >= POLL_TOTAL_CAP_MS) {
          setCapped(true);
          return;
        }
        try {
          const { data } = await apiClient.get<ClaimStatusResponse>(
            `/benefit-claims/${encodeURIComponent(claimId)}/status`,
            { headers }
          );
          if (cancelled) return;
          if (data.status === "success") {
            setPhase("success");
            setAmountMinor(data.amount_minor);
            return;
          }
          if (data.status === "failed") {
            setPhase("failed");
            setFailureReason(data.failure_reason);
            return;
          }
          setPhase("processing");
        } catch (error) {
          if (cancelled) return;
          const status = errorMessageStatus(error);
          // 查询资格失效（凭证过期/记录不可查）：停止轮询，给出客服路径。
          if (status === 401 || status === 404) {
            setPhase("unavailable");
            return;
          }
          // 网络/限流/服务端瞬时错误：按退避继续。
        }
      }
    };

    void poll();
    return () => {
      cancelled = true;
    };
  }, [claimId, urlCredential]);

  const config =
    phase === "success"
      ? {
          title: "领取成功",
          gradient: "from-danger-bg via-warning-bg to-warning-bg",
          amountColor: "text-danger",
          icon: "🧧",
        }
      : phase === "failed"
        ? {
            title: "红包未发出",
            gradient: "from-canvas via-muted to-canvas",
            amountColor: "text-foreground-secondary",
            icon: "😿",
          }
        : phase === "unavailable"
          ? {
              title: "暂时无法查询结果",
              gradient: "from-canvas via-muted to-canvas",
              amountColor: "text-foreground-secondary",
              icon: "🔍",
            }
          : {
              title: "处理中",
              gradient: "from-warning-bg via-warning-bg to-warning-bg",
              amountColor: "text-warning",
              icon: "⏳",
            };

  const detailText =
    phase === "success"
      ? "已到微信零钱，请注意查收"
      : phase === "failed"
        ? (FAILURE_REASON_TEXT[failureReason ?? ""] ??
          "红包未能发出，如需帮助请联系活动客服")
        : phase === "unavailable"
          ? UNAVAILABLE_DETAIL
          : capped
            ? PROCESSING_CAPPED_DETAIL
            : PROCESSING_DETAIL;

  return (
    <div
      className={`flex min-h-screen flex-col items-center bg-gradient-to-b ${config.gradient} px-6 pt-20`}
    >
      <div className="flex h-24 w-24 items-center justify-center rounded-full bg-surface shadow-lg">
        <span className="text-5xl">{config.icon}</span>
      </div>

      <h1 className="mt-6 text-xl font-bold text-foreground">{config.title}</h1>

      {phase === "success" && amountMinor != null && amountMinor > 0 && (
        <div className="mt-6 text-center">
          <p className="text-sm text-foreground-secondary">恭喜领取</p>
          <p className={`mt-1 text-5xl font-bold ${config.amountColor}`}>
            {fenToYuan(amountMinor)}
            <span className="ml-1 text-lg font-medium">元</span>
          </p>
        </div>
      )}

      <p className="mt-4 text-center text-sm text-foreground-secondary">
        {detailText}
      </p>
      {phase === "failed" && (
        <p className="mt-2 text-xs text-foreground-tertiary">
          如需帮助，请联系活动客服。
        </p>
      )}

      <div className="mt-auto w-full pb-10">
        <button
          type="button"
          onClick={() => {
            if (typeof window === "undefined") return;
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const wx = (window as any).WeixinJSBridge;
            if (wx) {
              wx.invoke("closeWindow", {}, () => {});
            } else if (window.history.length > 1) {
              window.history.back();
            } else {
              window.close();
            }
          }}
          className="w-full rounded-xl border border-base bg-surface py-3 text-sm font-medium text-foreground-secondary active:bg-muted"
        >
          {phase === "success" ? "完成" : "返回"}
        </button>
      </div>
    </div>
  );
}
