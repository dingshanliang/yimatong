export const BENEFIT_TYPE_MAP: Record<string, { label: string; color: string; description: string }> = {
  platform_coupon: { label: "平台券", color: "blue", description: "发放复购券、满减券，可关联券码池自动发码" },
  external_link: { label: "外部链接", color: "green", description: "领取后跳转小程序、商城页或活动页" },
  private_domain: { label: "私域二维码", color: "orange", description: "领取后展示群码或客服码，承接复购服务" },
  form_benefit: { label: "表单权益", color: "purple", description: "领取后引导填写留资、报名或问卷表单" },
  cash_red_packet: { label: "现金红包", color: "red", description: "通过微信支付转账连接器发放现金红包" },
};

export const BENEFIT_STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "已启用", color: "green" },
  inactive: { label: "已停用", color: "default" },
};

export const CLAIM_STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待领取", color: "default" },
  claimed: { label: "已领取", color: "blue" },
  success: { label: "已领取", color: "blue" },
  used: { label: "已使用", color: "green" },
  expired: { label: "已过期", color: "gray" },
  cancelled: { label: "已取消", color: "red" },
};

export const DELIVERY_STATUS_MAP: Record<string, { label: string; color: string }> = {
  not_required: { label: "无需发放", color: "default" },
  pending: { label: "发放中", color: "blue" },
  delivered: { label: "已发放", color: "green" },
  failed: { label: "发放失败", color: "red" },
};

export const AMOUNT_TYPE_OPTIONS = [
  { value: "fixed", label: "固定金额" },
  { value: "random", label: "随机金额" },
  { value: "lucky", label: "拼手气" },
];
