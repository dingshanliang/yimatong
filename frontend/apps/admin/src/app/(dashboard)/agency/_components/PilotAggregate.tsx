"use client";

import { App, Spin, Table, Tag } from "antd";
import { useCallback, useEffect, useState } from "react";

import api, { extractErrorMessage } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { STATUS_COLORS } from "@/lib/status-colors";

/** 待办复盘派生态 → Tag preset（复用 STATUS_COLORS 单一事实来源）。 */
const PENDING_STATUS_TAG: Record<string, string> = {
  pending: STATUS_COLORS.processing,
  overdue: STATUS_COLORS.error,
  overdue_completed: STATUS_COLORS.warning,
};

const PENDING_STATUS_LABEL: Record<string, string> = {
  pending: "待填写",
  overdue: "逾期",
  overdue_completed: "逾期完成",
};

interface PendingRetro {
  retro_id: string;
  period_day: number;
  next_review_date: string | null;
  derived_status: string;
}

interface PilotClientRow {
  client_id: string;
  client_name: string | null;
  client_slug: string | null;
  milestone_summary: { achieved_count: number; total: number };
  pending_retrospectives: PendingRetro[];
  full_pilot_access: boolean;
}

interface PilotAggregateData {
  summary: { total_clients: number; clients_with_pending_retros: number };
  clients: PilotClientRow[];
}

/** 代运营/平台试点跨租户聚合板（beads: yimatong-bgag.10，PRD §4.5）。
 * 展示各授权客户的里程碑达成 + 待办复盘；点击客户行进入该客户 /pilot 详情（context-switch）。
 */
export default function PilotAggregate() {
  const { message } = App.useApp();
  const switchAgencyContext = useAuthStore((s) => s.switchAgencyContext);
  const [data, setData] = useState<PilotAggregateData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchAggregate = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api.get<PilotAggregateData>("/ops/pilot-aggregate");
      setData(resp.data ?? null);
    } catch (err) {
      message.error(extractErrorMessage(err, "加载试点聚合失败"));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void fetchAggregate();
  }, [fetchAggregate]);

  if (loading) return <Spin />;
  if (!data || data.clients.length === 0) {
    return <div style={{ padding: 16 }}>暂无授权客户的试点数据</div>;
  }

  const columns = [
    {
      title: "客户",
      dataIndex: "client_name",
      key: "client_name",
      render: (name: string | null, row: PilotClientRow) =>
        name ?? row.client_slug ?? row.client_id.slice(0, 8),
    },
    {
      title: "里程碑达成",
      key: "milestone",
      render: (_: unknown, row: PilotClientRow) => {
        const { achieved_count, total } = row.milestone_summary;
        const ratio = total > 0 ? achieved_count / total : 0;
        const color =
          ratio >= 0.8
            ? STATUS_COLORS.success
            : ratio >= 0.4
              ? STATUS_COLORS.processing
              : STATUS_COLORS.neutral;
        return (
          <Tag color={color}>
            {achieved_count}/{total}
          </Tag>
        );
      },
    },
    {
      title: "待办复盘",
      key: "pending",
      render: (_: unknown, row: PilotClientRow) =>
        row.pending_retrospectives.length === 0 ? (
          <Tag color={STATUS_COLORS.neutral}>无</Tag>
        ) : (
          <span>
            {row.pending_retrospectives.map((p) => (
              <Tag
                key={p.retro_id}
                color={
                  PENDING_STATUS_TAG[p.derived_status] ?? STATUS_COLORS.neutral
                }
                style={{ marginBottom: 4 }}
              >
                第{p.period_day}天·
                {PENDING_STATUS_LABEL[p.derived_status] ?? p.derived_status}
              </Tag>
            ))}
          </span>
        ),
    },
    {
      title: "操作",
      key: "action",
      render: (_: unknown, row: PilotClientRow) => (
        <a
          onClick={() => {
            if (switchAgencyContext) {
              switchAgencyContext(row.client_id);
            }
          }}
        >
          进入客户试点
        </a>
      ),
    },
  ];

  return (
    <Table
      dataSource={data.clients}
      columns={columns}
      rowKey="client_id"
      pagination={false}
      size="small"
    />
  );
}
