"use client";

import { Tooltip } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";

/**
 * 订单转化率表头（扫码 UV → 关联订单）
 * 用于：活动排行、ROI 分析
 */
export function OrderConversionRateHeader() {
  return (
    <span className="inline-flex items-center gap-4">
      转化率
      <Tooltip
        title={
          <div style={{ lineHeight: 1.8 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>订单转化率</div>
            <div>关联订单数 ÷ 扫码独立访客（UV）× 100%</div>
            <div style={{ fontWeight: 600, marginTop: 8, marginBottom: 4 }}>指标说明</div>
            <div>衡量扫码流量最终转化为实际订单的比例。</div>
            <div>数值越高，说明扫码带来的实际购买转化效果越好。</div>
          </div>
        }
      >
        <InfoCircleOutlined style={{ color: "#999", cursor: "pointer" }} />
      </Tooltip>
    </span>
  );
}

/**
 * 订单转化率标题（用于 Statistic 等非表头场景）
 */
export function OrderConversionRateTitle() {
  return (
    <span className="inline-flex items-center gap-4">
      平均转化率
      <Tooltip
        title={
          <div style={{ lineHeight: 1.8 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>订单转化率</div>
            <div>关联订单数 ÷ 扫码独立访客（UV）× 100%</div>
            <div style={{ fontWeight: 600, marginTop: 8, marginBottom: 4 }}>指标说明</div>
            <div>衡量扫码流量最终转化为实际订单的比例。</div>
            <div>数值越高，说明扫码带来的实际购买转化效果越好。</div>
          </div>
        }
      >
        <InfoCircleOutlined style={{ color: "#999", cursor: "pointer" }} />
      </Tooltip>
    </span>
  );
}

/**
 * 领取转化率表头（扫码量 → 权益领取）
 * 用于：区域分析
 */
export function ClaimConversionRateHeader() {
  return (
    <span className="inline-flex items-center gap-4">
      转化率
      <Tooltip
        title={
          <div style={{ lineHeight: 1.8 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>领取转化率</div>
            <div>权益领取数 ÷ 扫码量 × 100%</div>
            <div style={{ fontWeight: 600, marginTop: 8, marginBottom: 4 }}>指标说明</div>
            <div>衡量消费者扫码后领取权益（优惠券、积分等）的比例。</div>
            <div>数值越高，说明权益对消费者的吸引力越强。</div>
          </div>
        }
      >
        <InfoCircleOutlined style={{ color: "#999", cursor: "pointer" }} />
      </Tooltip>
    </span>
  );
}

/**
 * 渠道领取转化率表头（扫码 UV → 预估领取）
 * 用于：渠道转化率对比
 */
export function ChannelConversionRateHeader() {
  return (
    <span className="inline-flex items-center gap-4">
      转化率%
      <Tooltip
        title={
          <div style={{ lineHeight: 1.8 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>渠道领取转化率</div>
            <div>预估领取数 ÷ 扫码独立访客（UV）× 100%</div>
            <div style={{ fontWeight: 600, marginTop: 8, marginBottom: 4 }}>指标说明</div>
            <div>按渠道的扫码 UV 占比分摊总领取量，计算各渠道的转化率。</div>
            <div>数值越高，说明该渠道的消费者领取意愿越强。</div>
          </div>
        }
      >
        <InfoCircleOutlined style={{ color: "#999", cursor: "pointer" }} />
      </Tooltip>
    </span>
  );
}
