"use client";

import { useState } from "react";
import { apiClient } from "@/lib/api";

interface PointsExchangeProps {
  benefitId: string;
  title: string;
  pointsCost: number;
  description?: string;
  scanToken?: string;
  onExchanged?: () => void;
}

export function PointsExchange({
  benefitId,
  title,
  pointsCost,
  description,
  scanToken,
  onExchanged,
}: PointsExchangeProps) {
  const [loading, setLoading] = useState(false);
  const [exchanged, setExchanged] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExchange = async () => {
    setLoading(true);
    setError(null);
    try {
      await apiClient.post("/benefit-claims", {
        benefit_id: benefitId,
        claim_type: "points_exchange",
      });
      setExchanged(true);
      onExchanged?.();
    } catch (err: unknown) {
      const e = err as { response?: { status?: number; data?: { code?: string } } };
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
      <div className="rounded-2xl border border-green-200 bg-green-50 p-4 shadow-sm">
        <div className="flex items-center gap-2">
          <span className="text-lg">✅</span>
          <div>
            <p className="text-sm font-semibold text-green-800">兑换成功</p>
            <p className="text-xs text-green-600">{title}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
          {description && (
            <p className="mt-1 text-xs text-gray-500">{description}</p>
          )}
        </div>
        <div className="ml-3 flex flex-col items-end">
          <div className="flex items-center gap-1">
            <span className="text-lg">⭐</span>
            <span className="text-lg font-bold text-amber-600">{pointsCost}</span>
          </div>
          <p className="text-xs text-gray-400">积分</p>
        </div>
      </div>
      {error && <p className="mt-2 text-xs text-red-500">{error}</p>}
      <button
        onClick={handleExchange}
        disabled={loading}
        className={`mt-3 w-full rounded-xl py-2.5 text-sm font-semibold transition-colors ${
          loading
            ? "cursor-wait bg-amber-400 text-white"
            : "bg-amber-500 text-white active:bg-amber-600"
        }`}
      >
        {loading ? "兑换中..." : "立即兑换"}
      </button>
    </div>
  );
}
