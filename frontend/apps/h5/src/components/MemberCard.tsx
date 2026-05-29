"use client";

import { useState } from "react";
import { apiClient } from "@/lib/api";

interface MemberCardProps {
  consumerId: string;
  memberLevel?: string;
  totalPoints?: number;
  scanToken?: string;
}

export function MemberCard({ consumerId, memberLevel, totalPoints, scanToken }: MemberCardProps) {
  const [profile, setProfile] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const level = (profile?.member_level as string) || memberLevel || "普通会员";
  const points = (profile?.total_points as number) ?? totalPoints ?? 0;

  const LEVEL_STYLES: Record<string, { icon: string; bg: string; text: string }> = {
    bronze: { icon: "🥉", bg: "bg-amber-50", text: "text-amber-700" },
    silver: { icon: "🥈", bg: "bg-gray-50", text: "text-gray-700" },
    gold: { icon: "🥇", bg: "bg-yellow-50", text: "text-yellow-700" },
    diamond: { icon: "💎", bg: "bg-blue-50", text: "text-blue-700" },
  };
  const style = LEVEL_STYLES[level] || LEVEL_STYLES.bronze;

  return (
    <div className="rounded-2xl bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-2xl">{style.icon}</span>
          <div>
            <p className="text-sm font-semibold text-gray-900">{level}</p>
            <p className="text-xs text-gray-500">{consumerId.slice(0, 8)}...</p>
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
