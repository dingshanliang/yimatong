"use client";

import { Tooltip } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";

/**
 * 租户健康评分表头组件，带 Tooltip 解释计算原理。
 *
 * 租户健康评分：扫码活跃度(0-30) + 活跃活动数(0-20) + 登录活跃度(0-20) + 到期天数(0-30) = 满分 100
 */
export function TenantHealthScoreHeader() {
  return (
    <span className="inline-flex items-center gap-4">
      健康评分
      <Tooltip
        title={
          <div style={{ lineHeight: 1.8 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>
              综合评分（满分 100）
            </div>
            <div>• 扫码活跃度（0–30 分）：近 7 天扫码量</div>
            <div>• 活跃活动数（0–20 分）：进行中的活动数量</div>
            <div>• 登录活跃度（0–20 分）：最近登录时间</div>
            <div>• 订阅到期天数（0–30 分）：距套餐到期剩余天数</div>
            <div style={{ fontWeight: 600, marginTop: 8, marginBottom: 4 }}>
              评级标准
            </div>
            <div>
              ≥ 75 健康 &nbsp;|&nbsp; 50–74 预警 &nbsp;|&nbsp; 25–49 异常
              &nbsp;|&nbsp; &lt; 25 停滞
            </div>
          </div>
        }
      >
        <InfoCircleOutlined
          style={{ color: "var(--ymt-color-text-tertiary)", cursor: "pointer" }}
        />
      </Tooltip>
    </span>
  );
}

/**
 * 用于 Statistic 等非表头场景的租户平均健康评分标题。
 */
export function TenantHealthScoreTitle() {
  return (
    <span className="inline-flex items-center gap-4">
      平均健康评分
      <Tooltip
        title={
          <div style={{ lineHeight: 1.8 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>
              综合评分（满分 100）
            </div>
            <div>• 扫码活跃度（0–30 分）：近 7 天扫码量</div>
            <div>• 活跃活动数（0–20 分）：进行中的活动数量</div>
            <div>• 登录活跃度（0–20 分）：最近登录时间</div>
            <div>• 订阅到期天数（0–30 分）：距套餐到期剩余天数</div>
            <div style={{ fontWeight: 600, marginTop: 8, marginBottom: 4 }}>
              评级标准
            </div>
            <div>
              ≥ 75 健康 &nbsp;|&nbsp; 50–74 预警 &nbsp;|&nbsp; 25–49 异常
              &nbsp;|&nbsp; &lt; 25 停滞
            </div>
          </div>
        }
      >
        <InfoCircleOutlined
          style={{ color: "var(--ymt-color-text-tertiary)", cursor: "pointer" }}
        />
      </Tooltip>
    </span>
  );
}
