"use client";

import { useMemo, useState } from "react";
import {
  Button,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Dayjs } from "dayjs";
import { AUDIT_ACTION_LABELS, formatAuditAction } from "@/lib/audit";
import { useCrud } from "@/lib/hooks";
import { STATUS_COLORS } from "@/lib/status-colors";

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

interface AuditOperator {
  id: string;
  name: string;
  email?: string | null;
}

export interface TenantAuditLog {
  id: string;
  tenant_id: string;
  timestamp: string;
  operator: AuditOperator;
  action: string;
  resource: string;
  result: string;
  details?: Record<string, unknown> | null;
}

const ACTION_OPTIONS = Object.entries(AUDIT_ACTION_LABELS).map(
  ([value, label]) => ({
    value,
    label,
  })
);

function getResourceLabel(log: TenantAuditLog): string {
  const name = log.details?.resource_name;
  if (typeof name === "string" && name) return name;
  const [, identifier] = log.resource.split(":", 2);
  return identifier || log.resource;
}

function getDetailSummary(log: TenantAuditLog): string {
  const details = log.details || {};
  if (typeof details.reason === "string" && details.reason)
    return details.reason;
  if (typeof details.before === "string" && typeof details.after === "string") {
    return `${details.before} → ${details.after}`;
  }
  return "—";
}

export default function AuditLogsPage() {
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [selectedLog, setSelectedLog] = useState<TenantAuditLog | null>(null);
  const {
    items: logs,
    total,
    page,
    pageSize,
    loading,
    setPage,
    setFilter,
  } = useCrud<TenantAuditLog>("/audit-logs");

  const updateFilters = (patch: Record<string, string | undefined>) => {
    const next = { ...filters };
    for (const [key, value] of Object.entries(patch)) {
      if (value) next[key] = value;
      else delete next[key];
    }
    setFilters(next);
    setFilter(next);
  };

  const columns = useMemo<ColumnsType<TenantAuditLog>>(
    () => [
      {
        title: "时间",
        dataIndex: "timestamp",
        width: 180,
        render: (value: string) => new Date(value).toLocaleString("zh-CN"),
      },
      {
        title: "操作人",
        dataIndex: "operator",
        width: 190,
        render: (operator: AuditOperator) => (
          <div>
            <div>{operator.name}</div>
            {operator.email && <Text type="secondary">{operator.email}</Text>}
          </div>
        ),
      },
      {
        title: "业务动作",
        dataIndex: "action",
        width: 150,
        render: (action: string) => (
          <Tag
            color={
              action.includes("deleted") || action.includes("disabled")
                ? "red"
                : "blue"
            }
          >
            {formatAuditAction(action)}
          </Tag>
        ),
      },
      {
        title: "业务对象",
        key: "resource",
        render: (_value, record) => getResourceLabel(record),
      },
      {
        title: "结果",
        dataIndex: "result",
        width: 90,
        render: (result: string) => (
          <Tag
            color={
              result === "success" ? STATUS_COLORS.success : STATUS_COLORS.error
            }
          >
            {result === "success" ? "成功" : "失败"}
          </Tag>
        ),
      },
      {
        title: "说明",
        key: "summary",
        ellipsis: true,
        render: (_value, record) => getDetailSummary(record),
      },
      {
        title: "操作",
        key: "actions",
        width: 80,
        render: (_value, record) => (
          <Button
            type="link"
            size="small"
            onClick={() => setSelectedLog(record)}
          >
            详情
          </Button>
        ),
      },
    ],
    []
  );

  return (
    <div>
      <div className="mb-4">
        <Title level={4} className="!mb-1">
          操作日志
        </Title>
        <Text type="secondary">
          查看本租户内的关键管理操作、危险变更与数据导出记录
        </Text>
      </div>

      <Space wrap className="mb-4">
        <RangePicker
          aria-label="操作时间"
          placeholder={["开始日期", "结束日期"]}
          onChange={(dates: null | [Dayjs | null, Dayjs | null]) =>
            updateFilters({
              start_time: dates?.[0]?.startOf("day").toISOString(),
              end_time: dates?.[1]?.endOf("day").toISOString(),
            })
          }
        />
        <Select
          aria-label="业务动作"
          placeholder="全部动作"
          allowClear
          showSearch
          optionFilterProp="label"
          options={ACTION_OPTIONS}
          style={{ width: 180 }}
          onChange={(value?: string) => updateFilters({ action: value })}
        />
        <Input.Search
          aria-label="搜索操作日志"
          placeholder="搜索操作人或业务对象"
          allowClear
          style={{ width: 260 }}
          onSearch={(value) =>
            updateFilters({ keyword: value.trim() || undefined })
          }
        />
      </Space>

      {logs.length === 0 && !loading ? (
        <Empty description="当前筛选条件下暂无操作记录" />
      ) : (
        <Table
          columns={columns}
          dataSource={logs}
          rowKey="id"
          loading={loading}
          pagination={{
            current: page,
            total,
            pageSize,
            onChange: setPage,
            showTotal: (count) => `共 ${count} 条`,
          }}
        />
      )}

      <Drawer
        title="操作详情"
        open={Boolean(selectedLog)}
        size="large"
        onClose={() => setSelectedLog(null)}
      >
        {selectedLog && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="时间">
              {new Date(selectedLog.timestamp).toLocaleString("zh-CN")}
            </Descriptions.Item>
            <Descriptions.Item label="操作人">
              {selectedLog.operator.name}
              {selectedLog.operator.email
                ? `（${selectedLog.operator.email}）`
                : ""}
            </Descriptions.Item>
            <Descriptions.Item label="业务动作">
              {formatAuditAction(selectedLog.action)}
            </Descriptions.Item>
            <Descriptions.Item label="业务对象">
              {getResourceLabel(selectedLog)}
            </Descriptions.Item>
            <Descriptions.Item label="执行结果">
              {selectedLog.result === "success" ? "成功" : "失败"}
            </Descriptions.Item>
            <Descriptions.Item label="记录内容">
              <pre className="m-0 whitespace-pre-wrap break-all text-xs">
                {JSON.stringify(selectedLog.details || {}, null, 2)}
              </pre>
            </Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>
    </div>
  );
}
