"use client";

import { Card, Empty, Space, Table, Tag } from "antd";

import { STATUS_COLORS } from "@/lib/status-colors";

import {
  type ActionItem,
  actionCompletionRate,
  computeMetricDeltas,
  formatDelta,
  type RetrospectiveRead,
} from "./types";

interface PeriodComparisonProps {
  retros: RetrospectiveRead[];
}

/** 相邻期复盘对比（beads: yimatong-bgag.10，PRD §4.5：指标环比 + 上期动作完成率）。
 * 至少 2 期已完成/有 scorecard 的复盘才显示对比；否则 Empty。
 */
export default function PeriodComparison({ retros }: PeriodComparisonProps) {
  if (retros.length < 2) {
    return (
      <Card title="相邻期对比" size="small">
        <Empty description="至少需要 2 期复盘才能对比" />
      </Card>
    );
  }

  // 按期次升序，逐对比较 r[i-1] → r[i]
  const sorted = [...retros].sort((a, b) => a.period_day - b.period_day);
  const rows: {
    key: string;
    from: number;
    to: number;
    deltas: ReturnType<typeof computeMetricDeltas>;
    prevCompletion: number | null;
  }[] = [];
  for (let i = 1; i < sorted.length; i++) {
    const prev = sorted[i - 1];
    const curr = sorted[i];
    rows.push({
      key: `${prev.period_day}-${curr.period_day}`,
      from: prev.period_day,
      to: curr.period_day,
      deltas: computeMetricDeltas(
        prev.scorecard_snapshot,
        curr.scorecard_snapshot
      ),
      prevCompletion: actionCompletionRate(prev.actions as ActionItem[]),
    });
  }

  const metricKeys = rows[0]?.deltas.map((d) => d.key) ?? [];

  const columns = [
    {
      title: "对比区间",
      dataIndex: "from",
      key: "from",
      render: (_: unknown, row: (typeof rows)[number]) =>
        `第 ${row.from} 天 → 第 ${row.to} 天`,
    },
    ...metricKeys.map((mk) => ({
      title: rows[0].deltas.find((d) => d.key === mk)?.label ?? mk,
      key: mk,
      render: (_: unknown, row: (typeof rows)[number]) => {
        const d = row.deltas.find((x) => x.key === mk);
        if (!d || d.delta === null)
          return <Tag color={STATUS_COLORS.neutral}>数据不足</Tag>;
        const color =
          d.delta > 0
            ? STATUS_COLORS.success
            : d.delta < 0
              ? STATUS_COLORS.error
              : STATUS_COLORS.neutral;
        return <Tag color={color}>{formatDelta(d.delta)}</Tag>;
      },
    })),
    {
      title: "上期动作完成率",
      key: "prevCompletion",
      render: (_: unknown, row: (typeof rows)[number]) =>
        row.prevCompletion === null ? (
          <Tag color={STATUS_COLORS.neutral}>无动作</Tag>
        ) : (
          <Tag color={STATUS_COLORS.processing}>
            {row.prevCompletion.toFixed(0)}%
          </Tag>
        ),
    },
  ];

  return (
    <Card title="相邻期对比" size="small">
      <Space direction="vertical" style={{ width: "100%" }}>
        <Table
          dataSource={rows}
          columns={columns}
          pagination={false}
          size="small"
          rowKey="key"
        />
      </Space>
    </Card>
  );
}
