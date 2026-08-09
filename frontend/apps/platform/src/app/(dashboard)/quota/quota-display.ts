export interface QuotaUsageItem {
  tenant_id: string;
  tenant_name: string;
  plan: string;
  quota: Record<string, number> | null;
  status: string;
  plan_expires_at?: string | null;
  read_only?: boolean;
  quota_enforcement_state: "ready" | "reconciliation_pending";
  enforcement_ready: boolean;
  reconciled_at: string | null;
  source_revision: string | null;
  usage: Record<string, number>;
}

export type QuotaKey =
  "max_codes" | "max_scans" | "max_campaigns" | "max_products" | "max_accounts";

const QUOTA_USAGE_KEYS: Record<QuotaKey, string> = {
  max_codes: "codes",
  max_scans: "scans",
  max_campaigns: "campaigns",
  max_products: "products",
  max_accounts: "accounts",
};

export interface PlanStatusDisplay {
  state: "active" | "expired" | "perpetual" | "unknown";
  label: string;
}

export interface QuotaEnforcementDisplay {
  ready: boolean;
  state: "ready" | "reconciliation_pending";
  label: "额度已就绪" | "额度校准中";
}

export function buildQuotaEnforcementDisplay(
  record: Pick<QuotaUsageItem, "quota_enforcement_state" | "enforcement_ready">
): QuotaEnforcementDisplay {
  const ready =
    record.quota_enforcement_state === "ready" &&
    record.enforcement_ready === true;
  return ready
    ? { ready: true, state: "ready", label: "额度已就绪" }
    : {
        ready: false,
        state: "reconciliation_pending",
        label: "额度校准中",
      };
}

export function buildPlanStatusDisplay(
  record: Pick<QuotaUsageItem, "plan_expires_at" | "read_only">,
  now = Date.now()
): PlanStatusDisplay {
  const expiresAt = record.plan_expires_at
    ? Date.parse(record.plan_expires_at)
    : Number.NaN;
  if (
    record.read_only === true ||
    (Number.isFinite(expiresAt) && expiresAt <= now)
  ) {
    return { state: "expired", label: "已到期（只读）" };
  }

  if (Number.isFinite(expiresAt)) {
    const date = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(expiresAt);
    return { state: "active", label: `有效至 ${date}` };
  }

  if (record.read_only === false && record.plan_expires_at === null) {
    return { state: "perpetual", label: "长期有效" };
  }
  return { state: "unknown", label: "状态未知" };
}

export interface QuotaDisplay {
  used: number;
  limitLabel: string;
  remainingLabel: string;
  percentLabel: string;
  progressPercent: number | null;
  state:
    | "正常"
    | "接近限额"
    | "已用尽"
    | "已超限"
    | "无限制"
    | "无法判断"
    | "待校准";
}

export function buildQuotaDisplay(
  record: QuotaUsageItem,
  key: QuotaKey
): QuotaDisplay {
  const rawUsage = record.usage?.[QUOTA_USAGE_KEYS[key]];
  const used = Number.isFinite(rawUsage) ? Math.max(0, rawUsage) : 0;
  const limit = record.quota?.[key];

  if (!buildQuotaEnforcementDisplay(record).ready) {
    return {
      used,
      limitLabel: "待校准",
      remainingLabel: "—",
      percentLabel: "—",
      progressPercent: null,
      state: "待校准",
    };
  }

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
    used: `${display.state === "待校准" ? "已记录" : "已用"} ${display.used.toLocaleString()}`,
    limit: `限额 ${display.limitLabel}`,
    remaining: `剩余 ${display.remainingLabel}`,
    percent: `占比 ${display.percentLabel}`,
  };
}
