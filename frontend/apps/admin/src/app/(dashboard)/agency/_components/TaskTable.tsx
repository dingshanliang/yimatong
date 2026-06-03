"use client";

import { useMemo, useState } from "react";
import { Button, Card, Input, Select, Space, Table, Tag } from "antd";
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
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [priorityFilter, setPriorityFilter] = useState<string>("all");
  const [searchText, setSearchText] = useState("");

  const sortedTasks = useMemo(() => {
    const filtered = tasks.filter((task) => {
      if (statusFilter !== "all" && task.status !== statusFilter) return false;
      if (priorityFilter !== "all" && task.priority !== priorityFilter) return false;
      if (searchText && !task.title.toLowerCase().includes(searchText.toLowerCase())) return false;
      return true;
    });
    return [...filtered].sort((a, b) => {
      if (a.overdue !== b.overdue) return a.overdue ? -1 : 1;
      if (a.priority !== b.priority) return a.priority === "high" ? -1 : 1;
      return (a.due_date || "").localeCompare(b.due_date || "");
    });
  }, [tasks, statusFilter, priorityFilter, searchText]);

  const columns: ColumnsType<WorkbenchTask> = [
    {
      title: "任务", dataIndex: "title", key: "title",
      render: (title: string, record) => (
        <div>
          <div>{title}</div>
          {record.description && (
            <div className="mt-1 text-xs text-text-muted line-clamp-1">{record.description}</div>
          )}
        </div>
      ),
    },
    {
      title: "关联客户", dataIndex: "tenant_id", key: "tenant_id",
      render: (v: string, record) => {
        const client = clients.find((c) => c.id === v);
        return record.tenant_name || client?.name || v?.slice(0, 8) + "...";
      },
    },
    {
      title: "优先级", dataIndex: "priority", key: "priority", width: 80,
      render: (p: string) => {
        const info = PRIORITY_MAP[p] || { label: p, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "状态", dataIndex: "status", key: "status", width: 90,
      render: (s: string) => {
        const info = TASK_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "截止日", dataIndex: "due_date", key: "due_date", width: 120,
      render: (v: string | null, record) => {
        if (!v) return "—";
        const date = v.split("T")[0];
        return record.overdue ? <Tag color="red">{date}（逾期）</Tag> : date;
      },
    },
    {
      title: "操作", key: "actions", width: 140,
      render: (_: unknown, record) => {
        if (record.status === "completed" || record.status === "cancelled") {
          return <Button size="small" type="link" danger onClick={() => onDelete(record.id, record.title)}>删除</Button>;
        }
        return (
          <Space size="small">
            {record.status === "pending" && (
              <Button size="small" type="link" onClick={() => onUpdateStatus(record.id, "in_progress")}>开始</Button>
            )}
            {record.status === "in_progress" && (
              <Button size="small" type="link" onClick={() => onUpdateStatus(record.id, "completed")}>完成</Button>
            )}
            <Button size="small" type="link" onClick={() => onUpdateStatus(record.id, "cancelled")}>取消</Button>
          </Space>
        );
      },
    },
  ];

  return (
    <Card title="任务列表" size="small">
      <div className="mb-4">
        <Space wrap>
          <Input.Search
            placeholder="搜索任务"
            allowClear
            style={{ width: 200 }}
            onSearch={(v) => setSearchText(v)}
            onChange={(e) => { if (!e.target.value) setSearchText(""); }}
          />
          <Select
            value={statusFilter}
            style={{ width: 120 }}
            options={[
              { value: "all", label: "全部状态" },
              { value: "pending", label: "待处理" },
              { value: "in_progress", label: "进行中" },
            ]}
            onChange={setStatusFilter}
          />
          <Select
            value={priorityFilter}
            style={{ width: 120 }}
            options={[
              { value: "all", label: "全部优先级" },
              { value: "high", label: "高" },
              { value: "medium", label: "中" },
              { value: "low", label: "低" },
            ]}
            onChange={setPriorityFilter}
          />
        </Space>
      </div>
      <Table columns={columns} dataSource={sortedTasks} rowKey="id" pagination={false} size="small" />
    </Card>
  );
}
