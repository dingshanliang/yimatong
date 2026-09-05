"use client";

import { useState } from "react";
import { ShieldCheck } from "lucide-react";

import { apiClient } from "@/lib/api";

const requestTypes = [
  { value: "access", label: "查阅我的个人信息" },
  { value: "copy", label: "获取个人信息副本" },
  { value: "correct", label: "更正个人信息" },
  { value: "delete", label: "删除非必要个人信息" },
  { value: "restrict", label: "限制继续处理" },
  { value: "withdraw_consent", label: "撤回相关同意" },
  { value: "close_membership", label: "申请注销品牌会员" },
] as const;

export function MemberPrivacyCenter({ scanToken }: { scanToken?: string }) {
  const [open, setOpen] = useState(false);
  const [requestType, setRequestType] =
    useState<(typeof requestTypes)[number]["value"]>("access");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState<{
    id: string;
    response_sla_workdays: number;
  } | null>(null);

  if (!scanToken) return null;

  const submit = async () => {
    if (reason.trim().length < 2 || saving) return;
    setSaving(true);
    setError("");
    try {
      const { data } = await apiClient.post(
        "/consumers/membership/privacy-requests",
        { request_type: requestType, reason: reason.trim(), evidence: {} },
        { headers: { Authorization: `Bearer ${scanToken}` } }
      );
      setReceipt(data as { id: string; response_sla_workdays: number });
    } catch {
      setError("申请暂未提交成功，请确认已恢复品牌会员后重试。");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section
      className="mx-4 mt-3 rounded-xl border border-base bg-surface px-4 py-4"
      aria-labelledby="privacy-rights-title"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2
            id="privacy-rights-title"
            className="flex items-center gap-2 font-semibold text-foreground"
          >
            <ShieldCheck className="h-5 w-5 text-action" aria-hidden="true" />
            个人信息权益
          </h2>
          <p className="mt-1 text-sm text-foreground-secondary">
            向当前品牌提交查阅、更正、删除或限制处理申请。
          </p>
        </div>
        {!receipt && (
          <button
            type="button"
            className="shrink-0 text-sm font-medium text-action"
            onClick={() => setOpen((value) => !value)}
          >
            {open ? "收起" : "提交申请"}
          </button>
        )}
      </div>

      {receipt ? (
        <div
          className="mt-3 rounded-xl bg-success-bg px-3 py-3 text-sm text-success"
          role="status"
        >
          申请已受理。品牌将在 {receipt.response_sla_workdays}{" "}
          个工作日内答复，请保存受理号 {receipt.id.slice(0, 8).toUpperCase()}。
        </div>
      ) : open ? (
        <div className="mt-4 space-y-3 border-t border-base pt-4">
          <label
            className="block text-sm font-medium text-foreground"
            htmlFor="privacy-request-type"
          >
            申请事项
          </label>
          <select
            id="privacy-request-type"
            value={requestType}
            onChange={(event) =>
              setRequestType(event.target.value as typeof requestType)
            }
            className="w-full rounded-xl border border-base bg-surface px-3 py-2.5 text-sm text-foreground"
          >
            {requestTypes.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <label
            className="block text-sm font-medium text-foreground"
            htmlFor="privacy-request-reason"
          >
            情况说明
          </label>
          <textarea
            id="privacy-request-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={3}
            maxLength={1000}
            className="w-full rounded-xl border border-base bg-surface px-3 py-2.5 text-sm text-foreground"
            placeholder="请简要说明需要处理的内容"
          />
          {error && (
            <p className="text-sm text-danger" role="alert">
              {error}
            </p>
          )}
          <button
            type="button"
            disabled={saving || reason.trim().length < 2}
            onClick={() => void submit()}
            className="w-full rounded-xl bg-action px-4 py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-action/50"
          >
            {saving ? "正在提交…" : "确认提交"}
          </button>
          <p className="text-xs text-foreground-secondary">
            营销撤回会立即生效；其他申请核验后处理，依法需保留的信息会转为限制访问。
          </p>
        </div>
      ) : null}
    </section>
  );
}
