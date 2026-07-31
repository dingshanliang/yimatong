"use client";

import { useRouter } from "next/navigation";
import {
  Button,
  Card,
  Empty,
  Input,
  Progress,
  Select,
  Space,
  Table,
  Tag,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useAuthStore } from "@/lib/auth";
import { STATUS_COLORS } from "@/lib/status-colors";
import type { AgencyClientRow } from "./types";
import { STATUS_MAP } from "./types";

interface ClientTableProps {
  clients: AgencyClientRow[];
  loading: boolean;
  filter: { q?: string; readiness?: string; task_status?: string };
  hasAnyClients: boolean;
  hasError: boolean;
  onFilterChange: (filter: {
    q?: string;
    readiness?: string;
    task_status?: string;
  }) => void;
  onOpenChecklist: (clientId: string, clientName: string) => void;
  onCreateTask: (clientId: string, title: string) => void;
}

function renderEmpty(hasAnyClients: boolean, hasError: boolean) {
  if (hasError) return <Empty description={false} />;
  if (hasAnyClients) {
    return (
      <Empty
        description={
          <Space orientation="vertical" size={2}>
            <span>没有符合当前条件的客户</span>
            <span className="text-sm text-text-muted">
              调整搜索词或筛选条件后再试。
            </span>
          </Space>
        }
      />
    );
  }
  return (
    <Empty
      description={
        <Space orientation="vertical" size={2}>
          <span>还没有客户</span>
          <span className="text-sm text-text-muted">
            初始化新客户后，这里会显示上线准备度和下一步动作。
          </span>
        </Space>
      }
    />
  );
}

export function ClientTable({
  clients,
  loading,
  filter,
  hasAnyClients,
  hasError,
  onFilterChange,
  onOpenChecklist,
  onCreateTask,
}: ClientTableProps) {
  const router = useRouter();
  const columns: ColumnsType<AgencyClientRow> = [
    {
      title: "客户",
      dataIndex: "name",
      key: "name",
      render: (name: string, record) => {
        const info = STATUS_MAP[record.status] || {
          label: record.status,
          color: STATUS_COLORS.neutral,
        };
        return (
          <Space orientation="vertical" size={2}>
            <span>{name}</span>
            <Space size={4}>
              <Tag>{record.plan}</Tag>
              <Tag color={info.color}>{info.label}</Tag>
            </Space>
          </Space>
        );
      },
    },
    {
      title: "上线准备度",
      key: "readiness",
      render: (_: unknown, record) => (
        <Space orientation="vertical" size={2} className="min-w-28">
          <Progress
            percent={record.readiness.percent}
            size="small"
            status={record.readiness.ready ? "success" : "active"}
          />
          <span>
            {record.readiness.passed_count}/{record.readiness.total_count}
          </span>
        </Space>
      ),
    },
    {
      title: "缺失配置",
      key: "missing",
      render: (_: unknown, record) =>
        record.readiness.ready ? (
          <Tag color={STATUS_COLORS.success}>已具备上线条件</Tag>
        ) : (
          <span>缺：{record.readiness.missing_labels.join("、")}</span>
        ),
    },
    {
      title: "任务",
      key: "tasks",
      render: (_: unknown, record) => (
        <Space size={4} wrap>
          {record.task_summary.overdue > 0 && (
            <Tag color={STATUS_COLORS.error}>
              逾期 {record.task_summary.overdue}
            </Tag>
          )}
          {record.task_summary.pending > 0 && (
            <Tag color={STATUS_COLORS.processing}>
              待办 {record.task_summary.pending}
            </Tag>
          )}
          {record.task_summary.in_progress > 0 && (
            <Tag color={STATUS_COLORS.processing}>
              进行中 {record.task_summary.in_progress}
            </Tag>
          )}
          {record.task_summary.high_priority > 0 && (
            <Tag color={STATUS_COLORS.error}>
              高优先级 {record.task_summary.high_priority}
            </Tag>
          )}
          {record.task_summary.pending +
            record.task_summary.in_progress +
            record.task_summary.overdue ===
            0 && <span>—</span>}
        </Space>
      ),
    },
    {
      title: "到期日",
      dataIndex: "plan_expires_at",
      key: "plan_expires_at",
      render: (v: string | null) => {
        if (!v) return "—";
        const date = v.split("T")[0];
        const daysLeft = Math.ceil(
          (new Date(v).getTime() - Date.now()) / (1000 * 60 * 60 * 24)
        );
        return daysLeft < 0 ? (
          <Tag color={STATUS_COLORS.error}>{date}（已过期）</Tag>
        ) : daysLeft < 30 ? (
          <Tag color={STATUS_COLORS.error}>
            {date}（剩余{daysLeft}天）
          </Tag>
        ) : (
          date
        );
      },
    },
    {
      title: "下一步",
      key: "actions",
      render: (_: unknown, record) => (
        <Space size="small">
          <Button
            size="small"
            type="primary"
            onClick={async () => {
              await useAuthStore.getState().switchAgencyContext(record.id);
              router.push("/");
            }}
          >
            进入管理
          </Button>
          <Button
            size="small"
            onClick={() => router.push(record.next_action.href)}
          >
            {record.next_action.label}
          </Button>
          <Button
            size="small"
            onClick={() => onOpenChecklist(record.id, record.name)}
          >
            上线检查
          </Button>
          <Button
            size="small"
            type="link"
            onClick={() =>
              onCreateTask(record.id, record.next_action.task_title)
            }
          >
            创建任务
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Card title="客户上线队列" size="small" className="mb-6">
      <div className="mb-4">
        <Space wrap>
          <Input.Search
            placeholder="搜索客户名称"
            allowClear
            style={{ width: 300 }}
            onSearch={(v) => onFilterChange({ ...filter, q: v || undefined })}
            onChange={(e) => {
              if (!e.target.value) onFilterChange({ ...filter, q: undefined });
            }}
          />
          <Select
            placeholder="上线状态"
            value={filter.readiness || "all"}
            style={{ width: 180 }}
            options={[
              { value: "all", label: "全部客户" },
              { value: "ready", label: "已具备上线条件" },
              { value: "blocked", label: "需补齐配置" },
            ]}
            onChange={(v) => onFilterChange({ ...filter, readiness: v })}
          />
          <Select
            placeholder="任务状态"
            value={filter.task_status || "all"}
            style={{ width: 160 }}
            options={[
              { value: "all", label: "全部任务状态" },
              { value: "pending", label: "待处理" },
              { value: "in_progress", label: "进行中" },
              { value: "overdue", label: "逾期" },
            ]}
            onChange={(v) => onFilterChange({ ...filter, task_status: v })}
          />
        </Space>
      </div>
      <Table
        columns={columns}
        dataSource={clients}
        rowKey="id"
        loading={loading}
        pagination={false}
        size="small"
        locale={{ emptyText: renderEmpty(hasAnyClients, hasError) }}
      />
    </Card>
  );
}
