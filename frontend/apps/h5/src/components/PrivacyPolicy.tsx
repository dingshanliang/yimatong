"use client";

import { useState } from "react";
import { apiClient } from "@/lib/api";

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

/**
 * 隐私政策组件
 *
 * 展示隐私政策内容，提供同意/拒绝操作按钮。
 * 支持富文本 HTML 和纯文本两种展示模式。
 * 同意后提供撤回授权入口。
 * 同意/撤回操作会调用后端 consent API（best-effort，失败不阻断 UI）。
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

  if (!content) return null;

  const handleAccept = async () => {
    setAccepted(true);
    onAccept?.();
    try {
      const res = await apiClient.post("/public/consents", {
        consent_type: "privacy_policy",
        public_id: publicId,
      });
      setConsentId(res.data?.id || null);
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
      }
    } catch {
      // best-effort
    }
  };

  return (
    <section className="rounded-2xl bg-white p-4 shadow-sm">
      <h3 className="text-base font-semibold text-gray-900">隐私政策</h3>

      {/* 政策内容 */}
      <div className="mt-3 max-h-64 overflow-y-auto">
        {/* 判断是否为 HTML 富文本 */}
        {content.trim().startsWith("<") ? (
          <div
            className="prose prose-sm max-w-none text-sm text-gray-600 [&_a]:text-blue-600 [&_a]:underline"
            dangerouslySetInnerHTML={{ __html: content }}
          />
        ) : (
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-600">
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
            className="flex-1 rounded-xl border border-gray-200 py-2.5 text-sm font-medium text-gray-700 transition-colors active:bg-gray-50"
          >
            拒绝
          </button>
          <button
            type="button"
            onClick={handleAccept}
            className="flex-1 rounded-xl bg-blue-600 py-2.5 text-sm font-semibold text-white transition-colors active:bg-blue-700"
          >
            同意
          </button>
        </div>
      )}

      {/* 已同意状态 */}
      {showActions && accepted && !showRevokeConfirm && (
        <div className="mt-4 flex items-center justify-between">
          <span className="inline-flex items-center gap-1.5 text-sm text-green-700">
            <svg
              className="h-4 w-4"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
            已同意隐私政策
          </span>
          <button
            type="button"
            onClick={() => setShowRevokeConfirm(true)}
            className="text-xs text-gray-400 underline-offset-2 hover:underline"
          >
            撤回授权
          </button>
        </div>
      )}

      {/* 撤回确认弹窗 */}
      {showRevokeConfirm && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center">
          <div className="w-full max-w-md rounded-t-2xl bg-white p-6 shadow-xl sm:rounded-2xl">
            <h4 className="text-base font-semibold text-gray-900">
              撤回授权确认
            </h4>
            <p className="mt-2 text-sm text-gray-600">
              撤回授权后可能影响部分功能的正常使用，确定要撤回吗？
            </p>
            <div className="mt-4 flex gap-3">
              <button
                type="button"
                onClick={() => setShowRevokeConfirm(false)}
                className="flex-1 rounded-xl border border-gray-200 py-2.5 text-sm text-gray-700 active:bg-gray-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleRevoke}
                className="flex-1 rounded-xl bg-red-600 py-2.5 text-sm font-semibold text-white active:bg-red-700"
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
