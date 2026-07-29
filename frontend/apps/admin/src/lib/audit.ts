export const AUDIT_ACTION_LABELS: Record<string, string> = {
  tenant_create: "创建租户",
  organization_created: "创建组织",
  organization_updated: "更新组织",
  organization_deleted: "删除组织",
  account_created: "创建账户",
  account_updated: "更新账户",
  account_deleted: "删除账户",
  account_enabled: "启用账户",
  account_disabled: "停用账户",
  product_created: "创建产品",
  product_updated: "更新产品",
  code_activate: "激活码批次",
  code_revoke: "撤销码",
  code_freeze: "冻结码",
  code_void: "作废码",
  page_published: "发布扫码页",
  sensitive_export: "导出敏感数据",
};

export function formatAuditAction(action: string): string {
  return AUDIT_ACTION_LABELS[action] || action;
}
