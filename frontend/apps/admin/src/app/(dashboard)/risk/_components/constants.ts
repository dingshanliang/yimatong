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

/** 各规则类型的配置模板：选择类型后自动填充，可在其上修改。 */
export const RULE_CONFIG_TEMPLATES: Record<string, Record<string, unknown>> = {
  ip_frequency: { window_minutes: 60, max_requests: 5 },
  phone_frequency: { window_minutes: 60, max_requests: 3 },
  device_frequency: { window_minutes: 1440, max_requests: 10 },
  scan_frequency: { window_minutes: 60, max_requests: 20 },
  cross_region: {},
};
