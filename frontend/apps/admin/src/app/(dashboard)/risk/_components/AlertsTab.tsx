"use client";

import { useCrud } from "@/lib/hooks";
import { App, Button, Popconfirm, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";

type Alert = Record<string, unknown> & { id: string };

export function AlertsTab() {
  const { message } = App.useApp();
  const { items, total, page, loading, setPage, mutate } =
    useCrud<Alert>("/risk-alerts");

  const columns: ColumnsType<Alert> = [
    { title: "码 ID", dataIndex: "public_id", key: "public_id" },
    {
      title: "类型",
      dataIndex: "alert_type",
      key: "alert_type",
      render: (t: string) => <Tag color={STATUS_COLORS.warning}>{t}</Tag>,
    },
    { title: "详情", dataIndex: "detail", key: "detail", ellipsis: true },
    {
      title: "状态",
      dataIndex: "resolved",
      key: "resolved",
      render: (v: boolean) => (
        <Tag color={v ? STATUS_COLORS.success : STATUS_COLORS.error}>
          {v ? "已处理" : "待处理"}
        </Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record) =>
        !record.resolved ? (
          <Popconfirm
            title="确认标记为已处理？"
            onConfirm={async () => {
              await api.post(`/risk-alerts/${record.id as string}/resolve`);
              message.success("已处理");
              mutate();
            }}
          >
            <Button size="small" type="link">
              处理
            </Button>
          </Popconfirm>
        ) : null,
    },
  ];

  return (
    <Table
      columns={columns}
      dataSource={items}
      rowKey="id"
      loading={loading}
      pagination={{
        current: page,
        total,
        pageSize: 20,
        onChange: setPage,
        showTotal: (t) => `共 ${t} 条`,
      }}
    />
  );
}
