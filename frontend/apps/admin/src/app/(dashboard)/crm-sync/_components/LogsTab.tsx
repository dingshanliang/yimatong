"use client";

import { Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { SyncLog } from "./types";
import { SYNC_STATUS_MAP } from "./constants";
import { STATUS_COLORS } from "@/lib/status-colors";
import { formatDate } from "./utils";

interface LogsTabProps {
  logs: SyncLog[];
  loading: boolean;
}

const columns: ColumnsType<SyncLog> = [
  {
    title: "同步类型",
    dataIndex: "sync_type",
    key: "sync_type",
    render: (v: string) => <Tag color={STATUS_COLORS.processing}>{v}</Tag>,
  },
  {
    title: "外部 ID",
    dataIndex: "external_id",
    key: "external_id",
    render: (v: string) => v || "-",
  },
  {
    title: "数据摘要",
    dataIndex: "data_summary",
    key: "data_summary",
    ellipsis: true,
    render: (v: string) => v || "-",
  },
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    render: (v: string) => {
      const info = SYNC_STATUS_MAP[v] || {
        label: v,
        color: STATUS_COLORS.neutral,
      };
      return <Tag color={info.color}>{info.label}</Tag>;
    },
  },
  {
    title: "错误信息",
    dataIndex: "error_message",
    key: "error_message",
    ellipsis: true,
    render: (v: string | null) =>
      v ? (
        <Typography.Text type="danger" ellipsis title={v}>
          {v}
        </Typography.Text>
      ) : (
        "-"
      ),
  },
  {
    title: "创建时间",
    dataIndex: "created_at",
    key: "created_at",
    render: (v: string) => formatDate(v),
  },
];

export function LogsTab({ logs, loading }: LogsTabProps) {
  return (
    <Table
      columns={columns}
      dataSource={logs}
      rowKey="id"
      loading={loading}
      pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
      locale={{ emptyText: "暂无同步日志" }}
    />
  );
}
