"use client";

import { CircleCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { apiClient } from "@/lib/api";
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
}

// yimatong-zgb1.5：政策版本（合规要求"明示版本"）
const PRIVACY_POLICY_VERSION = "2026-07-27-v1";

/** consentId 持久化 key（按 public_id 隔离，避免跨码混淆）。 */
function consentStorageKey(publicId?: string) {
  return `consent_id:${publicId || "anonymous"}`;
}

/** 从 localStorage 恢复 consentId（刷新后仍可撤回）。 */
function loadConsentId(publicId?: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(consentStorageKey(publicId));
  } catch {
    return null;
  }
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
}: PrivacyPolicyProps) {
  const [accepted, setAccepted] = useState(false);
  const [consentId, setConsentId] = useState<string | null>(null);
  const [showRevokeConfirm, setShowRevokeConfirm] = useState(false);

  // yimatong-zgb1.5：挂载时从 localStorage 恢复 consentId，恢复"已同意"视图
  useEffect(() => {
    const stored = loadConsentId(publicId);
    if (stored) {
      setConsentId(stored);
      setAccepted(true);
    }
  }, [publicId]);

  if (!content) return null;

  const handleAccept = async () => {
    setAccepted(true);
    onAccept?.();
    try {
      const res = await apiClient.post("/public/consents", {
        consent_type: "privacy",
        public_id: publicId,
        scenario: "privacy_policy",
        policy_version: PRIVACY_POLICY_VERSION,
      });
      const id = res.data?.id || null;
      setConsentId(id);
      if (id && publicId) {
        saveConsentId(publicId, id);
      }
    } catch {
      // best-effort: consent failure does not block UX
    }
  };

  const handleReject = () => {
    onReject?.();
  };

  const handleRevoke = async () => {
    setAccepted(false);
    setShowRevokeConfirm(false);
    try {
      if (consentId) {
        await apiClient.post(`/public/consents/${consentId}/withdraw`);
        // 撤回成功后清除持久化的 consentId
        saveConsentId(publicId, null);
        setConsentId(null);
      }
    } catch {
      // best-effort
    }
  };

  return (
    <section className="rounded-2xl bg-surface p-4 shadow-sm">
      <h3 className="text-base font-semibold text-foreground">隐私政策</h3>

      {/* 政策内容 */}
      <div className="mt-3 max-h-64 overflow-y-auto">
        {/* 判断是否为 HTML 富文本 */}
        {content.trim().startsWith("<") ? (
          <div
            className="prose prose-sm max-w-none text-sm text-foreground-secondary [&_a]:text-link [&_a]:underline"
            dangerouslySetInnerHTML={{ __html: sanitizeHtml(content) }}
          />
        ) : (
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground-secondary">
            {content}
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
            className="flex-1 rounded-xl bg-action py-2.5 text-sm font-semibold text-on-action transition-colors active:bg-action-active"
          >
            同意（版本 {PRIVACY_POLICY_VERSION}）
          </button>
        </div>
      )}

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
