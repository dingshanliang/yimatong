"use client";

import { Card, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { STATUS_MAP, PLAN_MAP } from "@/lib/constants";
import { STATUS_COLORS } from "@/lib/status-colors";

const { Title } = Typography;

interface QuotaUsageItem {
  tenant_id: string;
  tenant_name: string;
  plan: string;
  quota: Record<string, number> | null;
  status: string;
}

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

function renderQuotaBar(record: QuotaUsageItem, key: string) {
  const limit = record.quota?.[key];
  if (limit === undefined || limit === null) return <Tag>未设置</Tag>;
  if (limit === -1) return <Tag color={STATUS_COLORS.processing}>无限制</Tag>;

  // For now show limit only — actual usage tracking will come with health metrics in Wave 4
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{ fontSize: "var(--ymt-font-size-xs)" }}>
        限额 {limit.toLocaleString()}
      </span>
    </div>
  );
}
