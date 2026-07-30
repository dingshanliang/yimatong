/**
 * 业务状态 → design token 语义色集中映射。
 *
 * 颜色值取自 @yimatong/design-tokens 的 light 语义层（单一事实来源），
 * 用于 antd <Tag color> 直接渲染 hex。深色模式下 Tag 不自动切换色板
 * （已知遗留，后续可接 ConfigProvider 双色映射）。
 *
 * 语义口径（与 docs/02_tech/design-system/tokens.md 对齐）：
 *   success  成功 / 活跃 / 已完成 / 已发放 / 已使用 / 健康
 *   info     进行中 / 待处理 / 信息 / 平台券 / 入门
 *   warning  暂停 / 警告 / 预警 / 中危 / 待激活 / 私域 / 琥珀类
 *   danger   失败 / 危险 / 已终止 / 已撤销 / 红包 / 高危
 *   neutral  默认 / 草稿 / 未激活 / 占位（中性灰）
 */
export const STATUS_COLORS = {
  success: "#16a34a",
  info: "#1d4ed8",
  warning: "#f59e0b",
  danger: "#b91c1c",
  neutral: "#8c8c8c",
} as const;

export type StatusColor = keyof typeof STATUS_COLORS;
