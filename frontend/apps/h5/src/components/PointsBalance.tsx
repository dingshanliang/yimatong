"use client";

import { useState } from "react";

interface PointsBalanceProps {
  points: number;
  consumerId?: string;
}

export function PointsBalance({ points, consumerId }: PointsBalanceProps) {
  return (
    <div className="rounded-2xl bg-gradient-to-r from-blue-500 to-blue-600 p-4 text-white shadow-sm">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-medium text-blue-100">我的积分</p>
          <p className="mt-1 text-3xl font-bold">{points}</p>
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
