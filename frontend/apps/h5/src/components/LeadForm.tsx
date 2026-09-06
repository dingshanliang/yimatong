"use client";

import { Check } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { apiClient } from "@/lib/api";
import { loadConsentReceiptStatus } from "@/lib/consentReceiptStatus";
import { saveScanToken } from "@/lib/scan-token-store";

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
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function leadConsentStorageKey(publicId: string) {
  return `lead_consent_id:${publicId}`;
}

function loadLeadConsentId(publicId: string) {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(leadConsentStorageKey(publicId));
  } catch {
    return null;
  }
}

function saveLeadConsentId(publicId: string, consentId: string | null) {
  if (typeof window === "undefined") return;
  try {
    if (consentId) {
      localStorage.setItem(leadConsentStorageKey(publicId), consentId);
    } else {
      localStorage.removeItem(leadConsentStorageKey(publicId));
    }
  } catch {
    // Storage is a locator only; the durable consent receipt remains authoritative.
  }
}

function validCapturedIdentity(data: unknown): {
  consumerId: string;
  scanToken: string;
} | null {
  if (!data || typeof data !== "object") return null;
  const {
    status,
    consumer_id: consumerId,
    scan_token: scanToken,
  } = data as Record<string, unknown>;
  if (
    status !== "captured" ||
    typeof consumerId !== "string" ||
    !UUID_PATTERN.test(consumerId) ||
    typeof scanToken !== "string" ||
    scanToken.length > 4096
  ) {
    return null;
  }
  const parts = scanToken.split(".");
  if (
    parts.length !== 3 ||
    parts.some((part) => !/^[A-Za-z0-9_-]+$/.test(part))
  )
    return null;
  try {
    const encodedPayload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(
      atob(encodedPayload.padEnd(Math.ceil(encodedPayload.length / 4) * 4, "="))
    ) as Record<string, unknown>;
    if (
      payload.type !== "scan_token" ||
      payload.consumer_id !== consumerId ||
      typeof payload.exp !== "number" ||
      payload.exp <= Date.now() / 1000
    ) {
      return null;
    }
  } catch {
    return null;
  }
  return { consumerId, scanToken };
}

