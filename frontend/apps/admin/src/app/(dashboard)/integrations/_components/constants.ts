import { STATUS_COLORS } from "@/lib/status-colors";

export const EVENT_OPTIONS = [
  { value: "scan.created", label: "扫码事件" },
  { value: "claim.created", label: "领券事件" },
  { value: "claim.used", label: "核销事件" },
  { value: "claim.expired", label: "过期事件" },
  { value: "consumer.created", label: "新消费者" },
  { value: "consumer.profile_updated", label: "信息变更" },
  { value: "risk.alert", label: "风险告警" },
  { value: "campaign.started", label: "活动开始" },
  { value: "campaign.ended", label: "活动结束" },
];

export const API_KEY_ROLE_OPTIONS = [
  { value: "data_reader", label: "数据只读" },
  { value: "coupon_operator", label: "优惠券操作" },
  { value: "webhook_admin", label: "消息推送管理" },
  { value: "erp_sync", label: "商品资料同步" },
  { value: "full_access", label: "全部开放接口" },
] as const;

export type ApiKeyRole = (typeof API_KEY_ROLE_OPTIONS)[number]["value"];

export const API_KEY_ROLE_LABELS: Record<ApiKeyRole, string> =
  Object.fromEntries(
    API_KEY_ROLE_OPTIONS.map(({ value, label }) => [value, label])
  ) as Record<ApiKeyRole, string>;

export interface ApiKeyPrincipal {
  role?: string | null;
  tenant_type?: string | null;
  acting_tenant_id?: string | null;
}

export function canManageApiKeys(
  principal: ApiKeyPrincipal | null | undefined
): boolean {
  return (
    principal?.role?.toLowerCase() === "admin" &&
    principal.tenant_type === "brand" &&
    !principal.acting_tenant_id
  );
}

export const INTEGRATION_STATUS: Record<string, string> = {
  delivered: STATUS_COLORS.success,
  pending: STATUS_COLORS.processing,
  retrying: STATUS_COLORS.warning,
  failed: STATUS_COLORS.error,
};
