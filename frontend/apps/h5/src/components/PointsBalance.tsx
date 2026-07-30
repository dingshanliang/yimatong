"use client";

import { useEffect, useState } from "react";
import { apiClient, getConsumerId } from "@/lib/api";
import { PointsHistory } from "@/components/PointsHistory";

interface PointsBalanceProps {
  points?: number;
  consumerId?: string;
  scanToken?: string;
}

export function PointsBalance({
  points: fallbackPoints,
  consumerId: propConsumerId,
  scanToken,
}: PointsBalanceProps) {
  const consumerId = propConsumerId || getConsumerId() || undefined;
  const [points, setPoints] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [resolvedConsumerId, setResolvedConsumerId] = useState<string | null>(
    null
  );

  useEffect(() => {
    if (!consumerId) return;
    setLoading(true);
    const headers: Record<string, string> = {};
    if (scanToken) headers.Authorization = `Bearer ${scanToken}`;
    apiClient
      .get("/consumers/points/me", {
        params: { consumer_id: consumerId },
        headers,
      })
      .then((res) => {
        setPoints(res.data.total_points ?? 0);
        setResolvedConsumerId(res.data.consumer_id || consumerId);
      })
      .catch(() => setError("加载积分失败"))
      .finally(() => setLoading(false));
  }, [consumerId, scanToken]);

  const displayPoints = points ?? fallbackPoints ?? 0;

  if (loading && points === null) {
    return (
      <div className="rounded-2xl bg-gradient-to-r from-brand to-action p-4 text-white shadow-sm animate-pulse">
        <div className="flex items-center justify-between">
          <div>
            <div className="h-3 w-16 rounded bg-white/30" />
            <div className="mt-2 h-8 w-20 rounded bg-white/30" />
          </div>
          <div className="h-12 w-12 rounded-full bg-white/20" />
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl bg-gradient-to-r from-brand to-action p-4 text-white shadow-sm">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-medium text-on-action">我的积分</p>
          <p className="mt-1 text-3xl font-bold">{displayPoints}</p>
        </div>
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-white/20">
          <span className="text-2xl">⭐</span>
        </div>
      </div>
      <p className="mt-2 text-xs text-on-action">积分可用于兑换权益</p>
      {error && (
        <p className="mt-1 rounded-xl bg-danger/20 px-3 py-1.5 text-xs text-danger">
          {error}
        </p>
      )}
      {!consumerId && (
        <p className="mt-3 rounded-xl bg-white/15 px-3 py-2 text-xs text-on-action">
          完成手机号留资后，可查看积分并兑换权益。
        </p>
      )}

      {resolvedConsumerId && (
        <button
          onClick={() => setShowHistory(!showHistory)}
          className="mt-3 w-full rounded-xl bg-white/15 py-2 text-xs font-medium text-white backdrop-blur-sm active:bg-white/25"
        >
          {showHistory ? "收起明细" : "查看明细"}
        </button>
      )}

      {showHistory && resolvedConsumerId && (
        <div className="mt-3 max-h-60 overflow-y-auto rounded-xl bg-white/10 p-3 backdrop-blur-sm">
          <PointsHistory consumerId={resolvedConsumerId} />
        </div>
      )}
    </div>
  );
}
