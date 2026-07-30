"use client";

import { useState } from "react";
import { apiClient } from "@/lib/api";

interface PointsExchangeProps {
  benefitId: string;
  title: string;
  pointsCost: number;
  description?: string;
  scanToken?: string;
  currentPoints?: number;
  onExchanged?: () => void;
}

export function PointsExchange({
  benefitId,
  title,
  pointsCost,
  description,
  scanToken,
  currentPoints,
  onExchanged,
}: PointsExchangeProps) {
  const [loading, setLoading] = useState(false);
  const [exchanged, setExchanged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [remainingPoints, setRemainingPoints] = useState<number | null>(null);

  const hasEnoughPoints =
    currentPoints === undefined || currentPoints >= pointsCost;
  const deficit = currentPoints !== undefined ? pointsCost - currentPoints : 0;

  const handleExchange = async () => {
    if (!hasEnoughPoints || loading) return;
    setLoading(true);
    setError(null);
    try {
      await apiClient.post("/benefit-claims", {
        benefit_id: benefitId,
        scan_token: scanToken,
        claim_type: "points_exchange",
      });
      setExchanged(true);
      if (currentPoints !== undefined) {
        setRemainingPoints(currentPoints - pointsCost);
      }
      onExchanged?.();
    } catch (err: unknown) {
      const e = err as {
        response?: { status?: number; data?: { code?: string } };
      };
      if (e.response?.status === 409) {
        setExchanged(true);
        return;
      }
      setError("兑换失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  if (exchanged) {
    return (
      <div className="rounded-2xl border border-success bg-success-bg p-4 shadow-sm">
        <div className="flex items-center gap-2">
          <span className="text-lg">✅</span>
          <div className="flex-1">
            <p className="text-sm font-semibold text-success">兑换成功</p>
            <p className="text-xs text-success">{title}</p>
          </div>
          {remainingPoints !== null && (
            <span className="text-xs text-success">
              剩余 {remainingPoints} 积分
            </span>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border bg-surface p-4 shadow-sm">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <h3 className="text-sm font-semibold text-foreground">{title}</h3>
          {description && (
            <p className="mt-1 text-xs text-foreground-secondary">
              {description}
            </p>
          )}
        </div>
        <div className="ml-3 flex flex-col items-end">
          <div className="flex items-center gap-1">
            <span className="text-lg">⭐</span>
            <span className="text-lg font-bold text-warning">{pointsCost}</span>
          </div>
          <p className="text-xs text-foreground-tertiary">积分</p>
        </div>
      </div>

      {currentPoints !== undefined && (
        <div className="mt-2 flex items-center justify-between rounded-xl bg-muted px-3 py-1.5">
          <span className="text-xs text-foreground-secondary">当前余额</span>
          <span className="text-xs font-medium text-foreground-secondary">
            {currentPoints} 积分
          </span>
        </div>
      )}

      {!hasEnoughPoints && (
        <p className="mt-2 text-xs text-danger">
          积分不足，还需 {deficit} 积分
        </p>
      )}
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      <button
        onClick={handleExchange}
        disabled={loading || !hasEnoughPoints}
        className={`mt-3 w-full rounded-xl py-2.5 text-sm font-semibold transition-colors ${
          !hasEnoughPoints
            ? "cursor-not-allowed bg-muted text-foreground-tertiary"
            : loading
              ? "cursor-wait bg-warning text-on-action"
              : "bg-warning text-on-action active:bg-warning"
        }`}
      >
        {!hasEnoughPoints ? "积分不足" : loading ? "兑换中..." : "立即兑换"}
      </button>
    </div>
  );
}
