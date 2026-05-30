"use client";

import { useCrud } from "@/lib/hooks";
import { Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";

type Interception = Record<string, unknown> & { id: string };

const columns: ColumnsType<Interception> = [
  { title: "规则 ID", dataIndex: "risk_rule_id", key: "risk_rule_id", render: (v: string) => v?.slice(0, 8) + "..." },
  { title: "活动 ID", dataIndex: "campaign_id", key: "campaign_id", render: (v: string) => v ? v.slice(0, 8) + "..." : "—" },
  { title: "动作", dataIndex: "action", key: "action", render: (a: string) => <Tag color={a === "block" ? "red" : "orange"}>{a === "block" ? "拦截" : "预警"}</Tag> },
  { title: "消费者", dataIndex: "consumer_id", key: "consumer_id", render: (v: string) => v?.slice(0, 8) + "..." || "—" },
];

export function InterceptionsTab() {
  const { items, total, page, loading, setPage } = useCrud<Interception>("/risk-rules/interceptions");

  return (
    <Table columns={columns} dataSource={items} rowKey="id" loading={loading}
      pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
    />
  );
}
