export const BENEFIT_TYPE_MAP: Record<
  string,
  { label: string; color: string; description: string }
> = {
  platform_coupon: {
    label: "平台券",
    color: "#1d4ed8",
    description: "发放复购券、满减券，可关联券码池自动发码",
  },
  external_link: {
    label: "外部链接",
    color: "#16a34a",
    description: "领取后跳转小程序、商城页或活动页",
  },
  private_domain: {
    label: "私域二维码",
    color: "#f59e0b",
    description: "领取后展示群码或客服码，承接复购服务",
  },
  form_benefit: {
    label: "表单权益",
    color: "#f59e0b",
    description: "领取后引导填写留资、报名或问卷表单",
  },
  cash_red_packet: {
    label: "现金红包",
    color: "#b91c1c",
    description: "通过微信支付转账连接器发放现金红包",
  },
};

export const BENEFIT_STATUS_MAP: Record<
  string,
  { label: string; color: string }
> = {
  active: { label: "已启用", color: "#16a34a" },
  inactive: { label: "已停用", color: "#8c8c8c" },
};

export const CLAIM_STATUS_MAP: Record<
  string,
  { label: string; color: string }
> = {
  pending: { label: "待领取", color: "#8c8c8c" },
  claimed: { label: "已领取", color: "#1d4ed8" },
  success: { label: "已领取", color: "#1d4ed8" },
  used: { label: "已使用", color: "#16a34a" },
  expired: { label: "已过期", color: "#8c8c8c" },
  cancelled: { label: "已取消", color: "#b91c1c" },
};

export const DELIVERY_STATUS_MAP: Record<
  string,
  { label: string; color: string }
> = {
  not_required: { label: "无需发放", color: "#8c8c8c" },
  pending: { label: "发放中", color: "#1d4ed8" },
  delivered: { label: "已发放", color: "#16a34a" },
  failed: { label: "发放失败", color: "#b91c1c" },
};

export const AMOUNT_TYPE_OPTIONS = [
  { value: "fixed", label: "固定金额" },
  { value: "random", label: "随机金额" },
  { value: "lucky", label: "拼手气" },
];
