"use client";

import { Button, Card, Space, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { AgencyClientRow, WorkbenchTask } from "./types";
import { PRIORITY_MAP, TASK_STATUS_MAP } from "./types";

interface TaskTableProps {
  tasks: WorkbenchTask[];
  clients: AgencyClientRow[];
  onUpdateStatus: (taskId: string, newStatus: string) => void;
  onDelete: (taskId: string, taskTitle: string) => void;
}

export function TaskTable({ tasks, clients, onUpdateStatus, onDelete }: TaskTableProps) {
  const sortedTasks = [...tasks].sort((a, b) => {
    if (a.overdue !== b.overdue) return a.overdue ? -1 : 1;
    if (a.priority !== b.priority) return a.priority === "high" ? -1 : 1;
    return (a.due_date || "").localeCompare(b.due_date || "");
  });
  const columns: ColumnsType<WorkbenchTask> = [
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
    { title: "截止日", dataIndex: "due_date", key: "due_date", render: (v: string | null, record) => {
      if (!v) return "—";
      const date = v.split("T")[0];
      return record.overdue ? <Tag color="red">{date}（逾期）</Tag> : date;
    } },
    { title: "操作", key: "actions", render: (_: unknown, record) => {
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
      <Table columns={columns} dataSource={sortedTasks} rowKey="id" pagination={false} size="small" />
    </Card>
  );
}
