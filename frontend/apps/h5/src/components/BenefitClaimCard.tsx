"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiClient } from "@/lib/api";
import {
  saveClaimRevisitCredential,
  saveLatestClaimRef,
} from "@/lib/claim-revisit";

/** 权益类型 */
type BenefitType =
  | "platform_coupon"
  | "external_link"
  | "private_domain"
  | "form_benefit"
  | "cash_red_packet";

interface BenefitClaimCardProps {
  /** 权益 ID */
  benefitId: string;
  /** 权益类型 */
  benefitType: BenefitType | string;
  /** 权益标题 */
  title: string;
  /** 权益描述 */
  description?: string;
  /** 权益履约配置 */
  configJson?: Record<string, unknown>;
  /** 扫码令牌，用于鉴权 */
  scanToken?: string;
  /** 当前扫码 public_id，用于展示授权上下文。 */
  publicId?: string;
  /** 企业微信转化模式 */
  wecomMode?: "none" | "guide" | "required";
  /** 领取成功回调 */
  onClaimed?: () => void;
}

/** 权益类型对应的图标和配色 */
const BENEFIT_STYLES: Record<
  BenefitType,
  { icon: string; bg: string; text: string; border: string; label: string }
> = {
  platform_coupon: {
    icon: "🎫",
    bg: "bg-info-bg",
    text: "text-info",
    border: "border-info",
    label: "优惠券",
  },
  external_link: {
    icon: "↗",
    bg: "bg-success-bg",
    text: "text-success",
    border: "border-success",
    label: "专属入口",
  },
  private_domain: {
    icon: "💬",
    bg: "bg-warning-bg",
    text: "text-warning",
    border: "border-warning",
    label: "专属服务",
  },
  form_benefit: {
    icon: "✍",
    bg: "bg-warning-bg",
    text: "text-warning",
    border: "border-warning",
    label: "表单权益",
  },
  cash_red_packet: {
    icon: "🧧",
    bg: "bg-danger-bg",
    text: "text-danger",
    border: "border-danger",
    label: "现金红包",
  },
};

/** 按钮文案映射 */
const CLAIM_BUTTON_TEXT: Record<BenefitType, string> = {
  platform_coupon: "立即领取",
  external_link: "领取入口",
  private_domain: "领取服务",
  form_benefit: "领取表单",
  cash_red_packet: "领取红包",
};

const IDEMPOTENT_CLAIM_CONFLICT_CODES = new Set([
  "already_claimed",
  "idempotent",
  "replayed",
]);

function normalizeBenefitType(value: string): BenefitType {
  const legacyMap: Record<string, BenefitType> = {
    coupon: "platform_coupon",
    points: "platform_coupon",
    lottery: "external_link",
    gift: "external_link",
  };
  if (value in BENEFIT_STYLES) return value as BenefitType;
  return legacyMap[value] || "platform_coupon";
}

/**
 * 将分转换为元的显示字符串
 * 1 元 = 100 分
 */
function claimResponse(
  value: unknown,
  benefitId: string
): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null) return null;
  const data = value as Record<string, unknown>;
  return data.benefit_id === benefitId ? data : null;
}

function hasClaimReceipt(data: Record<string, unknown>): boolean {
  return typeof data.claim_id === "string" && data.claim_id.trim().length > 0;
}

function safeWechatAuthUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:" ||
      url.hostname !== "open.weixin.qq.com" ||
      url.pathname !== "/connect/oauth2/authorize"
    ) {
      return null;
    }
    return url.toString();
  } catch {
    return null;
  }
}