export function LeadForm({
  publicId,
  scanToken,
  title,
  subtitle,
  submitLabel,
  fields: configuredFields,
}: LeadFormProps) {
  const [submitted, setSubmitted] = useState(false);
  const [consentId, setConsentId] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");
  // yimatong-zgb1.5 AC3：采集手机号（PII）前必须勾选隐私授权
  const [consentAgreed, setConsentAgreed] = useState(false);
  const [policy, setPolicy] = useState<{
    purpose: string;
    policy_version: string;
    policy_digest: string;
  } | null>(null);
  const grantIdempotency = useRef(crypto.randomUUID());
  const leadIdempotency = useRef(crypto.randomUUID());
  const withdrawIdempotency = useRef(crypto.randomUUID());
  const requestGeneration = useRef(0);
  const authorityToken = useRef(scanToken);
  const activeFields = configuredFields?.length
    ? Array.from(
        new Set([
          ...configuredFields.filter((field) => field in FIELD_CONFIG),
          "phone",
        ])
      )
    : DEFAULT_FIELDS;
  const needsPhone = activeFields.includes("phone");

  useEffect(() => {
    const generation = ++requestGeneration.current;
    setPolicy(null);
    setSubmitted(false);
    setConsentId(null);
    setIsSubmitting(false);
    setConsentAgreed(false);
    setError("");
    grantIdempotency.current = crypto.randomUUID();
    leadIdempotency.current = crypto.randomUUID();
    withdrawIdempotency.current = crypto.randomUUID();
    authorityToken.current = scanToken;
    if (!needsPhone || !scanToken || publicId === "preview") return;
    let active = true;
    const load = async () => {
      try {
        const policyResponse = await apiClient.get("/public/consents/policy", {
          params: { purpose: "lead_capture" },
          headers: { Authorization: `Bearer ${scanToken}` },
        });
        if (!active || generation !== requestGeneration.current) return;
        const currentPolicy = policyResponse.data as {
          purpose?: string;
          policy_version?: string;
          policy_digest?: string;
        };
        setPolicy(currentPolicy as typeof policy);

        const storedConsentId = loadLeadConsentId(publicId);
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
          saveLeadConsentId(publicId, null);
          return;
        }
        if (receiptResult.kind === "preserve") return;
        const receipt = receiptResult.data as Record<string, unknown>;
        if (
          receipt.consent_id === storedConsentId &&
          receipt.status === "granted" &&
          receipt.purpose === "lead_capture" &&
          receipt.policy_version === currentPolicy.policy_version &&
          receipt.policy_digest === currentPolicy.policy_digest
        ) {
          setConsentId(storedConsentId);
          setSubmitted(true);
        } else {
          saveLeadConsentId(publicId, null);
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
  }, [needsPhone, publicId, scanToken]);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (isSubmitting) return;
    const generation = requestGeneration.current;
    // PII 字段（手机号）采集前必须勾选 consent
    if (needsPhone && !consentAgreed) {
      setError("请先阅读并同意隐私政策");
      return;
    }
    setIsSubmitting(true);
    setError("");
    const token = authorityToken.current;
    const fd = new FormData(e.currentTarget);
    const body: Record<string, unknown> = { public_id: publicId };
    for (const f of activeFields) {
      body[f] = fd.get(f) || "";
    }

    try {
      // yimatong-zgb1.5：采集 PII 前先 grant privacy consent（场景 lead_capture + 版本）
      let grantedConsentId: string | null = null;
      if (needsPhone && consentAgreed) {
        if (!policy) throw new Error("policy unavailable");
        const receipt = await apiClient.post(
          "/public/consents",
          {
            purpose: policy.purpose,
            policy_version: policy.policy_version,
            policy_digest: policy.policy_digest,
            idempotency_key: grantIdempotency.current,
          },
          { headers: token ? { Authorization: `Bearer ${token}` } : {} }
        );
        if (generation !== requestGeneration.current) return;
        if (!receipt.data?.consent_id || receipt.data?.status !== "granted") {
          throw new Error("consent receipt unavailable");
        }
        grantedConsentId = receipt.data.consent_id;
        body.consent_id = grantedConsentId;
      }
      body.idempotency_key = leadIdempotency.current;
      delete body.public_id;
      const res = await apiClient.post("/consumers/lead-capture", body, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (generation !== requestGeneration.current) return;
      const capturedIdentity = validCapturedIdentity(res.data);
      if (!capturedIdentity) throw new Error("lead receipt unavailable");
      if (typeof window !== "undefined") {
        localStorage.setItem("consumer_id", capturedIdentity.consumerId);
        // 凭证按码隔离存储（键含 publicId），不再写全局 scan_token。
        saveScanToken(publicId, capturedIdentity.scanToken);
      }
      authorityToken.current = capturedIdentity.scanToken;
      if (grantedConsentId) {
        saveLeadConsentId(publicId, grantedConsentId);
        setConsentId(grantedConsentId);
      }
      setSubmitted(true);
      grantIdempotency.current = crypto.randomUUID();
      leadIdempotency.current = crypto.randomUUID();
    } catch (err: unknown) {
      if (generation !== requestGeneration.current) return;
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
      if (generation === requestGeneration.current) setIsSubmitting(false);
    }
  };

  const handleWithdraw = async () => {
    const token = authorityToken.current;
    if (!consentId || !token || isSubmitting) return;
    const generation = requestGeneration.current;
    setIsSubmitting(true);
    setError("");
    try {
      const response = await apiClient.post(
        `/public/consents/${consentId}/withdraw`,
        { idempotency_key: withdrawIdempotency.current },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (generation !== requestGeneration.current) return;
      if (
        response.data?.consent_id !== consentId ||
        response.data?.status !== "withdrawn"
      ) {
        throw new Error("invalid withdraw receipt");
      }
      saveLeadConsentId(publicId, null);
      setConsentId(null);
      setSubmitted(false);
      setConsentAgreed(false);
      withdrawIdempotency.current = crypto.randomUUID();
    } catch {
      if (generation === requestGeneration.current) {
        setError("撤回未完成，请重试");
      }
    } finally {
      if (generation === requestGeneration.current) setIsSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <div className="mt-3 rounded-2xl bg-surface p-4 shadow-sm text-center">
        <div className="mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-success-bg">
          <Check className="h-5 w-5 text-success" aria-hidden="true" />
        </div>
        <p className="text-sm font-medium text-foreground">已提交联系方式</p>
        <button
          type="button"
          disabled={isSubmitting}
          onClick={handleWithdraw}
          className="mt-2 text-xs text-foreground-tertiary underline-offset-2 hover:underline"
        >
          {isSubmitting ? "撤回中..." : "撤回联系授权"}
        </button>
        {error && <p className="mt-2 text-xs text-danger">{error}</p>}
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
          const cfg = FIELD_CONFIG[f];
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
              我已阅读并同意《隐私政策》（版本{" "}
              {policy?.policy_version || "加载中"}
              ），授权品牌在留资场景下采集我的姓名与手机号。
            </span>
          </label>
        )}
        {error && <p className="text-xs text-danger">{error}</p>}
        <button
          type="submit"
          disabled={isSubmitting || (needsPhone && (!consentAgreed || !policy))}
          className={`w-full rounded-xl px-4 py-2.5 text-sm font-medium text-on-action transition-colors ${
            isSubmitting || (needsPhone && (!consentAgreed || !policy))
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
