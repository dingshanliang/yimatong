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
  translation_create: "新增多语言文案",
  translation_delete: "删除多语言文案",
  translation_batch_update: "批量更新多语言文案",
  private_domain_config_create: "新建私域承接配置",
  private_domain_config_update: "更新私域承接配置",
  password_changed: "修改密码",
  tenant_onboarding_step_completed: "完成初始化向导步骤",
};

export function formatAuditAction(action: string): string {
  return AUDIT_ACTION_LABELS[action] || action;
}
