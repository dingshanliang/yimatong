"use client";

import { useState, useCallback } from "react";
import { useCrud } from "@/lib/hooks";
import {
  Table,
  DatePicker,
  Select,
  Input,
  Space,
  Typography,
  Empty,
  Tag,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { type Dayjs } from "dayjs";

const { Title } = Typography;
const { RangePicker } = DatePicker;
const { Search } = Input;

interface AuditLog {
  id: string;
  timestamp: string;
  operator: string;
  action_type: string;
  target: string;
  ip: string;
  detail: string;
}

const ACTION_TYPE_OPTIONS = [
  { value: "", label: "全部类型" },
  { value: "create", label: "创建" },
  { value: "update", label: "更新" },
  { value: "delete", label: "删除" },
  { value: "export", label: "导出" },
  { value: "login", label: "登录" },
  { value: "config", label: "配置变更" },
];

const ACTION_TYPE_MAP: Record<string, { label: string; color: string }> = {
  create: { label: "创建", color: "green" },
  update: { label: "更新", color: "blue" },
  delete: { label: "删除", color: "red" },
  export: { label: "导出", color: "orange" },
  login: { label: "登录", color: "cyan" },
  config: { label: "配置变更", color: "purple" },
};

export default function AuditLogsPage() {
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs] | null>(null);

  const {
    items: logs,
    total,
    page,
    loading,
    setPage,
    setFilter,
    resetFilters,
  } = useCrud<AuditLog>("/platform/audit-logs");

  // Build and apply all filters at once, replacing previous state entirely.
  const applyFilters = useCallback(
    (overrides: Record<string, string | number> = {}) => {
      const params: Record<string, string | number> = {};
      if (overrides.start_date) params.start_date = overrides.start_date;
      if (overrides.end_date) params.end_date = overrides.end_date;
      if (overrides.action_type) params.action_type = overrides.action_type;
      if (overrides.keyword) params.keyword = overrides.keyword;
      resetFilters();
      if (Object.keys(params).length > 0) setFilter(params);
    },
    [resetFilters, setFilter],
  );

  const handleSearch = (value: string) => {
    const overrides: Record<string, string | number> = {};
    if (dateRange) {
      overrides.start_date = dateRange[0].format("YYYY-MM-DD");
      overrides.end_date = dateRange[1].format("YYYY-MM-DD");
    }
    if (value) overrides.keyword = value;
    applyFilters(overrides);
  };

  const columns: ColumnsType<AuditLog> = [
    {
      title: "时间",
      dataIndex: "timestamp",
      key: "timestamp",
      width: 180,
      render: (v: string) => {
        try {
          return new Date(v).toLocaleString("zh-CN");
        } catch {
          return v;
        }
      },
    },
    {
      title: "操作人",
      dataIndex: "operator",
      key: "operator",
      width: 120,
    },
    {
      title: "操作类型",
      dataIndex: "action_type",
      key: "action_type",
      width: 120,
      render: (type: string) => {
        const info = ACTION_TYPE_MAP[type] || { label: type, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "目标",
      dataIndex: "target",
      key: "target",
      width: 200,
    },
    {
      title: "IP",
      dataIndex: "ip",
      key: "ip",
      width: 140,
    },
    {
      title: "详情",
      dataIndex: "detail",
      key: "detail",
      ellipsis: true,
    },
  ];

  return (
    <div>
      <Title level={4}>操作日志</Title>

      <div className="mb-4">
        <Space wrap>
          <RangePicker
            placeholder={["开始日期", "结束日期"]}
            onChange={(dates) => {
              const overrides: Record<string, string | number> = {};
              if (dates && dates[0] && dates[1]) {
                setDateRange([dates[0], dates[1]]);
                overrides.start_date = dates[0].format("YYYY-MM-DD");
                overrides.end_date = dates[1].format("YYYY-MM-DD");
              } else {
                setDateRange(null);
              }
              applyFilters(overrides);
            }}
          />
          <Select
            value={undefined}
            placeholder="全部类型"
            onChange={(v) => {
              const overrides: Record<string, string | number> = {};
              if (dateRange) {
                overrides.start_date = dateRange[0].format("YYYY-MM-DD");
                overrides.end_date = dateRange[1].format("YYYY-MM-DD");
              }
              if (v) overrides.action_type = v;
              applyFilters(overrides);
            }}
            options={ACTION_TYPE_OPTIONS}
            style={{ width: 140 }}
            allowClear
          />
          <Search
            placeholder="搜索操作人或目标"
            allowClear
            onSearch={handleSearch}
            onClear={() => applyFilters(dateRange ? {
              start_date: dateRange[0].format("YYYY-MM-DD"),
              end_date: dateRange[1].format("YYYY-MM-DD"),
            } : {})}
            style={{ width: 240 }}
          />
        </Space>
      </div>

      {logs.length === 0 && !loading ? (
        <Empty description="暂无审计日志数据" />
      ) : (
        <Table
          columns={columns}
          dataSource={logs}
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
      )}

    </div>
  );
}
