"use client";

import { useEffect, useState } from "react";
import { apiClient, getConsumerId } from "@/lib/api";
import { PointsHistory } from "@/components/PointsHistory";

interface MemberCardProps {
  consumerId?: string;
  memberLevel?: string;
  totalPoints?: number;
}

interface LevelDef {
  icon: string;
  bg: string;
  text: string;
  label: string;
  threshold: number;
}

const LEVELS: Record<string, LevelDef> = {
  normal: { icon: "🥉", bg: "bg-amber-50", text: "text-amber-700", label: "普通会员", threshold: 0 },
  silver: { icon: "🥈", bg: "bg-gray-50", text: "text-gray-700", label: "银卡会员", threshold: 100 },
  gold: { icon: "🥇", bg: "bg-yellow-50", text: "text-yellow-700", label: "金卡会员", threshold: 500 },
  platinum: { icon: "💎", bg: "bg-blue-50", text: "text-blue-700", label: "白金会员", threshold: 2000 },
};

const LEVEL_ORDER = ["normal", "silver", "gold", "platinum"];

function getNextLevel(current: string): LevelDef | null {
  const idx = LEVEL_ORDER.indexOf(current);
  if (idx < 0 || idx >= LEVEL_ORDER.length - 1) return null;
  return LEVELS[LEVEL_ORDER[idx + 1]];
}

function getLevelProgress(points: number, currentLevel: string): number {
  const current = LEVELS[currentLevel];
  const next = getNextLevel(currentLevel);
  if (!next) return 100;
  const range = next.threshold - current.threshold;
  if (range <= 0) return 100;
  const progress = ((points - current.threshold) / range) * 100;
  return Math.min(100, Math.max(0, progress));
}

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
    consumer_id?: string;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  useEffect(() => {
    if (!consumerId) return;
    setLoading(true);
    apiClient
      .get("/consumers/me", { params: { consumer_id: consumerId } })
      .then((res) => setProfile(res.data))
      .catch(() => setError("加载会员信息失败"))
      .finally(() => setLoading(false));
  }, [consumerId]);

  const level = profile?.member_level || fallbackLevel || "normal";
  const points = profile?.total_points ?? fallbackPoints ?? 0;
  const resolvedConsumerId = profile?.consumer_id || consumerId || "";
  const style = LEVELS[level] || LEVELS.normal;
  const nextLevel = getNextLevel(level);
  const progress = getLevelProgress(points, level);
  const pointsNeeded = nextLevel ? nextLevel.threshold - points : 0;

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
              {profile?.nickname || (resolvedConsumerId ? `${resolvedConsumerId.slice(0, 8)}...` : "游客")}
            </p>
          </div>
        </div>
        <div className="text-right">
          <p className="text-2xl font-bold text-gray-900">{points}</p>
          <p className="text-xs text-gray-500">积分</p>
        </div>
      </div>

      {/* 等级进度 */}
      {nextLevel ? (
        <div className="mt-3">
          <div className="flex items-center justify-between text-xs text-gray-500">
            <span>{style.label}</span>
            <span>距{nextLevel.label}还需 {pointsNeeded} 积分</span>
          </div>
          <div className="mt-1 h-1.5 rounded-full bg-gray-100">
            <div
              className="h-full rounded-full bg-gradient-to-r from-blue-400 to-blue-600 transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      ) : (
        <p className="mt-2 text-xs text-blue-500">已臻至最高等级</p>
      )}

      {error && (
        <p className="mt-2 rounded-lg bg-red-50 px-3 py-1.5 text-xs text-red-600">{error}</p>
      )}

      {/* 积分明细 toggle */}
      {resolvedConsumerId && (
        <button
          onClick={() => setShowHistory(!showHistory)}
          className="mt-3 w-full rounded-xl border border-gray-100 py-2 text-xs text-gray-500 active:bg-gray-50"
        >
          {showHistory ? "收起明细" : "查看积分明细"}
        </button>
      )}

      {showHistory && resolvedConsumerId && (
        <div className="mt-2 max-h-60 overflow-y-auto">
          <PointsHistory consumerId={resolvedConsumerId} />
        </div>
      )}
    </div>
  );
}
