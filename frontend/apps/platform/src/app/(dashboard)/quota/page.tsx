"use client";

import { Card, Progress, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { STATUS_MAP, PLAN_MAP } from "@/lib/constants";
import { STATUS_COLORS } from "@/lib/status-colors";
import {
  buildQuotaDisplay,
  quotaDisplayLabels,
  type QuotaKey,
  type QuotaUsageItem,
} from "./quota-display";

const { Text, Title } = Typography;

export default function QuotaPage() {
  const router = useRouter();
  const { data, isLoading } = useSWR<QuotaUsageItem[]>("/platform/quota-usage");

  const columns: ColumnsType<QuotaUsageItem> = [
    {
      title: "租户",
      dataIndex: "tenant_name",
      key: "tenant_name",
      render: (name: string, record: QuotaUsageItem) => (
        <a onClick={() => router.push(`/tenants/${record.tenant_id}`)}>
          {name}
        </a>
      ),
    },
    {
      title: "套餐",
      dataIndex: "plan",
      key: "plan",
      width: 100,
      render: (plan: string) => <Tag>{PLAN_MAP[plan]?.label ?? plan}</Tag>,
    },
    {
      title: "码量额度",
      key: "codes",
      width: 200,
      render: (_: unknown, record: QuotaUsageItem) =>
        renderQuotaBar(record, "max_codes"),
    },
    {
      title: "活动额度",
      key: "campaigns",
      width: 200,
      render: (_: unknown, record: QuotaUsageItem) =>
        renderQuotaBar(record, "max_campaigns"),
    },
    {
      title: "扫码额度",
      key: "scans",
      width: 200,
      render: (_: unknown, record: QuotaUsageItem) =>
        renderQuotaBar(record, "max_scans"),
    },
    {
      title: "账号额度",
      key: "accounts",
      width: 200,
      render: (_: unknown, record: QuotaUsageItem) =>
        renderQuotaBar(record, "max_accounts"),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 80,
      render: (status: string) => {
        const s = STATUS_MAP[status];
        return s ? <Tag color={s.color}>{s.label}</Tag> : <Tag>{status}</Tag>;
      },
    },
  ];

  return (
    <div>
      <Title level={4} style={{ marginBottom: 16 }}>
        额度监控
      </Title>
      <Card>
        <Table<QuotaUsageItem>
          rowKey="tenant_id"
          columns={columns}
          dataSource={data ?? []}
          loading={isLoading}
          pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 个租户` }}
          size="middle"
        />
      </Card>
    </div>
  );
}

export function renderQuotaBar(record: QuotaUsageItem, key: QuotaKey) {
  const display = buildQuotaDisplay(record, key);
  const labels = quotaDisplayLabels(display);
  const stateColor =
    display.state === "已超限"
      ? STATUS_COLORS.error
      : display.state === "已用尽" || display.state === "接近限额"
        ? STATUS_COLORS.warning
        : display.state === "正常" || display.state === "无限制"
          ? STATUS_COLORS.success
          : STATUS_COLORS.neutral;

  return (
    <div style={{ minWidth: 190 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "2px 8px" }}>
        <Text style={{ fontSize: "var(--ymt-font-size-xs)" }}>
          {labels.used}
        </Text>
        <Text style={{ fontSize: "var(--ymt-font-size-xs)" }}>
          {labels.limit}
        </Text>
        <Text style={{ fontSize: "var(--ymt-font-size-xs)" }}>
          {labels.remaining}
        </Text>
        <Text style={{ fontSize: "var(--ymt-font-size-xs)" }}>
          {labels.percent}
        </Text>
        <Tag color={stateColor} style={{ marginInlineEnd: 0 }}>
          {display.state}
        </Tag>
      </div>
      {display.progressPercent !== null && (
        <Progress
          percent={display.progressPercent}
          showInfo={false}
          size="small"
          status={display.state === "已超限" ? "exception" : "normal"}
        />
      )}
    </div>
  );
}
