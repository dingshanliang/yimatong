"use client";

import { useState, useCallback } from "react";
import { apiClient } from "@/lib/api";

/** 权益类型 */
type BenefitType =
  | "coupon"
  | "points"
  | "lottery"
  | "gift"
  | "cash_red_packet";

interface BenefitClaimCardProps {
  /** 权益 ID */
  benefitId: string;
  /** 权益类型 */
  benefitType: BenefitType;
  /** 权益标题 */
  title: string;
  /** 权益描述 */
  description?: string;
  /** 扫码令牌，用于鉴权 */
  scanToken?: string;
  /** 领取成功回调 */
  onClaimed?: () => void;
}

/** 权益类型对应的图标和配色 */
const BENEFIT_STYLES: Record<
  BenefitType,
  { icon: string; bg: string; text: string; border: string; label: string }
> = {
  coupon: {
    icon: "🎫",
    bg: "bg-blue-50",
    text: "text-blue-700",
    border: "border-blue-200",
    label: "优惠券",
  },
  points: {
    icon: "⭐",
    bg: "bg-amber-50",
    text: "text-amber-700",
    border: "border-amber-200",
    label: "积分",
  },
  lottery: {
    icon: "🎰",
    bg: "bg-purple-50",
    text: "text-purple-700",
    border: "border-purple-200",
    label: "抽奖",
  },
  gift: {
    icon: "🎁",
    bg: "bg-rose-50",
    text: "text-rose-700",
    border: "border-rose-200",
    label: "礼品",
  },
  cash_red_packet: {
    icon: "🧧",
    bg: "bg-red-50",
    text: "text-red-700",
    border: "border-red-200",
    label: "现金红包",
  },
};

/** 按钮文案映射 */
const CLAIM_BUTTON_TEXT: Record<BenefitType, string> = {
  coupon: "立即领取",
  points: "立即领取",
  lottery: "立即抽奖",
  gift: "立即领取",
  cash_red_packet: "领取红包",
};

/**
 * 将分转换为元的显示字符串
 * 1 元 = 100 分
 */
function fenToYuan(fen: number): string {
  const yuan = fen / 100;
  // 如果是整数，不显示小数点
  if (yuan === Math.floor(yuan)) {
    return yuan.toFixed(0);
  }
  // 最多两位小数
  return yuan.toFixed(2);
}

/**
 * 权益领取卡片
 *
 * 支持优惠券/积分/抽奖/礼品/现金红包五种权益类型，
 * 点击领取调用后端接口，领取后按钮变为灰色已领取状态。
 * 当后端返回 require_auth 时，弹出手机号授权弹窗。
 * 当后端返回 require_wechat_auth 时，跳转微信 OAuth 授权。
 */
