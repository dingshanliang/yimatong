"use client";

import { useMemo, useState } from "react";
import {
  Card,
  Table,
  Tag,
  Typography,
  DatePicker,
  Space,
  Button,
  Input,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import useSWR, { mutate } from "swr";
import { ACTION_COLORS } from "@/lib/constants";
import { STATUS_COLORS } from "@/lib/status-colors";

const { Title } = Typography;
const { RangePicker } = DatePicker;

interface AuditLog {
  id: string;
  operator_id: string;
  target_tenant_id: string;
  action: string;
  resource: string;
  timestamp: string;
}

interface AuditLogPage {
  items: AuditLog[];
  total: number;
  page: number;
  page_size: number;
}

export default function AuditLogsPage() {
  const [dateRange, setDateRange] = useState<
    [dayjs.Dayjs | null, dayjs.Dayjs | null] | null
  >(null);
  const [searchText, setSearchText] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const url = useMemo(() => {
    const params = new URLSearchParams();
    if (dateRange?.[0]) params.set("start_time", dateRange[0].toISOString());
    // 结束日期取当天末尾，保证结束日整天的日志都被包含。
    if (dateRange?.[1]) {
      params.set("end_time", dateRange[1].endOf("day").toISOString());
    }
    params.set("page", String(page));
    params.set("page_size", String(pageSize));
    return `/platform/audit-logs?${params.toString()}`;
  }, [dateRange, page, pageSize]);

  const { data, isLoading } = useSWR<AuditLogPage>(url);

  // 当前页内的关键词过滤；跨页检索通过日期范围缩小窗口。
  const filtered = data?.items.filter((log) => {
    if (!searchText) return true;
    const lower = searchText.toLowerCase();
    return (
      log.action.toLowerCase().includes(lower) ||
      log.resource.toLowerCase().includes(lower) ||
      log.operator_id.toLowerCase().includes(lower) ||
      log.target_tenant_id.toLowerCase().includes(lower)
    );
  });

  const columns: ColumnsType<AuditLog> = [
    {
      title: "时间",
      dataIndex: "timestamp",
      key: "timestamp",
      width: 170,
      render: (v: string) => dayjs(v).format("YYYY-MM-DD HH:mm:ss"),
    },
    {
      title: "操作者",
      dataIndex: "operator_id",
      key: "operator_id",
      width: 130,
      render: (v: string) => (
        <Tag>{v === "platform-admin" ? "平台管理员" : v}</Tag>
      ),
    },
    {
      title: "目标租户",
      dataIndex: "target_tenant_id",
      key: "target_tenant_id",
      width: 120,
      ellipsis: true,
      render: (v: string) =>
        v === "platform" ? (
          <Tag color={STATUS_COLORS.warning}>平台</Tag>
        ) : (
          <span style={{ fontSize: "var(--ymt-font-size-xs)" }}>
            {v.slice(0, 8)}…
          </span>
        ),
    },
    {
      title: "操作",
      dataIndex: "action",
      key: "action",
      width: 160,
      render: (v: string) => {
        const color =
          ACTION_COLORS[v.split(":")[0]] ?? STATUS_COLORS.processing;
        return <Tag color={color}>{v}</Tag>;
      },
    },
    {
      title: "资源",
      dataIndex: "resource",
      key: "resource",
      ellipsis: true,
    },
  ];

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <Title level={4} style={{ margin: 0 }}>
          审计日志
        </Title>
        <Button icon={<ReloadOutlined />} onClick={() => mutate(url)}>
          刷新
        </Button>
      </div>

      <Card>
        <Space style={{ marginBottom: 16 }} wrap>
          <RangePicker
            value={dateRange}
            onChange={(dates) => {
              setDateRange(dates);
              setPage(1);
            }}
            placeholder={["开始时间", "结束时间"]}
          />
          {dateRange && (
            <Button size="small" onClick={() => setDateRange(null)}>
              清除日期
            </Button>
          )}
          <Input
            placeholder="搜索操作/资源/租户"
            prefix={<SearchOutlined />}
            allowClear
            style={{ width: 250 }}
            value={searchText}
            onChange={(e) => {
              setSearchText(e.target.value);
              setPage(1);
            }}
          />
        </Space>

        <Table<AuditLog>
          rowKey="id"
          columns={columns}
          dataSource={filtered ?? []}
          loading={isLoading}
          pagination={{
            current: page,
            pageSize,
            total: data?.total ?? 0,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条记录`,
            onChange: (p, ps) => {
              setPage(p);
              setPageSize(ps);
            },
          }}
          scroll={{ x: 800 }}
          size="middle"
        />
      </Card>
    </div>
  );
}
