export const QUOTA_FIELDS = [
  { key: "max_codes", label: "最大码量" },
  { key: "max_scans", label: "最大扫码量" },
  { key: "max_campaigns", label: "最大活动数" },
  { key: "max_products", label: "最大产品数" },
  { key: "max_accounts", label: "最大账号数" },
  { key: "max_codes_per_batch", label: "单批最大码量" },
] as const;

export const FEATURE_FIELDS = [
  { key: "ai_assistant", label: "AI 助手" },
  { key: "risk_module", label: "风控模块" },
  { key: "channel_portal", label: "渠道门户" },
  { key: "white_label", label: "白标" },
  { key: "cash_red_packet", label: "现金红包" },
] as const;

export function isValidQuotaValue(value: unknown): boolean {
  return (
    value === undefined ||
    value === null ||
    (typeof value === "number" &&
      Number.isInteger(value) &&
      (value === -1 || value >= 0))
  );
}

export function quotaLabel(key: string): string {
  return QUOTA_FIELDS.find((field) => field.key === key)?.label ?? key;
}

export function buildPlanConfiguration(values: Record<string, unknown>) {
  const quota_defaults: Record<string, number> = {};
  QUOTA_FIELDS.forEach(({ key }) => {
    const value = values[key];
    if (value !== undefined && value !== null) {
      quota_defaults[key] = Number(value);
    }
  });

  const feature_flags: Record<string, boolean> = {};
  FEATURE_FIELDS.forEach(({ key }) => {
    feature_flags[key] = values[key] === true;
  });

  return { quota_defaults, feature_flags };
}
