"use client";

import { useEffect, useState } from "react";
import { apiClient, getConsumerId } from "@/lib/api";

interface PointsBalanceProps {
  points?: number;
  consumerId?: string;
}

export function PointsBalance({
  points: fallbackPoints,
  consumerId: propConsumerId,
}: PointsBalanceProps) {
  const consumerId = propConsumerId || getConsumerId() || undefined;
  const [points, setPoints] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!consumerId) return;
    setLoading(true);
    apiClient
      .get("/consumers/me", { params: { consumer_id: consumerId } })
      .then((res) => setPoints(res.data.total_points ?? 0))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [consumerId]);

  const displayPoints = points ?? fallbackPoints ?? 0;

  if (loading && points === null) {
    return (
      <div className="rounded-2xl bg-gradient-to-r from-blue-500 to-blue-600 p-4 text-white shadow-sm animate-pulse">
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
    <div className="rounded-2xl bg-gradient-to-r from-blue-500 to-blue-600 p-4 text-white shadow-sm">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-medium text-blue-100">我的积分</p>
          <p className="mt-1 text-3xl font-bold">{displayPoints}</p>
        </div>
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-white/20">
          <span className="text-2xl">⭐</span>
        </div>
      </div>
      {consumerId && (
        <p className="mt-2 text-xs text-blue-100">积分可用于兑换权益</p>
      )}
    </div>
  );
}
