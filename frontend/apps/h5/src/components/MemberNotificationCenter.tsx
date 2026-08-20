"use client";

import { useEffect, useRef, useState } from "react";
import { Bell, RefreshCw } from "lucide-react";

import { apiClient } from "@/lib/api";

interface NotificationItem {
  id: string;
  notification_class: "service" | "marketing";
  notification_type: string;
  title: string;
  body: string;
  action_path?: string | null;
  occurred_at: string;
}

interface NotificationPreference {
  marketing_enabled: boolean;
  service_wechat_enabled: boolean;
}

interface MarketingPolicy {
  purpose: string;
  policy_version: string;
  policy_digest: string;
  policy_title: string;
  policy_content: string;
}

interface WeChatSubscriptionApi {
  requestSubscribeMessage(options: {
    tmplIds: string[];
    success(result: Record<string, string>): void;
    fail(): void;
  }): void;
}

declare global {
  interface Window {
    wx?: WeChatSubscriptionApi;
  }
}

const MARKETING_TEMPLATE_CODE = "coupon_expiry";

function dateLabel(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function requestWechatSubscription(templateId: string) {
  return new Promise<void>((resolve, reject) => {
    const wx = window.wx;
    if (!wx?.requestSubscribeMessage) {
      reject(new Error("wechat_subscription_unavailable"));
      return;
    }
    wx.requestSubscribeMessage({
      tmplIds: [templateId],
      success(result) {
        if (result[templateId] === "accept") resolve();
        else reject(new Error("wechat_subscription_declined"));
      },
      fail() {
        reject(new Error("wechat_subscription_failed"));
      },
    });
  });
}

export function MemberNotificationCenter({
  scanToken,
  refreshKey = 0,
}: {
  scanToken?: string;
  refreshKey?: number;
}) {
  const [notifications, setNotifications] = useState<NotificationItem[] | null>(
    null
  );
  const [preference, setPreference] = useState<NotificationPreference | null>(
    null
  );
  const [policy, setPolicy] = useState<MarketingPolicy | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [retryKey, setRetryKey] = useState(0);
  const consentIdempotency = useRef(crypto.randomUUID());
  const templateId = process.env.NEXT_PUBLIC_WECHAT_COUPON_EXPIRY_TEMPLATE_ID;

  useEffect(() => {
    if (!scanToken) {
      setNotifications(null);
      setPreference(null);
      return;
    }
    let active = true;
    setError("");
    const headers = { Authorization: `Bearer ${scanToken}` };
    Promise.all([
      apiClient.get("/consumers/membership/notifications", { headers }),
      apiClient.get("/consumers/membership/notification-preferences", {
        headers,
      }),
      apiClient.get("/public/consents/policy", {
        params: { purpose: "lead_capture" },
        headers,
      }),
    ])
      .then(([inbox, preferenceResponse, policyResponse]) => {
        if (!active) return;
        setNotifications(
          Array.isArray(inbox.data) ? (inbox.data as NotificationItem[]) : []
        );
        setPreference(preferenceResponse.data as NotificationPreference);
        setPolicy(policyResponse.data as MarketingPolicy);
      })
      .catch((requestError: { response?: { status?: number } }) => {
        if (!active) return;
        if (
          requestError.response?.status === 401 ||
          requestError.response?.status === 404
        ) {
          setNotifications(null);
          setPreference(null);
          return;
        }
        setError("消息暂时无法更新，请稍后重试");
      });
    return () => {
      active = false;
    };
  }, [refreshKey, retryKey, scanToken]);

  if (!scanToken || (notifications === null && preference === null && !error))
    return null;

  const subscribe = async () => {
    if (!scanToken || !policy || !templateId || saving) return;
    setSaving(true);
    setError("");
    try {
      await requestWechatSubscription(templateId);
      const consent = await apiClient.post(
        "/public/consents",
        {
          purpose: policy.purpose,
          policy_version: policy.policy_version,
          policy_digest: policy.policy_digest,
          idempotency_key: consentIdempotency.current,
        },
        { headers: { Authorization: `Bearer ${scanToken}` } }
      );
      if (
        consent.data?.status !== "granted" ||
        typeof consent.data?.consent_id !== "string"
      ) {
        throw new Error("invalid_consent_receipt");
      }
      const { data } = await apiClient.post(
        "/consumers/membership/notification-preferences/marketing-subscription",
        {
          marketing_consent_id: consent.data.consent_id,
          template_code: MARKETING_TEMPLATE_CODE,
        },
        { headers: { Authorization: `Bearer ${scanToken}` } }
      );
      setPreference(data as NotificationPreference);
    } catch {
      setError("未能开启微信提醒。消息中心仍会保留订单与优惠券事实。");
    } finally {
      setSaving(false);
    }
  };

  const unsubscribe = async () => {
    if (!scanToken || saving) return;
    setSaving(true);
    setError("");
    try {
      const { data } = await apiClient.delete(
        "/consumers/membership/notification-preferences/marketing-subscription",
        { headers: { Authorization: `Bearer ${scanToken}` } }
      );
      setPreference(data as NotificationPreference);
    } catch {
      setError("退订没有保存成功，请重试");
    } finally {
      setSaving(false);
    }
  };

  const canRequestWechat = Boolean(
    templateId &&
    typeof window !== "undefined" &&
    window.wx?.requestSubscribeMessage
  );

  return (
    <section
      className="mx-4 mt-3 overflow-hidden rounded-xl border border-base bg-surface"
      aria-labelledby="member-notification-title"
    >
      <div className="flex items-start justify-between gap-3 px-4 py-4">
        <div className="min-w-0">
          <h2
            id="member-notification-title"
            className="flex items-center gap-2 font-semibold text-foreground"
          >
            <Bell className="h-5 w-5 text-action" aria-hidden="true" />
            会员消息
          </h2>
          <p className="mt-1 text-sm text-foreground-secondary">
            订单和优惠券事实会一直保留在这里。
          </p>
        </div>
        <span className="shrink-0 text-sm tabular-nums text-foreground-secondary">
          {notifications?.length ?? 0} 条
        </span>
      </div>

      {error && (
        <div
          className="mx-4 mb-3 flex items-center justify-between gap-3 rounded-xl bg-danger-bg px-3 py-2 text-sm text-danger"
          role="alert"
        >
          <span>{error}</span>
          <button
            type="button"
            onClick={() => setRetryKey((key) => key + 1)}
            className="inline-flex shrink-0 items-center gap-1 font-medium"
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            重试
          </button>
        </div>
      )}

      {preference && (
        <div className="border-y border-base bg-canvas px-4 py-3">
          {preference.marketing_enabled ? (
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm text-foreground-secondary">
                优惠与复购微信提醒已开启
              </p>
              <button
                type="button"
                disabled={saving}
                onClick={() => void unsubscribe()}
                className="shrink-0 text-sm font-medium text-action disabled:opacity-50"
              >
                {saving ? "正在保存…" : "退订营销提醒"}
              </button>
            </div>
          ) : canRequestWechat && policy ? (
            <div>
              <p className="text-sm text-foreground-secondary">
                点击一次即可同意品牌营销规则并申请本次微信订阅授权。
              </p>
              <button
                type="button"
                disabled={saving}
                onClick={() => void subscribe()}
                className="mt-2 w-full rounded-xl bg-action px-4 py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-action/50"
              >
                {saving ? "正在开启…" : "开启优惠与复购提醒"}
              </button>
            </div>
          ) : (
            <p className="text-sm text-foreground-secondary">
              当前入口暂不支持微信订阅；消息中心不受影响。
            </p>
          )}
        </div>
      )}

      {notifications?.length === 0 && (
        <p className="px-4 py-5 text-sm text-foreground-secondary">
          暂时没有新消息。订单或优惠券状态变化后会显示在这里。
        </p>
      )}

      {notifications?.map((notification) => (
        <article
          key={notification.id}
          className="border-b border-base px-4 py-3 last:border-b-0"
        >
          <div className="flex items-start justify-between gap-3">
            <p className="min-w-0 font-medium text-foreground break-words">
              {notification.title}
            </p>
            <time
              className="shrink-0 text-xs tabular-nums text-foreground-secondary"
              dateTime={notification.occurred_at}
            >
              {dateLabel(notification.occurred_at)}
            </time>
          </div>
          <p className="mt-1 break-words text-sm text-foreground-secondary">
            {notification.body}
          </p>
          {notification.action_path && (
            <a
              href={notification.action_path}
              className="mt-2 inline-flex text-sm font-medium text-action"
            >
              查看详情
            </a>
          )}
        </article>
      ))}
    </section>
  );
}
