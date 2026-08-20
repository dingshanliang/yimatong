"use client";

import { useEffect, useState } from "react";
import { Check, Copy, RefreshCw, Store, Ticket } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";

import { apiClient } from "@/lib/api";

type CouponStatus = "available" | "reserved" | "used" | "expired" | "revoked";

interface MemberCoupon {
  id: string;
  coupon_number: string;
  name: string;
  amount_minor: number;
  minimum_spend_minor: number;
  channel_scope: "online" | "store" | "both";
  status: CouponStatus;
  valid_from: string;
  valid_until: string;
  reservation_expires_at?: string | null;
}

interface RedemptionCredential {
  couponId: string;
  token: string;
}

const STATUS_LABEL: Record<CouponStatus, string> = {
  available: "可使用",
  reserved: "订单占用中",
  used: "已使用",
  expired: "已过期",
  revoked: "已作废",
};

function money(minor: number) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    minimumFractionDigits: minor % 100 === 0 ? 0 : 2,
  }).format(minor / 100);
}

function dateLabel(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

export function MemberCouponWallet({
  scanToken,
  refreshKey = 0,
}: {
  scanToken?: string;
  refreshKey?: number;
}) {
  const [coupons, setCoupons] = useState<MemberCoupon[] | null>(null);
  const [error, setError] = useState(false);
  const [credential, setCredential] = useState<RedemptionCredential | null>(
    null
  );
  const [issuingId, setIssuingId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    if (!scanToken) {
      setCoupons(null);
      return;
    }
    let active = true;
    setError(false);
    apiClient
      .get("/consumers/membership/coupons", {
        headers: { Authorization: `Bearer ${scanToken}` },
      })
      .then(({ data }) => {
        if (active)
          setCoupons(Array.isArray(data) ? (data as MemberCoupon[]) : []);
      })
      .catch((requestError: { response?: { status?: number } }) => {
        if (!active) return;
        if (
          requestError.response?.status === 401 ||
          requestError.response?.status === 404
        ) {
          setCoupons(null);
          return;
        }
        setError(true);
      });
    return () => {
      active = false;
    };
  }, [refreshKey, retryKey, scanToken]);

  if (!scanToken || (coupons === null && !error)) return null;

  const issueCredential = async (couponId: string) => {
    if (issuingId) return;
    setIssuingId(couponId);
    setError(false);
    setCopied(false);
    try {
      const { data } = await apiClient.post(
        `/consumers/membership/coupons/${couponId}/store-token`,
        {},
        { headers: { Authorization: `Bearer ${scanToken}` } }
      );
      if (typeof data?.redemption_token !== "string")
        throw new Error("invalid redemption credential");
      setCredential({ couponId, token: data.redemption_token });
    } catch {
      setError(true);
    } finally {
      setIssuingId(null);
    }
  };

  const copyCredential = async () => {
    if (!credential) return;
    try {
      await navigator.clipboard.writeText(credential.token);
      setCopied(true);
    } catch {
      setError(true);
    }
  };

  return (
    <section
      className="mx-4 mt-3 overflow-hidden rounded-xl bg-foreground text-white"
      aria-labelledby="coupon-wallet-title"
    >
      <div className="flex items-start justify-between gap-3 px-4 pb-3 pt-4">
        <div>
          <h2
            id="coupon-wallet-title"
            className="flex items-center gap-2 font-semibold"
          >
            <Ticket className="h-5 w-5 text-action-light" aria-hidden="true" />
            我的复购券
          </h2>
          <p className="mt-1 text-xs text-white/70">
            券状态随会员身份恢复，不依赖当前手机。
          </p>
        </div>
        <span className="tabular-nums text-sm text-white/70">
          {coupons?.length ?? 0} 张
        </span>
      </div>

      {error && (
        <div
          className="mx-4 mb-3 flex items-center justify-between gap-3 rounded-xl bg-white/10 px-3 py-2 text-sm"
          role="alert"
        >
          <span>券包暂时无法更新，请稍后重试。</span>
          <button
            type="button"
            onClick={() => setRetryKey((key) => key + 1)}
            className="inline-flex items-center gap-1 font-medium"
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            重试
          </button>
        </div>
      )}

      {coupons?.length === 0 && (
        <p className="border-t border-white/10 px-4 py-5 text-sm text-white/70">
          完成符合条件的扫码活动后，复购券会自动存入这里。
        </p>
      )}

      {coupons?.map((coupon) => {
        const canUseAtStore =
          coupon.status === "available" &&
          (coupon.channel_scope === "store" || coupon.channel_scope === "both");
        const open = credential?.couponId === coupon.id;
        return (
          <article
            key={coupon.id}
            className="border-t border-white/10 bg-white px-4 py-4 text-foreground"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="font-semibold">{coupon.name}</p>
                <p className="mt-1 text-xs text-foreground-secondary">
                  {coupon.minimum_spend_minor > 0
                    ? `商品金额满 ${money(coupon.minimum_spend_minor)} 可用`
                    : "无门槛可用"}
                  · 有效期至 {dateLabel(coupon.valid_until)}
                </p>
              </div>
              <div className="shrink-0 text-right">
                <p className="text-xl font-bold tabular-nums text-action">
                  {money(coupon.amount_minor)}
                </p>
                <p className="mt-0.5 text-xs font-medium text-foreground-secondary">
                  {STATUS_LABEL[coupon.status]}
                </p>
              </div>
            </div>

            {canUseAtStore && !open && (
              <button
                type="button"
                disabled={issuingId === coupon.id}
                onClick={() => void issueCredential(coupon.id)}
                className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-action px-4 py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Store className="h-4 w-4" aria-hidden="true" />
                {issuingId === coupon.id ? "正在生成…" : "到店出示核销码"}
              </button>
            )}

            {open && credential && (
              <div className="mt-4 text-center" aria-live="polite">
                <div className="mx-auto w-fit rounded-xl border border-base bg-white p-3">
                  <QRCodeSVG
                    value={credential.token}
                    size={184}
                    level="M"
                    aria-label="门店核销二维码"
                  />
                </div>
                <p className="mt-3 text-sm font-medium">请让门店员工扫码核销</p>
                <p className="mt-1 text-xs text-foreground-secondary">
                  核销码 5 分钟内有效，使用后立即失效。
                </p>
                <div className="mt-3 flex gap-2">
                  <button
                    type="button"
                    onClick={() => void issueCredential(coupon.id)}
                    className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-xl border border-base px-3 py-2 text-sm font-medium"
                  >
                    <RefreshCw className="h-4 w-4" aria-hidden="true" />
                    刷新核销码
                  </button>
                  <button
                    type="button"
                    onClick={() => void copyCredential()}
                    className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-xl border border-base px-3 py-2 text-sm font-medium"
                  >
                    {copied ? (
                      <Check className="h-4 w-4" aria-hidden="true" />
                    ) : (
                      <Copy className="h-4 w-4" aria-hidden="true" />
                    )}
                    {copied ? "已复制" : "复制凭证"}
                  </button>
                </div>
              </div>
            )}
          </article>
        );
      })}
    </section>
  );
}
