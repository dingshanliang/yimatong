"use client";

import { Tooltip } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";

/**
 * 健康评分表头组件，带 Tooltip 解释计算原理。
 *
 * 渠道健康评分公式：100 − (异常率×40 + 跨区率×30 + 重复率×30)
 */
export function ChannelHealthScoreHeader() {
  return (
    <span className="inline-flex items-center gap-4">
      健康评分
      <Tooltip
        title={
          <div style={{ lineHeight: 1.8 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>评分公式</div>
            <div>100 − (异常率×40 + 跨区率×30 + 重复率×30)</div>
            <div style={{ fontWeight: 600, marginTop: 8, marginBottom: 4 }}>指标说明</div>
            <div>• 异常率（权重 40%）：非独立 IP 扫码占比</div>
            <div>• 跨区率（权重 30%）：跨区域窜货扫码占比</div>
            <div>• 重复率（权重 30%）：重复扫码占比</div>
            <div style={{ fontWeight: 600, marginTop: 8, marginBottom: 4 }}>评级标准</div>
            <div>≥ 80 健康 &nbsp;|&nbsp; 60–79 预警 &nbsp;|&nbsp; &lt; 60 危险</div>
          </div>
        }
      >
        <InfoCircleOutlined style={{ color: "#999", cursor: "pointer" }} />
      </Tooltip>
    </span>
  );
}

/**
 * 用于 Statistic / Card 等非表头场景的渠道健康评分标题。
 * 渲染为 ReactNode，可直接作为 title 属性值。
 */
export function ChannelHealthScoreTitle() {
  return (
    <span className="inline-flex items-center gap-4">
      渠道平均健康评分
      <Tooltip
        title={
          <div style={{ lineHeight: 1.8 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>评分公式</div>
            <div>100 − (异常率×40 + 跨区率×30 + 重复率×30)</div>
            <div style={{ fontWeight: 600, marginTop: 8, marginBottom: 4 }}>指标说明</div>
            <div>• 异常率（权重 40%）：非独立 IP 扫码占比</div>
            <div>• 跨区率（权重 30%）：跨区域窜货扫码占比</div>
            <div>• 重复率（权重 30%）：重复扫码占比</div>
            <div style={{ fontWeight: 600, marginTop: 8, marginBottom: 4 }}>评级标准</div>
            <div>≥ 80 健康 &nbsp;|&nbsp; 60–79 预警 &nbsp;|&nbsp; &lt; 60 危险</div>
          </div>
        }
      >
        <InfoCircleOutlined style={{ color: "#999", cursor: "pointer" }} />
      </Tooltip>
    </span>
  );
}
