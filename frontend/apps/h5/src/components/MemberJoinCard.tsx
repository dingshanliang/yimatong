"use client";

import { useEffect, useRef, useState } from "react";
import { apiClient } from "@/lib/api";

interface CurrentPolicy {
  purpose: string;
  policy_version: string;
  policy_digest: string;
  policy_title: string;
  policy_content: string;
}

interface MemberJoinCardProps {
  scanToken?: string;
  onScanTokenChange?: (token: string) => void;
  onMembershipReady?: () => void;
}

export function MemberJoinCard({
  scanToken,
  onScanTokenChange,
  onMembershipReady,
}: MemberJoinCardProps) {
  const [policy, setPolicy] = useState<CurrentPolicy | null>(null);
  const [agreed, setAgreed] = useState(false);
  const [membershipNumber, setMembershipNumber] = useState<string | null>(null);
  const [recoveryToken, setRecoveryToken] = useState<string | null>(null);
  const [recovered, setRecovered] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [policyLoadFailed, setPolicyLoadFailed] = useState(false);
  const [policyReloadKey, setPolicyReloadKey] = useState(0);
  const [loadingPolicy, setLoadingPolicy] = useState(false);
  const joinIdempotency = useRef(crypto.randomUUID());
  const consentIdempotency = useRef(crypto.randomUUID());
  const recoveryIdempotency = useRef(crypto.randomUUID());

  useEffect(() => {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    const token = fragment.get("member_recovery_token");
    if (!token) return;
    setRecoveryToken(token);
    window.history.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}${window.location.search}`
    );
  }, []);

  useEffect(() => {
    if (!scanToken) return;
    let active = true;
    setLoadingPolicy(true);
    apiClient
      .get("/public/consents/policy", {
        params: { purpose: "brand_membership" },
        headers: { Authorization: `Bearer ${scanToken}` },
      })
      .then(({ data }) => {
        if (active) {
          setPolicy(data as CurrentPolicy);
          setPolicyLoadFailed(false);
        }
      })
      .catch(() => {
        // 政策拉取失败不能让入会入口静默消失（转化静默流失），给出可重试的降级态
        if (active) {
          setPolicy(null);
          setPolicyLoadFailed(true);
        }
      })
      .finally(() => {
        if (active) setLoadingPolicy(false);
      });
    return () => {
      active = false;
    };
  }, [scanToken, policyReloadKey]);

  if (!scanToken) return null;

  const recover = async () => {
    if (!recoveryToken || loading) return;
    setLoading(true);
    setError("");
    try {
      const membership = await apiClient.post(
        "/consumers/membership/recover",
        {
          recovery_token: recoveryToken,
          idempotency_key: recoveryIdempotency.current,
        },
        { headers: { Authorization: `Bearer ${scanToken}` } }
      );
      const nextNumber = membership.data?.membership_number;
      if (typeof nextNumber !== "string")
        throw new Error("invalid recovery receipt");
      setMembershipNumber(nextNumber);
      setRecovered(true);
      setRecoveryToken(null);
      onMembershipReady?.();
    } catch {
      setError("会员身份恢复失败，请重新从已验证的会员入口进入");
    } finally {
      setLoading(false);
    }
  };

  if (recoveryToken) {
    return (
      <section className="mx-4 mt-3 rounded-xl border border-base bg-surface p-4">
        <h2 className="font-semibold text-foreground">恢复品牌会员身份</h2>
        <p className="mt-1 text-sm text-foreground-secondary">
          使用已验证的会员凭据，把本入口恢复到同一品牌会员名下。
        </p>
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
        <button
          type="button"
          disabled={loading}
          onClick={() => void recover()}
          className="mt-3 w-full rounded-xl bg-action py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-action/50"
        >
          {loading ? "正在验证…" : "验证并恢复会员"}
        </button>
      </section>
    );
  }

  if (membershipNumber) {
    return (
      <section
        className="mx-4 mt-3 rounded-xl border border-success bg-success-bg p-4"
        aria-live="polite"
      >
        <p className="font-semibold text-success">
          {recovered ? "已恢复品牌会员" : "已加入品牌会员"}
        </p>
        <p className="mt-1 text-sm text-success">
          会员编号：{membershipNumber}
        </p>
      </section>
    );
  }

  if (!policy && policyLoadFailed) {
    return (
      <section className="mx-4 mt-3 rounded-xl border border-base bg-surface p-4">
        <h2 className="font-semibold text-foreground">加入品牌会员</h2>
        <p className="mt-1 text-sm text-foreground-secondary">
          会员权益暂时无法加载，请稍后重试。
        </p>
        <button
          type="button"
          onClick={() => setPolicyReloadKey((key) => key + 1)}
          className="mt-3 w-full rounded-xl border border-base py-2.5 text-sm font-medium text-foreground-secondary"
        >
          重新加载
        </button>
      </section>
    );
  }

  if (!policy) return null;

  const join = async () => {
    if (!agreed || loading) return;
    setLoading(true);
    setError("");
    try {
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
      if (!consent.data?.consent_id || consent.data?.status !== "granted") {
        throw new Error("invalid consent receipt");
      }
      const membership = await apiClient.post(
        "/consumers/membership/join",
        {
          consent_id: consent.data.consent_id,
          idempotency_key: joinIdempotency.current,
        },
        { headers: { Authorization: `Bearer ${scanToken}` } }
      );
      const nextToken = membership.data?.scan_token;
      const nextNumber = membership.data?.membership_number;
      if (typeof nextToken !== "string" || typeof nextNumber !== "string") {
        throw new Error("invalid membership receipt");
      }
      localStorage.setItem("scan_token", nextToken);
      setMembershipNumber(nextNumber);
      onScanTokenChange?.(nextToken);
      onMembershipReady?.();
    } catch (err) {
      // scan_token 绑定 IP 且 30 分钟过期：401 时重试永远失败，需重新扫码
      const status = (err as { response?: { status?: number } } | undefined)
        ?.response?.status;
      setError(
        status === 401
          ? "会员凭证已失效（如切换了网络或停留过久），请重新扫码后再加入"
          : "入会凭据未能保存，请稍后重试"
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="mx-4 mt-3 rounded-xl border border-base bg-surface p-4">
      <h2 className="font-semibold text-foreground">加入品牌会员</h2>
      <p className="mt-1 text-sm text-foreground-secondary">
        入会后可在后续入口恢复本品牌会员身份。匿名查看产品与溯源不受影响。
      </p>
      <details className="mt-3 text-sm text-foreground-secondary">
        <summary className="cursor-pointer font-medium text-foreground">
          {policy.policy_title}
        </summary>
        <p className="mt-2 whitespace-pre-wrap">{policy.policy_content}</p>
      </details>
      <label className="mt-3 flex items-start gap-2 text-sm text-foreground-secondary">
        <input
          type="checkbox"
          checked={agreed}
          onChange={(event) => setAgreed(event.target.checked)}
          className="mt-0.5"
        />
        <span>我已阅读并同意建立本品牌会员关系</span>
      </label>
      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
      <button
        type="button"
        disabled={!agreed || loading}
        onClick={() => void join()}
        className="mt-3 w-full rounded-xl bg-action py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-action/50"
      >
        {loading ? "正在保存…" : "确认加入会员"}
      </button>
    </section>
  );
}
