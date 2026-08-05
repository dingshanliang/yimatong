import { describe, expect, it } from "vitest";

import {
  buildQuotaDisplay,
  quotaDisplayLabels,
  type QuotaUsageItem,
} from "../quota-display";

function quotaItem(
  quota: Record<string, number> | null,
  usage: Record<string, number>
): QuotaUsageItem {
  return {
    tenant_id: "tenant-1",
    tenant_name: "示例品牌",
    plan: "growth",
    quota,
    status: "active",
    usage,
  };
}

describe("quota usage presentation", () => {
  it("maps backend usage and limit payloads to business-readable UI", () => {
    const item = quotaItem({ max_codes: 100 }, { codes: 80 });
    const display = buildQuotaDisplay(item, "max_codes");
    const labels = quotaDisplayLabels(display);

    expect(labels).toEqual({
      used: "已用 80",
      limit: "限额 100",
      remaining: "剩余 20",
      percent: "占比 80.0%",
    });
    expect(display.state).toBe("接近限额");
  });

  it("maps authoritative scan-event usage to the max_scans quota", () => {
    const display = buildQuotaDisplay(
      quotaItem({ max_scans: 1000 }, { scans: 820 }),
      "max_scans"
    );

    expect(display.used).toBe(820);
    expect(display.state).toBe("接近限额");
  });

  it("shows exceeded usage without reporting a negative remainder", () => {
    expect(
      buildQuotaDisplay(
        quotaItem({ max_campaigns: 10 }, { campaigns: 12 }),
        "max_campaigns"
      )
    ).toEqual({
      used: 12,
      limitLabel: "10",
      remainingLabel: "0",
      percentLabel: "120.0%",
      progressPercent: 100,
      state: "已超限",
    });
  });

  it("distinguishes unlimited, missing, and zero limits", () => {
    expect(
      buildQuotaDisplay(
        quotaItem({ max_accounts: -1 }, { accounts: 23 }),
        "max_accounts"
      )
    ).toMatchObject({
      used: 23,
      limitLabel: "无限制",
      remainingLabel: "无限制",
      percentLabel: "不适用",
      state: "无限制",
    });
    expect(
      buildQuotaDisplay(quotaItem(null, { accounts: 2 }), "max_accounts")
    ).toMatchObject({
      used: 2,
      limitLabel: "未配置",
      remainingLabel: "—",
      percentLabel: "—",
      state: "无法判断",
    });
    expect(
      buildQuotaDisplay(
        quotaItem({ max_accounts: 0 }, { accounts: 0 }),
        "max_accounts"
      )
    ).toMatchObject({
      used: 0,
      limitLabel: "0",
      remainingLabel: "0",
      percentLabel: "不适用（限额为 0）",
      state: "已用尽",
    });
    expect(
      buildQuotaDisplay(
        quotaItem({ max_accounts: 0 }, { accounts: 1 }),
        "max_accounts"
      )
    ).toMatchObject({ state: "已超限" });
  });
});
