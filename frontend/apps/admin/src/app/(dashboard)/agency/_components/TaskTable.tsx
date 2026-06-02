"use client";

import { Button, Card, Select, Space, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Client, Task } from "./types";
import { PRIORITY_MAP, TASK_STATUS_MAP } from "./types";

interface TaskTableProps {
  tasks: Task[];
  clients: Client[];
  taskFilter: { tenant_id?: string; status?: string };
  onFilterChange: (filter: { tenant_id?: string; status?: string }) => void;
  onUpdateStatus: (taskId: string, newStatus: string) => void;
  onDelete: (taskId: string, taskTitle: string) => void;
}

export function TaskTable({ tasks, clients, taskFilter, onFilterChange, onUpdateStatus, onDelete }: TaskTableProps) {
  const columns: ColumnsType<Task> = [
    { title: "任务", dataIndex: "title", key: "title" },
    { title: "关联客户", dataIndex: "tenant_id", key: "tenant_id", render: (v: string, record) => {
      const client = clients.find((c) => c.id === v); return record.tenant_name || client?.name || v?.slice(0, 8) + "...";
    }},
    { title: "优先级", dataIndex: "priority", key: "priority", render: (p: string) => {
      const info = PRIORITY_MAP[p] || { label: p, color: "default" }; return <Tag color={info.color}>{info.label}</Tag>;
    }},
    { title: "状态", dataIndex: "status", key: "status", render: (s: string) => {
      const info = TASK_STATUS_MAP[s] || { label: s, color: "default" }; return <Tag color={info.color}>{info.label}</Tag>;
    }},
    { title: "截止日", dataIndex: "due_date", key: "due_date", render: (v: string | null) => v?.split("T")[0] || "—" },
    { title: "操作", key: "actions", render: (_: unknown, record: Task) => {
      if (record.status === "completed" || record.status === "cancelled") {
        return <Button size="small" type="link" danger onClick={() => onDelete(record.id, record.title)}>删除</Button>;
      }
      return (
        <Space size="small">
          {record.status === "pending" && <Button size="small" type="link" onClick={() => onUpdateStatus(record.id, "in_progress")}>开始</Button>}
          {record.status === "in_progress" && <Button size="small" type="link" onClick={() => onUpdateStatus(record.id, "completed")}>完成</Button>}
          <Button size="small" type="link" onClick={() => onUpdateStatus(record.id, "cancelled")}>取消</Button>
        </Space>
      );
    }},
  ];

  return (
    <Card title="任务列表" size="small">
      <div className="mb-4">
        <Space>
          <Select placeholder="按客户筛选" allowClear style={{ width: 200 }}
            options={clients.map((c) => ({ value: c.id, label: c.name }))}
            onChange={(v) => onFilterChange({ ...taskFilter, tenant_id: v || undefined })}
          />
          <Select placeholder="按状态筛选" allowClear style={{ width: 150 }}
            options={Object.entries(TASK_STATUS_MAP).map(([k, v]) => ({ value: k, label: v.label }))}
            onChange={(v) => onFilterChange({ ...taskFilter, status: v || undefined })}
          />
        </Space>
      </div>
      <Table columns={columns} dataSource={tasks} rowKey="id" pagination={false} size="small" />
    </Card>
  );
}
