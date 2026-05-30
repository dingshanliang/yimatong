"use client";

import { useCrud } from "@/lib/hooks";
import { Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";

type DiversionClue = Record<string, unknown> & { id: string };

const columns: ColumnsType<DiversionClue> = [
  { title: "码 ID", dataIndex: "public_id", key: "public_id" },
  { title: "预期区域", dataIndex: "expected_region", key: "expected_region" },
  { title: "实际城市", dataIndex: "detected_city", key: "detected_city" },
  {
    title: "状态",
    dataIndex: "resolved",
    key: "resolved",
    render: (v: boolean) => <Tag color={v ? "green" : "red"}>{v ? "已处理" : "待处理"}</Tag>,
  },
];

export function DiversionTab() {
  const { items, total, page, loading, setPage } = useCrud<DiversionClue>("/channels/diversion-clues");

  return (
    <Table
      columns={columns}
      dataSource={items}
      rowKey="id"
      loading={loading}
      pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
    />
  );
}
