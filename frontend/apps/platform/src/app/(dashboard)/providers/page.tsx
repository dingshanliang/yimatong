"use client";

import { Card, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { STATUS_MAP } from "@/lib/constants";

const { Title, Text } = Typography;

interface Provider {
  id: string;
  name: string;
  slug: string;
  status: string;
  managed_client_ids: string[];
}

export default function ProvidersPage() {
  const router = useRouter();
  const { data, isLoading } = useSWR<Provider[]>("/platform/service-providers");

  const columns: ColumnsType<Provider> = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
      render: (name: string, record: Provider) => (
        <a onClick={() => router.push(`/tenants/${record.id}`)}>{name}</a>
      ),
    },
    { title: "Slug", dataIndex: "slug", key: "slug", width: 150, ellipsis: true },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 80,
      render: (status: string) => {
        const s = STATUS_MAP[status as keyof typeof STATUS_MAP];
        return <Tag color={s?.color}>{s?.label ?? status}</Tag>;
      },
    },
    {
      title: "托管客户数",
      key: "clients",
      width: 100,
      render: (_: unknown, record: Provider) => record.managed_client_ids?.length ?? 0,
    },
  ];

  return (
    <div>
      <Title level={4} style={{ marginBottom: 16 }}>服务商管理</Title>
      <Card>
        <Table<Provider>
          rowKey="id"
          columns={columns}
          dataSource={data ?? []}
          loading={isLoading}
          pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 个服务商` }}
          size="middle"
          locale={{ emptyText: <Text type="secondary">暂无服务商数据。服务商管理功能将在后续版本中完善。</Text> }}
        />
      </Card>
    </div>
  );
}
