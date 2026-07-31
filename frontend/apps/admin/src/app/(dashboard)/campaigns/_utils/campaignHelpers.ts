/**
 * 活动配置模块 — 工具函数和常量
 * 从 campaigns/page.tsx 提取的纯函数和类型定义
 */

import dayjs, { type Dayjs } from "dayjs";
import { STATUS_COLORS } from "@/lib/status-colors";

// ── 类型 ──────────────────────────────────

export type CampaignStatusType = "draft" | "active" | "paused" | "ended";
export type ComputedCampaignStatus = CampaignStatusType | "pending";

export interface CampaignRecord {
  id: string;
  name: string;
  campaign_type: string;
  status: CampaignStatusType;
  computed_status: ComputedCampaignStatus;
  product_id?: string | null;
  product_name?: string | null;
  start_at: string;
  end_at: string;
  description?: string | null;
  rules_json?: Record<string, unknown>;
  benefit_count: number;
  stock_total: number;
  stock_used: number;
  claim_count: number;
  wecom_add_count?: number;
}

export interface ProductOption {
  id: string;
  name: string;
  category?: string;
}

// ── 常量 ──────────────────────────────────

export const STATUS_MAP: Record<
  CampaignStatusType | "pending",
  { label: string; color: string }
> = {
  draft: { label: "草稿", color: STATUS_COLORS.neutral },
  pending: { label: "待开始", color: STATUS_COLORS.processing },
  active: { label: "进行中", color: STATUS_COLORS.success },
  paused: { label: "已暂停", color: STATUS_COLORS.warning },
  ended: { label: "已结束", color: STATUS_COLORS.neutral },
};

export const PARTICIPATION_LABELS: Record<string, string> = {
  first_scan: "首次扫码",
  any_scan: "任意扫码",
  member_only: "仅会员",
};

export const BENEFIT_TYPE_OPTIONS = [
  { value: "platform_coupon", label: "优惠券" },
  { value: "external_link", label: "外部链接" },
  { value: "private_domain", label: "私域引导" },
  { value: "form_benefit", label: "表单收集" },
  { value: "cash_red_packet", label: "现金红包" },
];

// ── 格式化函数 ──────────────────────────────

export function formatDateTime(value?: string | null) {
  if (!value) return "未设置";
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format("YYYY-MM-DD HH:mm") : value;
}

export function formatCampaignTime(record: CampaignRecord) {
  const start = formatDateTime(record.start_at);
  const end = formatDateTime(record.end_at);
  const status = getDisplayStatus(record);
  if (status === "active") {
    const endAt = dayjs(record.end_at);
    if (endAt.isValid()) {
      const days = Math.max(endAt.endOf("day").diff(dayjs(), "day"), 0);
      return { range: `${start} 至 ${end}`, hint: `剩余 ${days} 天` };
    }
  }
  if (status === "pending")
    return { range: `${start} 至 ${end}`, hint: "未开始" };
  if (status === "ended")
    return { range: `${start} 至 ${end}`, hint: "已结束" };
  return { range: `${start} 至 ${end}`, hint: "" };
}

// ── 访问器函数 ──────────────────────────────

export function getDisplayStatus(
  record: CampaignRecord
): ComputedCampaignStatus {
  return record.computed_status || record.status || "draft";
}

export function getBenefitCount(record: CampaignRecord) {
  return record.benefit_count ?? 0;
}

export function getStockTotal(record: CampaignRecord) {
  return record.stock_total ?? 0;
}

export function getStockUsed(record: CampaignRecord) {
  return record.stock_used ?? 0;
}

export function getClaimCount(record: CampaignRecord) {
  return record.claim_count ?? 0;
}

// ── 转换函数 ──────────────────────────────

export function toDateRange(
  startAt?: string,
  endAt?: string
): [Dayjs, Dayjs] | undefined {
  const start = dayjs(startAt);
  const end = dayjs(endAt);
  if (!start.isValid() || !end.isValid()) return undefined;
  return [start, end];
}

export function rulesFromCampaign(record?: CampaignRecord | null) {
  return (record?.rules_json || {}) as Record<string, string>;
}

export function participationTypeFromText(value?: string) {
  if (value?.includes("首次")) return "first_scan";
  if (value?.includes("会员")) return "member_only";
  return "any_scan";
}

export function claimLimitCountFromText(value?: string) {
  const matched = value?.match(/\d+/);
  return matched ? Number(matched[0]) : 1;
}

export function formatParticipationCondition(value?: string) {
  return PARTICIPATION_LABELS[value || "any_scan"];
}

export function formatClaimLimit(
  campaignGoal?: string,
  claimLimitCount?: number
) {
  const count = claimLimitCount || 1;
  const action =
    campaignGoal === "lottery" || campaignGoal === "points" ? "参与" : "领取";
  return `每人限${action}${count}次`;
}

export function getProductLabel(products: ProductOption[], productId?: string) {
  const product = products.find((item) => item.id === productId);
  if (!product) return "";
  return product.category
    ? `${product.name} · ${product.category}`
    : product.name;
}

export function getProductName(products: ProductOption[], productId?: string) {
  return products.find((item) => item.id === productId)?.name || "";
}
