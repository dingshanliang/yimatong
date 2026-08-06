"use client";

import { Card, Empty, Space, Tag, Timeline, Typography } from "antd";

import { STATUS_COLORS, STATUS_TOKEN_COLORS } from "@/lib/status-colors";

import {
  type DerivedDuration,
  formatDuration,
  MILESTONE_COLOR_KEY,
  type MilestoneItem,
} from "./types";

const { Text } = Typography;

interface MilestoneTimelineProps {
  milestones: MilestoneItem[];
  derivedDurations: DerivedDuration[];
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "未达成";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** 试点里程碑时间线（beads: yimatong-bgag.5，PRD §4.5）。只读展示。 */
export default function MilestoneTimeline({
  milestones,
  derivedDurations,
}: MilestoneTimelineProps) {
  if (!milestones.length) {
    return (
      <Card title="试点里程碑" size="small">
        <Empty description="暂无里程碑数据" />
      </Card>
    );
  }

  const items = milestones.map((m) => {
    const colorKey = MILESTONE_COLOR_KEY[m.status];
    return {
      color: STATUS_TOKEN_COLORS[colorKey],
      children: (
        <div>
          <Text strong>{m.label}</Text>
          {m.status === "achieved" ? (
            <Text type="secondary" style={{ marginLeft: 8 }}>
              {formatDateTime(m.achieved_at)}
            </Text>
          ) : (
            <Tag color={STATUS_COLORS.neutral} style={{ marginLeft: 8 }}>
              未达成
            </Tag>
          )}
        </div>
      ),
    };
  });

  return (
    <Card
      title="试点里程碑"
      size="small"
      extra={
        derivedDurations.length > 0 ? (
          <Space>
            {derivedDurations.map((d) => (
              <Tag
                key={d.label}
                color={
                  d.status === "computed"
                    ? STATUS_COLORS.processing
                    : STATUS_COLORS.neutral
                }
              >
                {d.label}：{formatDuration(d.seconds)}
              </Tag>
            ))}
          </Space>
        ) : null
      }
    >
      <Timeline items={items} />
    </Card>
  );
}
