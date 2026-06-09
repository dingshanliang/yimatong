"use client";

import { useCallback, useEffect, useState } from "react";
import { apiClient, getConsumerId } from "@/lib/api";

interface Transaction {
  id: string;
  amount: number;
  balance_after: number;
  txn_type: "earning" | "spending" | "expired";
  reason: string;
}

interface PointsHistoryProps {
  consumerId?: string;
  scanToken?: string;
}

const PAGE_SIZE = 20;

export function PointsHistory({ consumerId, scanToken }: PointsHistoryProps) {
  const resolvedConsumerId = consumerId || getConsumerId() || undefined;
  const [items, setItems] = useState<Transaction[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPage = useCallback(
    async (p: number) => {
      setLoading(true);
      if (!resolvedConsumerId) {
        setItems([]);
        setTotal(0);
        setLoading(false);
        setInitialLoading(false);
        return;
      }
      try {
        const headers: Record<string, string> = {};
        if (scanToken) headers.Authorization = `Bearer ${scanToken}`;
        const { data } = await apiClient.get("/consumers/points/transactions", {
          params: { consumer_id: resolvedConsumerId, page: p, page_size: PAGE_SIZE },
          headers,
        });
        const newItems = (data.items || []) as Transaction[];
        if (p === 1) {
          setItems(newItems);
        } else {
          setItems((prev) => [...prev, ...newItems]);
        }
        setTotal(data.total || 0);
        setPage(p);
      } catch {
        setError("加载积分记录失败");
      } finally {
        setLoading(false);
        setInitialLoading(false);
      }
    },
    [resolvedConsumerId, scanToken],
  );

  useEffect(() => {
    fetchPage(1);
  }, [fetchPage]);

  const hasMore = items.length < total;

  if (initialLoading) {
    return (
      <div className="space-y-3 py-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="flex items-center justify-between animate-pulse">
            <div className="flex items-center gap-2">
              <div className="h-5 w-10 rounded bg-gray-200" />
              <div className="h-4 w-20 rounded bg-gray-200" />
            </div>
            <div className="h-4 w-12 rounded bg-gray-200" />
          </div>
        ))}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <p className="py-4 text-center text-sm text-gray-400">暂无积分记录</p>
    );
  }

  return (
    <div>
      {error && (
        <p className="mb-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">{error}</p>
      )}
      <div className="divide-y divide-gray-100">
        {items.map((txn) => (
          <div key={txn.id} className="flex items-center justify-between py-2.5">
            <div className="flex items-center gap-2 min-w-0">
              <span
                className={`inline-flex rounded-md px-1.5 py-0.5 text-xs font-medium ${
                  txn.txn_type === "earning"
                    ? "bg-green-50 text-green-700"
                    : txn.txn_type === "expired"
                      ? "bg-gray-100 text-gray-600"
                      : "bg-orange-50 text-orange-700"
                }`}
              >
                {txn.txn_type === "earning" ? "收入" : txn.txn_type === "expired" ? "过期" : "支出"}
              </span>
              <span className="truncate text-sm text-gray-700">
                {txn.reason || (txn.txn_type === "earning" ? "积分奖励" : txn.txn_type === "expired" ? "积分过期" : "积分消耗")}
              </span>
            </div>
            <div className="flex items-center gap-3 shrink-0">
              <span
                className={`text-sm font-semibold ${
                  txn.txn_type === "earning" ? "text-green-600" : txn.txn_type === "expired" ? "text-gray-500" : "text-orange-600"
                }`}
              >
                {txn.txn_type === "earning" ? "+" : "-"}
                {txn.amount}
              </span>
              <span className="text-xs text-gray-400 w-16 text-right">
                余 {txn.balance_after}
              </span>
            </div>
          </div>
        ))}
      </div>

      {hasMore && (
        <button
          onClick={() => fetchPage(page + 1)}
          disabled={loading}
          className="mt-2 w-full rounded-xl py-2 text-xs text-gray-500 active:bg-gray-50 disabled:text-gray-300"
        >
          {loading ? "加载中..." : "加载更多"}
        </button>
      )}
    </div>
  );
}
