"use client";

import { Table, Tag, Typography } from "antd";
import { useCrud } from "@/lib/hooks";
import { STATUS_COLORS } from "@/lib/status-colors";
import type { ColumnsType } from "antd/es/table";

type DiversionClue = Record<string, unknown> & {
  id: string;
  public_id: string;
  expected_region: string;
  detected_city: string;
  resolved: boolean;
};

export function DiversionTab() {
  const { items, total, page, loading, setPage } = useCrud<DiversionClue>(
    "/channels/diversion-clues"
  );

  const columns: ColumnsType<DiversionClue> = [
    { title: "码 ID", dataIndex: "public_id", key: "public_id" },
    {
      title: "预期区域",
      dataIndex: "expected_region",
      key: "expected_region",
      render: (v: string) => v || "—",
    },
    {
      title: "实际城市",
      dataIndex: "detected_city",
      key: "detected_city",
      render: (v: string) => v || "—",
    },
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
      width: 80,
      render: () => (
        <Typography.Text type="secondary">请到渠道管理调查</Typography.Text>
      ),
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