interface ClaimRequestGeneration {
  generation: number;
  benefitId: string;
  scanToken?: string;
  controller: AbortController;
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
  configJson = {},
  scanToken,
  publicId,
  wecomMode = "none",
  onClaimed,
}: BenefitClaimCardProps) {
  const [loading, setLoading] = useState(false);
  const [claimed, setClaimed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showPhoneModal, setShowPhoneModal] = useState(false);
  const [phone, setPhone] = useState("");
  const [wecomPrompt, setWecomPrompt] = useState<{
    message: string;
    qrCode?: string;
  } | null>(null);
  const [wechatConsentGranted, setWechatConsentGranted] = useState(false);
  const lastClickRef = useRef(0);
  const generationRef = useRef(0);
  const activeControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const router = useRouter();
  const identityRef = useRef({ benefitId, scanToken });
  identityRef.current = { benefitId, scanToken };

  const normalizedBenefitType = normalizeBenefitType(benefitType);
  const style =
    BENEFIT_STYLES[normalizedBenefitType] ?? BENEFIT_STYLES.platform_coupon;
  const buttonText = CLAIM_BUTTON_TEXT[normalizedBenefitType] ?? "立即领取";

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      activeControllerRef.current?.abort();
      activeControllerRef.current = null;
    };
  }, []);

  useEffect(() => {
    generationRef.current += 1;
    activeControllerRef.current?.abort();
    activeControllerRef.current = null;
    lastClickRef.current = 0;
    setLoading(false);
    setClaimed(false);
    setError(null);
    setShowPhoneModal(false);
    setPhone("");
    setWecomPrompt(null);
    setWechatConsentGranted(false);
  }, [benefitId, scanToken]);

  const beginRequest = useCallback((): ClaimRequestGeneration => {
    activeControllerRef.current?.abort();
    const controller = new AbortController();
    const request = {
      generation: generationRef.current + 1,
      benefitId,
      scanToken,
      controller,
    };
    generationRef.current = request.generation;
    activeControllerRef.current = controller;
    setLoading(true);
    setError(null);
    return request;
  }, [benefitId, scanToken]);

  const isCurrentRequest = useCallback((request: ClaimRequestGeneration) => {
    const identity = identityRef.current;
    return (
      mountedRef.current &&
      !request.controller.signal.aborted &&
      generationRef.current === request.generation &&
      identity.benefitId === request.benefitId &&
      identity.scanToken === request.scanToken
    );
  }, []);

  const finishRequest = useCallback(
    (request: ClaimRequestGeneration) => {
      if (!isCurrentRequest(request)) return;
      if (activeControllerRef.current === request.controller) {
        activeControllerRef.current = null;
      }
      setLoading(false);
    },
    [isCurrentRequest]
  );

  /**
   * 受理成功后进入专属结果页：保存回访凭证（scan_token 时效后仍可恢复查询），
   * 跳转携带 claim_id；领取卡标记已领取防重复点击。
   */
  const navigateToClaimResult = useCallback(
    (data: Record<string, unknown>) => {
      const receiptClaimId = String(data.claim_id);
      if (
        typeof data.revisit_credential === "string" &&
        data.revisit_credential
      ) {
        saveClaimRevisitCredential(receiptClaimId, data.revisit_credential);
      }
      if (publicId) {
        // 回访入口：重扫该码时可提示"查看我的红包"（kc6d.7）
        saveLatestClaimRef(publicId, receiptClaimId);
      }
      setClaimed(true);
      onClaimed?.();
      router.push(
        `/redpacket/result?claim_id=${encodeURIComponent(receiptClaimId)}`
      );
    },
    [onClaimed, publicId, router]
  );

  const handleClaim = useCallback(async () => {
    if (loading || claimed) return;
    const now = Date.now();
    if (now - lastClickRef.current < 1000) return; // 1秒防抖
    lastClickRef.current = now;
    const request = beginRequest();

    try {
      const res = await apiClient.post(
        "/benefit-claims",
        {
          benefit_id: benefitId,
        },
        {
          signal: request.controller.signal,
          headers: scanToken
            ? { Authorization: `Bearer ${scanToken}` }
            : undefined,
        }
      );
      if (!isCurrentRequest(request)) return;

      const data = claimResponse(res.data, benefitId);
      if (!data) {
        setError("领取结果异常，请刷新页面后重试");
        return;
      }

      if (data.status === "pending" && hasClaimReceipt(data)) {
        navigateToClaimResult(data);
        return;
      }

      // 现金红包需要微信 OAuth 授权
      if (data.status === "require_wechat_auth") {
        if (data.auth_url_path !== "/wechat/auth-url") {
          setError("获取授权地址失败，请刷新页面后重试");
          return;
        }
        try {
          // 获取微信 OAuth 跳转地址
          const authRes = await apiClient.post(
            "/wechat/auth-url",
            {
              benefit_id: benefitId,
              scan_token: scanToken,
              consent_granted: true,
            },
            { signal: request.controller.signal }
          );
          if (!isCurrentRequest(request)) return;
          const authData = authRes.data as { auth_url?: unknown };
          const authUrl = safeWechatAuthUrl(authData.auth_url);
          if (authUrl) {
            window.location.href = authUrl;
            return;
          }
          setError("获取授权地址失败，请刷新页面后重试");
          return;
        } catch {
          if (!isCurrentRequest(request)) return;
          setError("获取授权地址失败，请重试");
          return;
        }
      }

      if (data.status === "claimed" && hasClaimReceipt(data)) {
        setWecomPrompt(null);
        setClaimed(true);
        onClaimed?.();
        return;
      }

      setError("领取结果异常，请刷新页面后重试");
    } catch (err: unknown) {
      if (!isCurrentRequest(request)) return;
      const response =
        typeof err === "object" && err !== null && "response" in err
          ? (
              err as {
                response?: {
                  status?: number;
                  data?: {
                    code?: string;
                    detail?:
                      | string
                      | { code?: string; message?: string; qr_code?: string };
                  };
                };
              }
            ).response
          : undefined;
      const detail = response?.data?.detail;
      const errorCode =
        typeof detail === "object" ? detail.code : response?.data?.code;
      // 判断是否需要手机号授权
      if (errorCode === "require_auth") {
        setShowPhoneModal(true);
        return;
      }
      if (errorCode === "require_wecom_contact") {
        setWecomPrompt({
          message:
            typeof detail === "object"
              ? detail.message || "请先添加企业微信，再继续领取权益"
              : "请先添加企业微信，再继续领取权益",
          qrCode: typeof detail === "object" ? detail.qr_code : undefined,
        });
        return;
      }
      if (response?.status === 409) {
        if (
          typeof errorCode === "string" &&
          IDEMPOTENT_CLAIM_CONFLICT_CODES.has(errorCode)
        ) {
          setWecomPrompt(null);
          setClaimed(true);
          onClaimed?.();
          return;
        }
        if (errorCode === "launch_release_not_current") {
          setError("活动内容已更新，请重新扫码或刷新页面后领取");
          return;
        }
        if (errorCode === "benefit_not_in_launch_release") {
          setError("该权益当前不可领取，请刷新页面查看最新活动");
          return;
        }
        setError("领取失败，请刷新页面后重试");
        return;
      }
      setError("领取失败，请稍后重试");
    } finally {
      finishRequest(request);
    }
  }, [
    benefitId,
    scanToken,
    loading,
    claimed,
    onClaimed,
    beginRequest,
    finishRequest,
    isCurrentRequest,
    navigateToClaimResult,
  ]);

  const handlePhoneSubmit = useCallback(async () => {
    if (!phone || phone.length < 11) return;
    const request = beginRequest();
    try {
      const response = await apiClient.post(
        "/benefit-claims",
        {
          benefit_id: benefitId,
          phone,
        },
        {
          signal: request.controller.signal,
          headers: scanToken
            ? { Authorization: `Bearer ${scanToken}` }
            : undefined,
        }
      );
      if (!isCurrentRequest(request)) return;
      const data = claimResponse(response.data, benefitId);
      if (data?.status === "claimed" && hasClaimReceipt(data)) {
        setClaimed(true);
        setShowPhoneModal(false);
        onClaimed?.();
      } else if (data?.status === "pending" && hasClaimReceipt(data)) {
        setShowPhoneModal(false);
        navigateToClaimResult(data);
      } else {
        setError("领取结果异常，请刷新页面后重试");
      }
    } catch {
      if (!isCurrentRequest(request)) return;
      setError("授权失败，请重试");
    } finally {
      finishRequest(request);
    }
  }, [
    benefitId,
    scanToken,
    phone,
    onClaimed,
    beginRequest,
    finishRequest,
    isCurrentRequest,
    navigateToClaimResult,
  ]);

  const handleShowWeComGuide = useCallback(async () => {
    if (!scanToken || !benefitId) return;
    const request = beginRequest();
    try {
      const { data } = await apiClient.post<{ qr_code?: string }>(
        "/integrations/wecom/contact-way",
        {
          benefit_id: benefitId,
          scan_token: scanToken,
        },
        { signal: request.controller.signal }
      );
      if (!isCurrentRequest(request)) return;
      setWecomPrompt({
        message: "添加企业微信，获取活动提醒和复购服务",
        qrCode: data.qr_code,
      });
    } catch {
      if (!isCurrentRequest(request)) return;
      setError("企业微信添加入口暂不可用，请稍后重试");
    } finally {
      finishRequest(request);
    }
  }, [benefitId, scanToken, beginRequest, finishRequest, isCurrentRequest]);

  return (
    <>
      <div
        className={`rounded-2xl border bg-surface p-4 shadow-sm ${style.border}`}
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
              <h3 className="truncate text-base font-semibold text-foreground">
                {title}
              </h3>
            </div>
            {description && (
              <p className="mt-1 text-sm text-foreground-secondary line-clamp-2">
                {description}
              </p>
            )}
          </div>
        </div>

        {/* 领取按钮 */}
        <div className="mt-4">
          {error && (
            <p className="mb-2 text-center text-xs text-danger">{error}</p>
          )}
          {wecomPrompt && (
            <div className="mb-3 rounded-xl border border-info bg-info-bg p-3 text-center">
              <p className="text-sm font-medium text-info">
                {wecomPrompt.message}
              </p>
              {wecomPrompt.qrCode ? (
                <img
                  src={wecomPrompt.qrCode}
                  alt="企业微信添加二维码"
                  className="mx-auto mt-3 h-40 w-40 rounded-lg bg-surface object-contain p-2"
                />
              ) : (
                <p className="mt-2 text-xs text-info">
                  添加入口暂不可用，请稍后再试。
                </p>
              )}
              <p className="mt-2 text-xs text-info">
                添加后可能需要几秒确认，请返回本页继续领取。
              </p>
            </div>
          )}
          {normalizedBenefitType === "cash_red_packet" && !claimed && (
            <label className="mb-3 flex items-start gap-2 rounded-xl bg-muted px-3 py-2 text-xs text-foreground-secondary">
              <input
                type="checkbox"
                checked={wechatConsentGranted}
                onChange={(event) =>
                  setWechatConsentGranted(event.target.checked)
                }
                className="mt-0.5"
              />
              <span>
                我同意为领取本次红包绑定微信身份，并按隐私政策记录授权。
                {publicId ? `（查验码 ${publicId}）` : ""}
              </span>
            </label>
          )}
          <button
            type="button"
            disabled={
              loading ||
              claimed ||
              (normalizedBenefitType === "cash_red_packet" &&
                !wechatConsentGranted)
            }
            onClick={handleClaim}
            className={`w-full rounded-xl py-2.5 text-sm font-semibold transition-colors ${
              claimed
                ? "cursor-default bg-muted text-foreground-tertiary"
                : loading
                  ? "cursor-wait bg-action/60 text-white"
                  : normalizedBenefitType === "cash_red_packet"
                    ? "bg-danger text-white active:bg-danger"
                    : "bg-action text-white active:bg-action-active"
            }`}
          >
            {claimed
              ? "已领取"
              : loading
                ? "领取中..."
                : wecomPrompt
                  ? "我已添加，继续领取"
                  : buttonText}
          </button>
          {claimed && normalizedBenefitType === "platform_coupon" && (
            <div className="mt-3 rounded-xl bg-info-bg px-3 py-2 text-center text-sm text-info">
              {typeof configJson.coupon_code === "string" &&
              configJson.coupon_code
                ? `券码：${configJson.coupon_code}`
                : "优惠券已领取，请按活动说明使用。"}
            </div>
          )}
          {claimed &&
            normalizedBenefitType === "external_link" &&
            typeof configJson.url === "string" && (
              <a
                href={configJson.url}
                className="mt-3 block rounded-xl bg-success py-2.5 text-center text-sm font-semibold text-white"
              >
                {(configJson.link_text as string) || "立即前往"}
              </a>
            )}
          {claimed && normalizedBenefitType === "private_domain" && (
            <div className="mt-3 rounded-xl border border-warning bg-warning-bg p-3 text-center">
              {typeof configJson.group_name === "string" && (
                <p className="text-sm font-medium text-warning">
                  {configJson.group_name}
                </p>
              )}
              {typeof configJson.qr_image_url === "string" ? (
                <img
                  src={configJson.qr_image_url}
                  alt="权益二维码"
                  className="mx-auto mt-2 h-40 w-40 rounded-lg bg-surface object-contain p-2"
                />
              ) : (
                <p className="text-xs text-warning">
                  请联系活动客服获取服务入口。
                </p>
              )}
            </div>
          )}
          {claimed &&
            normalizedBenefitType === "form_benefit" &&
            typeof configJson.form_url === "string" && (
              <a
                href={configJson.form_url}
                className="mt-3 block rounded-xl bg-warning py-2.5 text-center text-sm font-semibold text-white"
              >
                填写表单
              </a>
            )}
          {wecomMode === "guide" && !claimed && !wecomPrompt && (
            <button
              type="button"
              disabled={loading}
              onClick={handleShowWeComGuide}
              className="mt-2 w-full rounded-xl border border-info bg-surface py-2.5 text-sm font-semibold text-info active:bg-info-bg disabled:cursor-not-allowed disabled:text-info"
            >
              添加企业微信获取专属服务
            </button>
          )}
        </div>
      </div>

      {/* 手机号授权弹窗 */}
      {showPhoneModal && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center">
          <div className="w-full max-w-md rounded-t-2xl bg-surface p-6 shadow-xl sm:rounded-2xl">
            <h4 className="text-base font-semibold text-foreground">
              授权手机号
            </h4>
            <p className="mt-1 text-sm text-foreground-secondary">
              该权益需要授权手机号后才能领取
            </p>
            <input
              type="tel"
              maxLength={11}
              placeholder="请输入手机号"
              value={phone}
              onChange={(e) => setPhone(e.target.value.replace(/\D/g, ""))}
              className="mt-4 w-full rounded-xl border border-base px-4 py-2.5 text-sm outline-none focus:border-focus-ring focus:ring-1 focus:ring-focus-ring"
            />
            <div className="mt-4 flex gap-3">
              <button
                type="button"
                onClick={() => setShowPhoneModal(false)}
                className="flex-1 rounded-xl border border-base py-2.5 text-sm text-foreground-secondary active:bg-muted"
              >
                取消
              </button>
              <button
                type="button"
                disabled={phone.length < 11 || loading}
                onClick={handlePhoneSubmit}
                className="flex-1 rounded-xl bg-action py-2.5 text-sm font-semibold text-white active:bg-action-active disabled:cursor-not-allowed disabled:bg-action/60"
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