export function BenefitClaimCard({
  benefitId,
  benefitType,
  title,
  description,
  scanToken,
  onClaimed,
}: BenefitClaimCardProps) {
  const [loading, setLoading] = useState(false);
  const [claimed, setClaimed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showPhoneModal, setShowPhoneModal] = useState(false);
  const [phone, setPhone] = useState("");
  // 红包领取成功后的金额展示（分）
  const [redPacketAmount, setRedPacketAmount] = useState<number | null>(null);

  const style = BENEFIT_STYLES[benefitType] ?? BENEFIT_STYLES.gift;
  const buttonText = CLAIM_BUTTON_TEXT[benefitType] ?? "立即领取";

  const handleClaim = useCallback(async () => {
    if (loading || claimed) return;
    setLoading(true);
    setError(null);

    try {
      const res = await apiClient.post("/benefit-claims", {
        benefit_id: benefitId,
        scan_token: scanToken,
      });

      const data = res.data as {
        status: string;
        benefit_id: string;
        amount?: number;
        claim_id?: string;
        auth_url_path?: string;
      };

      // 现金红包需要微信 OAuth 授权
      if (data.status === "require_wechat_auth" && data.auth_url_path) {
        try {
          // 获取微信 OAuth 跳转地址
          const authRes = await apiClient.get(data.auth_url_path);
          const authData = authRes.data as { auth_url: string };
          if (authData.auth_url) {
            // 跳转到微信 OAuth 页面，授权后回调会重定向到结果页
            window.location.href = authData.auth_url;
            return;
          }
        } catch {
          setError("获取授权地址失败，请重试");
          return;
        }
      }

      // 红包领取成功（已有 OpenID 的情况）
      if (
        benefitType === "cash_red_packet" &&
        (data.status === "success" || data.status === "delivered") &&
        typeof data.amount === "number"
      ) {
        setRedPacketAmount(data.amount);
        setClaimed(true);
        onClaimed?.();
        return;
      }

      // 通用领取成功
      setClaimed(true);
      onClaimed?.();
    } catch (err: unknown) {
      // 判断是否需要手机号授权
      if (
        typeof err === "object" &&
        err !== null &&
        "response" in err &&
        (err as { response?: { status?: number; data?: { code?: string } } })
          .response?.data?.code === "require_auth"
      ) {
        setShowPhoneModal(true);
        return;
      }
      // 判断幂等冲突（已领取）
      if (
        typeof err === "object" &&
        err !== null &&
        "response" in err &&
        (err as { response?: { status?: number } }).response?.status === 409
      ) {
        setClaimed(true);
        return;
      }
      setError("领取失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  }, [benefitId, benefitType, scanToken, loading, claimed, onClaimed]);

  const handlePhoneSubmit = useCallback(async () => {
    if (!phone || phone.length < 11) return;
    setLoading(true);
    try {
      await apiClient.post("/benefit-claims", {
        benefit_id: benefitId,
        scan_token: scanToken,
        phone,
      });
      setClaimed(true);
      setShowPhoneModal(false);
      onClaimed?.();
    } catch {
      setError("授权失败，请重试");
    } finally {
      setLoading(false);
    }
  }, [benefitId, scanToken, phone, onClaimed]);

  // 红包领取成功时显示金额
  if (redPacketAmount !== null) {
    return (
      <div className="rounded-2xl border border-red-200 bg-gradient-to-b from-red-50 to-amber-50 p-5 shadow-sm">
        <div className="text-center">
          <span className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-red-100 text-2xl">
            🧧
          </span>
          <p className="mt-3 text-sm text-gray-600">恭喜领取</p>
          <p className="mt-1 text-3xl font-bold text-red-600">
            {fenToYuan(redPacketAmount)}
            <span className="ml-1 text-base font-medium">元</span>
          </p>
          <p className="mt-2 text-xs text-gray-500">
            红包已发放至微信零钱，请注意查收
          </p>
        </div>
      </div>
    );
  }

  return (
    <>
      <div
        className={`rounded-2xl border bg-white p-4 shadow-sm ${style.border}`}
      >
        {/* 权益类型标签 + 标题 */}
        <div className="flex items-start gap-3">
          <span
            className={`inline-flex h-10 w-10 items-center justify-center rounded-xl text-lg ${style.bg}`}
          >
            {style.icon}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span
                className={`inline-block rounded-md px-1.5 py-0.5 text-xs font-medium ${style.bg} ${style.text}`}
              >
                {style.label}
              </span>
              <h3 className="truncate text-base font-semibold text-gray-900">
                {title}
              </h3>
            </div>
            {description && (
              <p className="mt-1 text-sm text-gray-600 line-clamp-2">
                {description}
              </p>
            )}
          </div>
        </div>

        {/* 领取按钮 */}
        <div className="mt-4">
          {error && (
            <p className="mb-2 text-center text-xs text-red-600">{error}</p>
          )}
          <button
            type="button"
            disabled={loading || claimed}
            onClick={handleClaim}
            className={`w-full rounded-xl py-2.5 text-sm font-semibold transition-colors ${
              claimed
                ? "cursor-default bg-gray-100 text-gray-400"
                : loading
                  ? "cursor-wait bg-blue-400 text-white"
                  : benefitType === "cash_red_packet"
                    ? "bg-red-600 text-white active:bg-red-700"
                    : "bg-blue-600 text-white active:bg-blue-700"
            }`}
          >
            {claimed ? "已领取" : loading ? "领取中..." : buttonText}
          </button>
        </div>
      </div>

      {/* 手机号授权弹窗 */}
      {showPhoneModal && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center">
          <div className="w-full max-w-md rounded-t-2xl bg-white p-6 shadow-xl sm:rounded-2xl">
            <h4 className="text-base font-semibold text-gray-900">
              授权手机号
            </h4>
            <p className="mt-1 text-sm text-gray-600">
              该权益需要授权手机号后才能领取
            </p>
            <input
              type="tel"
              maxLength={11}
              placeholder="请输入手机号"
              value={phone}
              onChange={(e) => setPhone(e.target.value.replace(/\D/g, ""))}
              className="mt-4 w-full rounded-xl border border-gray-200 px-4 py-2.5 text-sm outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            />
            <div className="mt-4 flex gap-3">
              <button
                type="button"
                onClick={() => setShowPhoneModal(false)}
                className="flex-1 rounded-xl border border-gray-200 py-2.5 text-sm text-gray-700 active:bg-gray-50"
              >
                取消
              </button>
              <button
                type="button"
                disabled={phone.length < 11 || loading}
                onClick={handlePhoneSubmit}
                className="flex-1 rounded-xl bg-blue-600 py-2.5 text-sm font-semibold text-white active:bg-blue-700 disabled:cursor-not-allowed disabled:bg-blue-300"
              >
                确认授权
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
