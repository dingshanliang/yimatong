"use client";

import { CircleCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { apiClient } from "@/lib/api";
import { loadConsentReceiptStatus } from "@/lib/consentReceiptStatus";
import { sanitizeHtml } from "@/lib/sanitize";

interface PrivacyPolicyProps {
  /** 隐私政策内容（支持 HTML 富文本或纯文本） */
  content?: string;
  /** 同意回调 */
  onAccept?: () => void;
  /** 拒绝回调 */
  onReject?: () => void;
  /** 是否显示操作按钮（默认 true） */
  showActions?: boolean;
  /** 关联的 public_id（用于 consent API） */
  publicId?: string;
  scanToken?: string;
  purpose?: string;
}

interface CurrentPolicy {
  purpose: string;
  policy_version: string;
  policy_digest: string;
  policy_title: string;
  policy_content: string;
}

/** consentId 持久化 key（按 public_id 隔离，避免跨码混淆）。 */
function consentStorageKey(publicId?: string) {
  return `consent_id:${publicId || "anonymous"}`;
}

function saveConsentId(publicId: string | undefined, id: string | null) {
  if (typeof window === "undefined") return;
  try {
    if (id) {
      localStorage.setItem(consentStorageKey(publicId), id);
    } else {
      localStorage.removeItem(consentStorageKey(publicId));
    }
  } catch {
    // ignore storage errors
  }
}

function loadConsentId(publicId: string | undefined) {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(consentStorageKey(publicId));
  } catch {
    return null;
  }
}

/**
 * 隐私政策组件
 *
 * 展示隐私政策内容，提供同意/拒绝操作按钮。
 * 支持富文本 HTML 和纯文本两种展示模式。
 * 同意后提供撤回授权入口。
 * 同意/撤回操作会调用后端 consent API（best-effort，失败不阻断 UI）。
 *
 * yimatong-zgb1.5：consentId 持久化到 localStorage（按 public_id 隔离），
 * 刷新后仍可撤回（修之前只在 React state 里、刷新即丢失的 bug）。
 */
