"use client";

import { useEffect, useState } from "react";
import { Alert, Card, Col, Empty, Row, Space, Statistic, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title, Text } = Typography;

type Allocation = {
  id: string;
  batch_code?: string;
  product_name?: string;
  distributor_name?: string;
  region_name?: string;
  store_name?: string;
  quantity: number;
  remaining_quantity: number;
};

type RegionSummary = {
  id: string;
  name: string;
  city?: string;
  store_count: number;
  allocated_quantity: number;
};

type DistributorSummary = {
  scope: { type: "distributor" | "region"; id: string; name: string };
  region_count: number;
  store_count: number;
  allocated_quantity: number;
  pending_diversion_count: number;
  allocation_count: number;
  regions?: RegionSummary[];
  recent_allocations: Allocation[];
};

export default function ChannelPortalPage() {
  const [summary, setSummary] = useState<DistributorSummary | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    api
      .get("/channels/portal/distributor/summary")
      .then(({ data }) => {
        if (active) setSummary(data);
      })
      .catch(() => {
        if (active) setError(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const columns: ColumnsType<Allocation> = [
    { title: "码批次", dataIndex: "batch_code", render: (value) => value || "未命名批次" },
    { title: "产品", dataIndex: "product_name", render: (value) => value || "未命名产品" },
    {
      title: "流向范围",
      render: (_, record) => record.store_name || record.region_name || record.distributor_name || "经销商范围",
    },
    { title: "收货数量", dataIndex: "quantity", render: (value) => `${value || 0} 个` },
    { title: "批次余量", dataIndex: "remaining_quantity", render: (value) => <Tag color="blue">剩余 {value || 0}</Tag> },
  ];

  const regionColumns: ColumnsType<RegionSummary> = [
    { title: "区域", dataIndex: "name" },
    { title: "城市", dataIndex: "city", render: (value) => value || "-" },
    { title: "门店数", dataIndex: "store_count", render: (value) => `${value || 0} 家` },
    { title: "已收货码量", dataIndex: "allocated_quantity", render: (value) => `${value || 0} 个` },
  ];

  if (error) {
    return <Alert type="warning" message="未找到经销商入口范围" description="请联系品牌方管理员绑定经销商范围。" />;
  }

  return (
    <div>
      <div className="mb-5">
        <Title level={4} className="!mb-1">
          经销商工作台
        </Title>
        <Text type="secondary">{summary?.scope.name || "正在加载渠道数据"}</Text>
      </div>

      <Row gutter={[16, 16]} className="mb-5">
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="覆盖区域" value={summary?.region_count || 0} suffix="个" loading={loading} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="覆盖门店" value={summary?.store_count || 0} suffix="个" loading={loading} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="已收货码量" value={summary?.allocated_quantity || 0} loading={loading} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="待处理异常" value={summary?.pending_diversion_count || 0} loading={loading} />
          </Card>
        </Col>
      </Row>

      <Card title="区域覆盖" size="small" className="mb-5">
        {summary?.regions?.length ? (
          <Table columns={regionColumns} dataSource={summary.regions} rowKey="id" pagination={false} loading={loading} />
        ) : (
          <Space className="flex justify-center py-10">
            <Empty description="暂无区域数据" />
          </Space>
        )}
      </Card>

      <Card title="最近收货流向" size="small">
        {summary?.recent_allocations?.length ? (
          <Table columns={columns} dataSource={summary.recent_allocations} rowKey="id" pagination={false} loading={loading} />
        ) : (
          <Space className="flex justify-center py-10">
            <Empty description="暂无分配记录" />
          </Space>
        )}
      </Card>
    </div>
  );
}
