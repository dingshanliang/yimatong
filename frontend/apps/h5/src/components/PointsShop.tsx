"use client";

import { useCallback, useEffect, useState } from "react";
import { apiClient } from "@/lib/api";

interface PointProduct {
  id: string;
  name: string;
  description?: string;
  image_url?: string;
  points_cost: number;
  stock: number;
  total_claimed: number;
}

interface PointsShopProps {
  consumerId: string;
  currentPoints: number;
  scanToken?: string;
  onExchanged?: () => void;
}

export function PointsShop({ consumerId, currentPoints, scanToken, onExchanged }: PointsShopProps) {
  const [products, setProducts] = useState<PointProduct[]>([]);
  const [loading, setLoading] = useState(true);
  const [exchanging, setExchanging] = useState<string | null>(null);
  const [exchanged, setExchanged] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const fetchProducts = useCallback(async () => {
    try {
      const headers: Record<string, string> = {};
      if (scanToken) headers.Authorization = `Bearer ${scanToken}`;
      const { data } = await apiClient.get("/members/point-products", {
        params: { enabled_only: true, page_size: 50 },
        headers,
      });
      setProducts((data.items || []) as PointProduct[]);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [scanToken]);

  useEffect(() => { fetchProducts(); }, [fetchProducts]);

  const handleExchange = async (product: PointProduct) => {
    setExchanging(product.id);
    setError(null);
    try {
      await apiClient.post("/members/point-products/exchange", {
        consumer_id: consumerId,
        product_id: product.id,
      });
      setExchanged((prev) => new Set(prev).add(product.id));
      onExchanged?.();
    } catch (err: unknown) {
      const e = err as { response?: { status?: number; data?: { detail?: string } } };
      if (e.response?.status === 409) {
        setExchanged((prev) => new Set(prev).add(product.id));
      } else {
        setError(e.response?.data?.detail || "兑换失败");
      }
    } finally {
      setExchanging(null);
    }
  };

  if (loading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-24 rounded-2xl bg-gray-100 animate-pulse" />
        ))}
      </div>
    );
  }

  if (products.length === 0) {
    return (
      <div className="py-8 text-center">
        <p className="text-sm text-gray-400">暂无可兑换商品</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {error && (
        <div className="rounded-xl bg-red-50 px-3 py-2 text-xs text-red-500">{error}</div>
      )}

      {products.map((product) => {
        const isExchanged = exchanged.has(product.id);
        const canAfford = currentPoints >= product.points_cost;
        const isExchanging = exchanging === product.id;

        return (
          <div key={product.id} className="rounded-2xl border bg-white p-4 shadow-sm">
            <div className="flex items-start justify-between">
              <div className="flex-1 min-w-0">
                {product.image_url && (
                  <img src={product.image_url} alt={product.name} className="mb-2 h-20 w-full rounded-lg object-cover" />
                )}
                <h3 className="text-sm font-semibold text-gray-900">{product.name}</h3>
                {product.description && (
                  <p className="mt-1 text-xs text-gray-500 line-clamp-2">{product.description}</p>
                )}
                <p className="mt-1 text-xs text-gray-400">
                  库存 {product.stock} · 已兑 {product.total_claimed}
                </p>
              </div>
              <div className="ml-3 flex flex-col items-end shrink-0">
                <div className="flex items-center gap-1">
                  <span className="text-lg">⭐</span>
                  <span className="text-lg font-bold text-amber-600">{product.points_cost}</span>
                </div>
                <p className="text-xs text-gray-400">积分</p>
              </div>
            </div>

            {isExchanged ? (
              <div className="mt-3 rounded-xl bg-green-50 py-2 text-center text-sm text-green-600">
                ✅ 已兑换
              </div>
            ) : (
              <button
                onClick={() => handleExchange(product)}
                disabled={!canAfford || product.stock <= 0 || !!exchanging}
                className={`mt-3 w-full rounded-xl py-2.5 text-sm font-semibold transition-colors ${
                  product.stock <= 0
                    ? "cursor-not-allowed bg-gray-100 text-gray-400"
                    : !canAfford
                      ? "cursor-not-allowed bg-gray-100 text-gray-400"
                      : isExchanging
                        ? "cursor-wait bg-amber-400 text-white"
                        : "bg-amber-500 text-white active:bg-amber-600"
                }`}
              >
                {product.stock <= 0 ? "已售罄" : !canAfford ? "积分不足" : isExchanging ? "兑换中..." : "立即兑换"}
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
