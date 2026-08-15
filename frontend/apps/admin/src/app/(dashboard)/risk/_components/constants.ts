export const RULE_TYPES = [
  { value: "ip_frequency", label: "IP 频率限制" },
  { value: "phone_frequency", label: "手机号频率限制" },
  { value: "device_frequency", label: "设备频率限制" },
  { value: "scan_frequency", label: "扫码频率限制" },
  { value: "cross_region", label: "跨区风险" },
];

export const ACTIONS = [
  { value: "block", label: "拦截" },
  { value: "warn", label: "预警" },
];
