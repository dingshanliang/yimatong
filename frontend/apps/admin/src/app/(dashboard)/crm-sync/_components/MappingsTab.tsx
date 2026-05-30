"use client";

import { Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { SyncMapping } from "./types";
import { DIRECTION_MAP, SYNC_STATUS_MAP } from "./constants";
import { formatDate, maskId } from "./utils";

interface MappingsTabProps {
  mappings: SyncMapping[];
  loading: boolean;
}

const columns: ColumnsType<SyncMapping> = [
  { title: "消费者 ID", dataIndex: "consumer_id", key: "consumer_id", render: (v: string) => maskId(v) },
  { title: "外部 ID", dataIndex: "external_id", key: "external_id", render: (v: string) => v || "-" },
  { title: "来源系统", dataIndex: "source_system", key: "source_system", render: (v: string) => <Tag>{v || "-"}</Tag> },
  { title: "同步方向", dataIndex: "sync_direction", key: "sync_direction", render: (v: string) => { const info = DIRECTION_MAP[v] || { label: v, color: "default" }; return <Tag color={info.color}>{info.label}</Tag>; } },
  { title: "最近同步", dataIndex: "last_synced_at", key: "last_synced_at", render: (v: string | null) => formatDate(v) },
  { title: "状态", dataIndex: "status", key: "status", render: (v: string) => { const info = SYNC_STATUS_MAP[v] || { label: v, color: "default" }; return <Tag color={info.color}>{info.label}</Tag>; } },
];

export function MappingsTab({ mappings, loading }: MappingsTabProps) {
  return (
    <Table columns={columns} dataSource={mappings} rowKey="id" loading={loading}
      pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
      locale={{ emptyText: "暂无同步映射记录" }}
    />
  );
}
