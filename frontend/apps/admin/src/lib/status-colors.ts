/**
 * 业务状态 → antd Tag preset 名集中映射（design tokens 单一事实来源）。
 *
 * 值用 antd `<Tag color>` 的 **preset 状态名**（success/processing/warning/
 * error/default），而非 hex。理由：preset 名由 antd 经 ConfigProvider 的
 * `colorSuccess/Warning/Error/Info` 种子 token 计算（见
 * `@yimatong/design-tokens/lib/antd.ts` getAntdTheme），而该种子已绑定本仓库
 * design tokens 的 feedback 语义层。因此：
 *   1. 颜色始终来自 design tokens 单一事实来源（不散落 hex）；
 *   2. 深色模式自动自适应（darkAlgorithm + dark feedback tokens），无需双色维护。
 *
 * 语义口径（与 docs/02_tech/design-system/tokens.md 对齐）：
 *   success    成功 / 活跃 / 已完成 / 已发放 / 已使用 / 健康
 *   processing 进行中 / 待处理 / 信息 / 平台券 / 入门 / 蓝
 *   warning    暂停 / 警告 / 预警 / 中危 / 待激活 / 私域 / 琥珀类
 *   error      失败 / 危险 / 已终止 / 已撤销 / 红包 / 高危 / 红
 *   default    默认 / 草稿 / 未激活 / 占位（中性灰）
 *
 * 仅用于 antd `<Tag color={...}>`。不要把 preset 名喂给 inline style 的
 * color/background、Progress strokeColor 或 Badge status——那些需要真实 CSS 颜色值。
 */

/** antd `<Tag color>` 接受的 preset 状态名（design tokens 语义色）。 */
export const STATUS_COLORS = {
  success: "success",
  processing: "processing",
  warning: "warning",
  error: "error",
  neutral: "default",
} as const;

export type StatusColor = keyof typeof STATUS_COLORS;

/**
 * 需要真实 CSS 颜色值的组件使用的 design-token 映射。
 * 不要把这里的 CSS var 改成 antd preset，也不要把 STATUS_COLORS 用到这些位置。
 */
export const STATUS_TOKEN_COLORS = {
  success: "var(--ymt-color-feedback-success)",
  processing: "var(--ymt-color-feedback-info)",
  warning: "var(--ymt-color-feedback-warning)",
  error: "var(--ymt-color-feedback-danger)",
  neutral: "var(--ymt-color-text-tertiary)",
} as const;

export type StatusTokenColor = keyof typeof STATUS_TOKEN_COLORS;

/**
 * 旧 hex → preset 速查，便于把硬编码 hex 的本地 STATUS_MAP 平移到集中映射。
 * 仅覆盖历史代码出现过的 5 个 hex。
 */
export const HEX_TO_STATUS: Record<string, StatusColor> = {
  "#16a34a": "success",
  "#1d4ed8": "processing",
  "#f59e0b": "warning",
  "#b91c1c": "error",
  "#8c8c8c": "neutral",
};