export function PrivacyPolicy({
  content,
  onAccept,
  onReject,
  showActions = true,
  publicId,
  scanToken,
  purpose = "privacy_policy",
}: PrivacyPolicyProps) {
  const [accepted, setAccepted] = useState(false);
  const [consentId, setConsentId] = useState<string | null>(null);
  const [showRevokeConfirm, setShowRevokeConfirm] = useState(false);
  const [policy, setPolicy] = useState<CurrentPolicy | null>(null);
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const grantIdempotency = useRef(crypto.randomUUID());
  const withdrawIdempotency = useRef(crypto.randomUUID());
  const requestGeneration = useRef(0);

  useEffect(() => {
    const generation = ++requestGeneration.current;
    setPolicy(null);
    setAccepted(false);
    setConsentId(null);
    setShowRevokeConfirm(false);
    setError("");
    grantIdempotency.current = crypto.randomUUID();
    withdrawIdempotency.current = crypto.randomUUID();
    if (!publicId || publicId === "preview" || !scanToken) return;
    let active = true;
    const load = async () => {
      try {
        const policyResponse = await apiClient.get("/public/consents/policy", {
          params: { purpose },
          headers: { Authorization: `Bearer ${scanToken}` },
        });
        if (!active || generation !== requestGeneration.current) return;
        const currentPolicy = policyResponse.data as CurrentPolicy;
        setPolicy(currentPolicy);

        const storedConsentId = loadConsentId(publicId);
        if (!storedConsentId) return;
        const receiptResult = await loadConsentReceiptStatus(
          () =>
            apiClient.get(`/public/consents/${storedConsentId}/status`, {
              headers: { Authorization: `Bearer ${scanToken}` },
            }),
          () => active && generation === requestGeneration.current
        );
        if (receiptResult.kind === "cancelled") return;
        if (receiptResult.kind === "clear") {
          saveConsentId(publicId, null);
          return;
        }
        if (receiptResult.kind === "preserve") return;
        const receipt = receiptResult.data as {
          consent_id?: string;
          status?: string;
          purpose?: string;
          policy_version?: string;
          policy_digest?: string;
        };
        if (
          receipt.consent_id === storedConsentId &&
          receipt.status === "granted" &&
          receipt.purpose === currentPolicy.purpose &&
          receipt.policy_version === currentPolicy.policy_version &&
          receipt.policy_digest === currentPolicy.policy_digest
        ) {
          setConsentId(storedConsentId);
          setAccepted(true);
        } else {
          saveConsentId(publicId, null);
        }
      } catch {
        if (active && generation === requestGeneration.current) {
          setError("隐私政策暂时无法加载，请稍后重试");
        }
      }
    };
    void load();
    return () => {
      active = false;
    };
  }, [publicId, purpose, scanToken]);

  const visibleContent = policy?.policy_content || content;
  if (!visibleContent) return null;

  const handleAccept = async () => {
    if (!policy || isSubmitting) return;
    const generation = requestGeneration.current;
    setIsSubmitting(true);
    setError("");
    try {
      const res = await apiClient.post(
        "/public/consents",
        {
          purpose: policy.purpose,
          policy_version: policy.policy_version,
          policy_digest: policy.policy_digest,
          idempotency_key: grantIdempotency.current,
        },
        { headers: scanToken ? { Authorization: `Bearer ${scanToken}` } : {} }
      );
      if (generation !== requestGeneration.current) return;
      const id = res.data?.consent_id || null;
      if (!id || res.data?.status !== "granted")
        throw new Error("invalid receipt");
      setConsentId(id);
      setAccepted(true);
      grantIdempotency.current = crypto.randomUUID();
      onAccept?.();
      if (id && publicId) {
        saveConsentId(publicId, id);
      }
    } catch {
      if (generation === requestGeneration.current) {
        setError("授权未保存，请重试");
      }
    } finally {
      if (generation === requestGeneration.current) setIsSubmitting(false);
    }
  };

  const handleReject = () => {
    onReject?.();
  };

  const handleRevoke = async () => {
    const generation = requestGeneration.current;
    setIsSubmitting(true);
    setError("");
    try {
      if (consentId) {
        const response = await apiClient.post(
          `/public/consents/${consentId}/withdraw`,
          { idempotency_key: withdrawIdempotency.current },
          { headers: scanToken ? { Authorization: `Bearer ${scanToken}` } : {} }
        );
        if (generation !== requestGeneration.current) return;
        if (response.data?.status !== "withdrawn")
          throw new Error("invalid receipt");
        // 撤回成功后清除持久化的 consentId
        saveConsentId(publicId, null);
        setConsentId(null);
        setAccepted(false);
        setShowRevokeConfirm(false);
        withdrawIdempotency.current = crypto.randomUUID();
      }
    } catch {
      if (generation === requestGeneration.current) {
        setShowRevokeConfirm(false);
        setError("撤回未完成，请重试");
      }
    } finally {
      if (generation === requestGeneration.current) setIsSubmitting(false);
    }
  };

  return (
    <section className="rounded-2xl bg-surface p-4 shadow-sm">
      <h3 className="text-base font-semibold text-foreground">隐私政策</h3>

      {/* 政策内容 */}
      <div className="mt-3 max-h-64 overflow-y-auto">
        {/* 判断是否为 HTML 富文本 */}
        {visibleContent.trim().startsWith("<") ? (
          <div
            className="prose prose-sm max-w-none text-sm text-foreground-secondary [&_a]:text-link [&_a]:underline"
            dangerouslySetInnerHTML={{ __html: sanitizeHtml(visibleContent) }}
          />
        ) : (
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground-secondary">
            {visibleContent}
          </p>
        )}
      </div>

      {/* 操作按钮 */}
      {showActions && !accepted && (
        <div className="mt-4 flex gap-3">
          <button
            type="button"
            onClick={handleReject}
            className="flex-1 rounded-xl border border-base py-2.5 text-sm font-medium text-foreground-secondary transition-colors active:bg-muted"
          >
            拒绝
          </button>
          <button
            type="button"
            onClick={handleAccept}
            disabled={!policy || isSubmitting}
            className="flex-1 rounded-xl bg-action py-2.5 text-sm font-semibold text-on-action transition-colors active:bg-action-active"
          >
            {isSubmitting
              ? "保存中..."
              : `同意（版本 ${policy?.policy_version || "加载中"}）`}
          </button>
        </div>
      )}
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}

      {/* 已同意状态 */}
      {showActions && accepted && !showRevokeConfirm && (
        <div className="mt-4 flex items-center justify-between">
          <span className="inline-flex items-center gap-1.5 text-sm text-success">
            <CircleCheck className="h-4 w-4" aria-hidden="true" />
            已同意隐私政策
          </span>
          <button
            type="button"
            onClick={() => setShowRevokeConfirm(true)}
            className="text-xs text-foreground-tertiary underline-offset-2 hover:underline"
          >
            撤回授权
          </button>
        </div>
      )}

      {/* 撤回确认弹窗 */}
      {showRevokeConfirm && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center">
          <div className="w-full max-w-md rounded-t-2xl bg-surface p-6 shadow-xl sm:rounded-2xl">
            <h4 className="text-base font-semibold text-foreground">
              撤回授权确认
            </h4>
            <p className="mt-2 text-sm text-foreground-secondary">
              撤回授权后可能影响部分功能的正常使用，确定要撤回吗？
            </p>
            <div className="mt-4 flex gap-3">
              <button
                type="button"
                onClick={() => setShowRevokeConfirm(false)}
                className="flex-1 rounded-xl border border-base py-2.5 text-sm text-foreground-secondary active:bg-muted"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleRevoke}
                className="flex-1 rounded-xl bg-danger py-2.5 text-sm font-semibold text-on-action active:bg-danger"
              >
                确认撤回
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
