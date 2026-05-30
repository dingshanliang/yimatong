export const RULE_TYPES = [
  { value: "frequency", label: "频率限制" },
  { value: "ip_diversity", label: "IP 多样性" },
  { value: "geo_anomaly", label: "地域异常" },
  { value: "budget", label: "预算控制" },
  { value: "time_window", label: "时间窗口" },
];

export const ACTIONS = [
  { value: "block", label: "拦截" },
  { value: "warn", label: "预警" },
  { value: "flag", label: "标记" },
];
