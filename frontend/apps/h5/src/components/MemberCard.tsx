"use client";

import { useEffect, useState } from "react";
import { apiClient, getConsumerId } from "@/lib/api";

interface MemberCardProps {
  consumerId?: string;
  memberLevel?: string;
  totalPoints?: number;
}

const LEVEL_STYLES: Record<string, { icon: string; bg: string; text: string; label: string }> = {
  normal: { icon: "🥉", bg: "bg-amber-50", text: "text-amber-700", label: "普通会员" },
  silver: { icon: "🥈", bg: "bg-gray-50", text: "text-gray-700", label: "银卡会员" },
  gold: { icon: "🥇", bg: "bg-yellow-50", text: "text-yellow-700", label: "金卡会员" },
  platinum: { icon: "💎", bg: "bg-blue-50", text: "text-blue-700", label: "白金会员" },
};

export function MemberCard({
  consumerId: propConsumerId,
  memberLevel: fallbackLevel,
  totalPoints: fallbackPoints,
}: MemberCardProps) {
  const consumerId = propConsumerId || getConsumerId() || undefined;
  const [profile, setProfile] = useState<{
    member_level: string;
    total_points: number;
    nickname?: string;
  } | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!consumerId) return;
    setLoading(true);
    apiClient
      .get("/consumers/me", { params: { consumer_id: consumerId } })
      .then((res) => setProfile(res.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [consumerId]);

  const level = profile?.member_level || fallbackLevel || "normal";
  const points = profile?.total_points ?? fallbackPoints ?? 0;
  const style = LEVEL_STYLES[level] || LEVEL_STYLES.normal;

  if (loading && !profile) {
    return (
      <div className="rounded-2xl bg-white p-4 shadow-sm animate-pulse">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-8 w-8 rounded-full bg-gray-200" />
            <div>
              <div className="h-4 w-16 rounded bg-gray-200" />
              <div className="mt-1 h-3 w-24 rounded bg-gray-200" />
            </div>
          </div>
          <div className="text-right">
            <div className="h-6 w-12 rounded bg-gray-200" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-2xl">{style.icon}</span>
          <div>
            <p className="text-sm font-semibold text-gray-900">{style.label}</p>
            <p className="text-xs text-gray-500">
              {profile?.nickname || (consumerId ? `${consumerId.slice(0, 8)}...` : "游客")}
            </p>
          </div>
        </div>
        <div className="text-right">
          <p className="text-2xl font-bold text-gray-900">{points}</p>
          <p className="text-xs text-gray-500">积分</p>
        </div>
      </div>
    </div>
  );
}
