"use client";

import { useCallback, useEffect, useState } from "react";
import { apiClient, getConsumerId } from "@/lib/api";

interface PointProduct {
  id: string;
  name: string;
  description?: string;
  image_url?: string;
  points_cost: number;
  stock: number;
  total_claimed: number;
  starts_at?: string | null;
  ends_at?: string | null;
  per_consumer_limit?: number;
  can_exchange?: boolean;
  exchange_block_reason?: string | null;
}

interface PointsShopProps {
  consumerId?: string;
  currentPoints?: number;
  scanToken?: string;
  onExchanged?: () => void;
}

function formatDate(value?: string | null) {
  if (!value) return "不限制";
  return new Date(value).toLocaleDateString("zh-CN");
}

export function PointsShop({ consumerId: propConsumerId, currentPoints, scanToken, onExchanged }: PointsShopProps) {
  const consumerId = propConsumerId || getConsumerId() || undefined;
  const [products, setProducts] = useState<PointProduct[]>([]);
  const [loading, setLoading] = useState(true);
  const [exchanging, setExchanging] = useState<string | null>(null);
  const [exchanged, setExchanged] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const fetchProducts = useCallback(async () => {
    if (!consumerId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const headers: Record<string, string> = {};
      if (scanToken) headers.Authorization = `Bearer ${scanToken}`;
      const { data } = await apiClient.get("/consumers/points/products", {
        params: { consumer_id: consumerId },
        headers,
      });
      setProducts((data.items || []) as PointProduct[]);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [consumerId, scanToken]);

  useEffect(() => { fetchProducts(); }, [fetchProducts]);

  const handleExchange = async (product: PointProduct) => {
    if (!consumerId) return;
    setExchanging(product.id);
    setError(null);
    try {
      const headers: Record<string, string> = {};
      if (scanToken) headers.Authorization = `Bearer ${scanToken}`;
      await apiClient.post("/consumers/points/exchanges", {
        consumer_id: consumerId,
        product_id: product.id,
      }, { headers });
      setExchanged((prev) => new Set(prev).add(product.id));
      await fetchProducts();
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

  if (!consumerId) {
    return (
      <div className="rounded-2xl border border-amber-100 bg-amber-50 p-4 text-center">
        <p className="text-sm font-medium text-amber-900">完成手机号留资后可兑换积分权益</p>
        <p className="mt-1 text-xs text-amber-700">留资成功后，积分余额和可兑换商品会自动显示。</p>
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
        const canExchange = product.can_exchange !== false && !isExchanged;
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
                <p className="mt-1 text-xs text-gray-400">
                  有效期 {formatDate(product.starts_at)} 至 {formatDate(product.ends_at)}
                </p>
                <p className="mt-1 text-xs text-gray-400">
                  每人限兑 {product.per_consumer_limit && product.per_consumer_limit > 0 ? `${product.per_consumer_limit} 次` : "不限制"}
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
                disabled={!canExchange || product.stock <= 0 || !!exchanging}
                className={`mt-3 w-full rounded-xl py-2.5 text-sm font-semibold transition-colors ${
                  product.stock <= 0
                    ? "cursor-not-allowed bg-gray-100 text-gray-400"
                    : !canExchange
                      ? "cursor-not-allowed bg-gray-100 text-gray-400"
                      : isExchanging
                        ? "cursor-wait bg-amber-400 text-white"
                        : "bg-amber-500 text-white active:bg-amber-600"
                }`}
              >
                {product.stock <= 0 ? "已售罄" : !canExchange ? product.exchange_block_reason || "暂不可兑换" : isExchanging ? "兑换中..." : "立即兑换"}
              </button>
            )}
            {typeof currentPoints === "number" && (
              <p className="mt-2 text-center text-xs text-gray-400">当前积分 {currentPoints}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}
