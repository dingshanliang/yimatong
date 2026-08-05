export interface QuotaUsageItem {
  tenant_id: string;
  tenant_name: string;
  plan: string;
  quota: Record<string, number> | null;
  status: string;
  usage: Record<string, number>;
}

export type QuotaKey =
  "max_codes" | "max_scans" | "max_campaigns" | "max_accounts";

const QUOTA_USAGE_KEYS: Record<QuotaKey, string> = {
  max_codes: "codes",
  max_scans: "scans",
  max_campaigns: "campaigns",
  max_accounts: "accounts",
};

export interface QuotaDisplay {
  used: number;
  limitLabel: string;
  remainingLabel: string;
  percentLabel: string;
  progressPercent: number | null;
  state: "正常" | "接近限额" | "已用尽" | "已超限" | "无限制" | "无法判断";
}

export function buildQuotaDisplay(
  record: QuotaUsageItem,
  key: QuotaKey
): QuotaDisplay {
  const rawUsage = record.usage?.[QUOTA_USAGE_KEYS[key]];
  const used = Number.isFinite(rawUsage) ? Math.max(0, rawUsage) : 0;
  const limit = record.quota?.[key];

  if (limit === -1) {
    return {
      used,
      limitLabel: "无限制",
      remainingLabel: "无限制",
      percentLabel: "不适用",
      progressPercent: null,
      state: "无限制",
    };
  }

  if (
    limit === undefined ||
    limit === null ||
    !Number.isFinite(limit) ||
    limit < 0
  ) {
    return {
      used,
      limitLabel: "未配置",
      remainingLabel: "—",
      percentLabel: "—",
      progressPercent: null,
      state: "无法判断",
    };
  }

  const remaining = Math.max(limit - used, 0);
  if (limit === 0) {
    return {
      used,
      limitLabel: "0",
      remainingLabel: "0",
      percentLabel: "不适用（限额为 0）",
      progressPercent: null,
      state: used > 0 ? "已超限" : "已用尽",
    };
  }

  const percent = (used / limit) * 100;
  const state =
    used > limit
      ? "已超限"
      : used === limit
        ? "已用尽"
        : percent >= 80
          ? "接近限额"
          : "正常";
  return {
    used,
    limitLabel: limit.toLocaleString(),
    remainingLabel: remaining.toLocaleString(),
    percentLabel: `${percent.toFixed(1)}%`,
    progressPercent: Math.min(Math.round(percent), 100),
    state,
  };
}

export function quotaDisplayLabels(display: QuotaDisplay) {
  return {
    used: `已用 ${display.used.toLocaleString()}`,
    limit: `限额 ${display.limitLabel}`,
    remaining: `剩余 ${display.remainingLabel}`,
    percent: `占比 ${display.percentLabel}`,
  };
}
