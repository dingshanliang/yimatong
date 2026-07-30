"use client";

import { Check } from "lucide-react";
import { useState } from "react";
import { apiClient } from "@/lib/api";

interface LeadFormProps {
  publicId: string;
  scanToken?: string;
  title?: string;
  subtitle?: string;
  submitLabel?: string;
  fields?: string[];
}

const FIELD_CONFIG: Record<
  string,
  {
    label: string;
    type: string;
    placeholder: string;
    inputMode?: string;
    autoComplete?: string;
  }
> = {
  name: { label: "姓名", type: "text", placeholder: "请输入姓名" },
  phone: {
    label: "手机号",
    type: "tel",
    placeholder: "请输入手机号",
    inputMode: "tel",
    autoComplete: "tel",
  },
  region: {
    label: "所在地区",
    type: "text",
    placeholder: "请输入所在地区",
    autoComplete: "address-level1",
  },
  intention: { label: "意向说明", type: "text", placeholder: "请简述您的需求" },
};

const DEFAULT_FIELDS = ["name", "phone"];

// yimatong-zgb1.5：隐私政策版本（场景 lead_capture）。
// 真实部署应从后端拉取，当前用固定版本占位（合规要求"明示版本"）。
const PRIVACY_POLICY_VERSION = "2026-07-27-v1";

export function LeadForm({
  publicId,
  scanToken,
  title,
  subtitle,
  submitLabel,
  fields: configuredFields,
}: LeadFormProps) {
  const [submitted, setSubmitted] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");
  // yimatong-zgb1.5 AC3：采集手机号（PII）前必须勾选隐私授权
  const [consentAgreed, setConsentAgreed] = useState(false);
  const activeFields = configuredFields?.length
    ? configuredFields
    : DEFAULT_FIELDS;
  const needsPhone = activeFields.includes("phone");

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (isSubmitting) return;
    // PII 字段（手机号）采集前必须勾选 consent
    if (needsPhone && !consentAgreed) {
      setError("请先阅读并同意隐私政策");
      return;
    }
    setIsSubmitting(true);
    setError("");
    const fd = new FormData(e.currentTarget);
    const body: Record<string, unknown> = { public_id: publicId };
    for (const f of activeFields) {
      body[f] = fd.get(f) || "";
    }

    try {
      // yimatong-zgb1.5：采集 PII 前先 grant privacy consent（场景 lead_capture + 版本）
      if (needsPhone && consentAgreed) {
        try {
          await apiClient.post(
            "/public/consents",
            {
              consent_type: "privacy",
              public_id: publicId,
              scenario: "lead_capture",
              policy_version: PRIVACY_POLICY_VERSION,
            },
            {
              headers: scanToken
                ? { Authorization: `Bearer ${scanToken}` }
                : {},
            }
          );
        } catch {
          // consent 写入失败不阻断主流程（后端会再次校验），但记日志
          console.warn("failed to grant consent before lead-capture");
        }
      }
      const res = await apiClient.post("/consumers/lead-capture", body, {
        headers: scanToken ? { Authorization: `Bearer ${scanToken}` } : {},
      });
      if (res.data?.consumer_id && typeof window !== "undefined") {
        localStorage.setItem("consumer_id", res.data.consumer_id);
      }
      setSubmitted(true);
    } catch (err: unknown) {
      // yimatong-zgb1.5：后端 consent gating 返回 403 consent_required / consent_withdrawn
      const status = (
        err as { response?: { status?: number; data?: { detail?: string } } }
      ).response?.status;
      const detail = (err as { response?: { data?: { detail?: string } } })
        .response?.data?.detail;
      if (status === 403 && detail === "consent_withdrawn") {
        setError(
          "已撤回隐私授权，无法采集个人信息。如需重新授权，请联系品牌客服。"
        );
      } else if (status === 403 && detail === "consent_required") {
        setError("需要先同意隐私政策才能提交手机号。");
      } else {
        setError("提交失败，请稍后重试");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <div className="mt-3 rounded-2xl bg-surface p-4 shadow-sm text-center">
        <div className="mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-success-bg">
          <Check className="h-5 w-5 text-success" aria-hidden="true" />
        </div>
        <p className="text-sm font-medium text-foreground">提交成功</p>
      </div>
    );
  }

  return (
    <div className="mt-3 rounded-2xl bg-surface p-4 shadow-sm">
      <h2 className="text-base font-semibold text-foreground">
        {title || "留下联系方式"}
      </h2>
      <p className="mt-1 text-xs text-foreground-tertiary">
        {subtitle || "品牌将通过此信息与您联系（选填）"}
      </p>
      <form className="mt-3 space-y-3" onSubmit={handleSubmit}>
        {activeFields.map((f) => {
          const cfg = FIELD_CONFIG[f] || {
            label: f,
            type: "text",
            placeholder: `请输入${f}`,
          };
          return (
            <div key={f}>
              <label
                htmlFor={`lead-${f}`}
                className="block text-sm font-medium text-foreground-secondary"
              >
                {cfg.label}
              </label>
              <input
                id={`lead-${f}`}
                name={f}
                type={cfg.type}
                inputMode={
                  (cfg as { inputMode?: string })
                    .inputMode as React.InputHTMLAttributes<HTMLInputElement>["inputMode"]
                }
                autoComplete={
                  (cfg as { autoComplete?: string }).autoComplete as string
                }
                placeholder={cfg.placeholder}
                className="mt-1 block w-full rounded-xl border border-base px-3 py-2.5 text-sm text-foreground placeholder:text-foreground-tertiary focus:border-focus-ring focus:ring-1 focus:ring-focus-ring focus:outline-none"
              />
            </div>
          );
        })}
        {/* yimatong-zgb1.5 AC3：PII 采集前的隐私授权勾选 + 场景版本明示 */}
        {needsPhone && (
          <label className="flex items-start gap-2 rounded-xl bg-muted p-2.5 text-xs text-foreground-secondary">
            <input
              type="checkbox"
              checked={consentAgreed}
              onChange={(e) => setConsentAgreed(e.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 rounded border-strong text-action focus:ring-focus-ring"
              aria-label="同意隐私政策"
            />
            <span>
              我已阅读并同意《隐私政策》（版本 {PRIVACY_POLICY_VERSION}
              ），授权品牌在留资场景下采集我的姓名与手机号。
            </span>
          </label>
        )}
        {error && <p className="text-xs text-danger">{error}</p>}
        <button
          type="submit"
          disabled={isSubmitting || (needsPhone && !consentAgreed)}
          className={`w-full rounded-xl px-4 py-2.5 text-sm font-medium text-on-action transition-colors ${
            isSubmitting || (needsPhone && !consentAgreed)
              ? "bg-action/60 cursor-not-allowed"
              : "bg-action hover:bg-action-hover active:bg-action-active"
          }`}
        >
          {isSubmitting ? "提交中..." : submitLabel || "提交"}
        </button>
      </form>
    </div>
  );
}
