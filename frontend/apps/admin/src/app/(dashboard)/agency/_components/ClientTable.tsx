"use client";

import { App, Button, Card, Input, Space, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Client } from "./types";
import { STATUS_MAP } from "./types";

interface ClientTableProps {
  clients: Client[];
  loading: boolean;
  onSearch: (q: string) => void;
  onOpenChecklist: (clientId: string, clientName: string) => void;
}

export function ClientTable({ clients, loading, onSearch, onOpenChecklist }: ClientTableProps) {
  const columns: ColumnsType<Client> = [
    { title: "客户名称", dataIndex: "name", key: "name" },
    { title: "套餐", dataIndex: "plan", key: "plan", render: (p: string) => <Tag>{p}</Tag> },
    { title: "状态", dataIndex: "status", key: "status", render: (s: string) => {
      const info = STATUS_MAP[s] || { label: s, color: "default" }; return <Tag color={info.color}>{info.label}</Tag>;
    }},
    { title: "到期日", dataIndex: "plan_expires_at", key: "plan_expires_at", render: (v: string | null) => {
      if (!v) return "—";
      const date = v.split("T")[0];
      const daysLeft = Math.ceil((new Date(v).getTime() - Date.now()) / (1000 * 60 * 60 * 24));
      return daysLeft < 0 ? <Tag color="red">{date}（已过期）</Tag> : daysLeft < 30 ? <Tag color="red">{date}（剩余{daysLeft}天）</Tag> : date;
    }},
    { title: "创建时间", dataIndex: "created_at", key: "created_at", render: (v: string) => v?.split("T")[0] || "—" },
    { title: "操作", key: "actions", render: (_: unknown, record: Client) => (
      <Button size="small" onClick={() => onOpenChecklist(record.id, record.name)}>检查清单</Button>
    )},
  ];

  return (
    <Card title="客户列表" size="small" className="mb-6">
      <div className="mb-4">
        <Space>
          <Input.Search placeholder="搜索客户名称" allowClear style={{ width: 300 }}
            onSearch={(v) => onSearch(v)}
            onChange={(e) => { if (!e.target.value) onSearch(""); }}
          />
        </Space>
      </div>
      <Table columns={columns} dataSource={clients} rowKey="id" loading={loading} pagination={false} size="small" />
    </Card>
  );
}
