"use client";

import { useEffect, useState } from "react";
import {
  Alert,
  Card,
  Col,
  Descriptions,
  Empty,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title, Text } = Typography;

type Allocation = {
  id: string;
  batch_code?: string;
  product_name?: string;
  quantity: number;
  remaining_quantity: number;
};

type StoreSummary = {
  scope: { type: "store"; id: string; name: string; code: string };
  address?: string;
  status: string;
  region_name?: string;
  distributor_name?: string;
  allocated_quantity: number;
  allocation_count: number;
  recent_allocations: Allocation[];
};

export default function StorePortalPage() {
  const [summary, setSummary] = useState<StoreSummary | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    api
      .get("/channels/portal/store/summary")
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
    {
      title: "码批次",
      dataIndex: "batch_code",
      render: (value) => value || "未命名批次",
    },
    {
      title: "产品",
      dataIndex: "product_name",
      render: (value) => value || "未命名产品",
    },
    {
      title: "收货数量",
      dataIndex: "quantity",
      render: (value) => `${value || 0} 个`,
    },
    {
      title: "批次余量",
      dataIndex: "remaining_quantity",
      render: (value) => <Tag color="#1d4ed8">剩余 {value || 0}</Tag>,
    },
  ];

  if (error) {
    return (
      <Alert
        type="warning"
        message="未找到门店入口范围"
        description="请联系品牌方管理员绑定门店范围。"
      />
    );
  }

  return (
    <div>
      <div className="mb-5">
        <Title level={4} className="!mb-1">
          门店工作台
        </Title>
        <Text type="secondary">
          {summary?.scope.name || "正在加载门店数据"}
        </Text>
      </div>

      <Row gutter={[16, 16]} className="mb-5">
        <Col xs={12} md={8}>
          <Card size="small">
            <Statistic
              title="已收货码量"
              value={summary?.allocated_quantity || 0}
              loading={loading}
            />
          </Card>
        </Col>
        <Col xs={12} md={8}>
          <Card size="small">
            <Statistic
              title="收货批次"
              value={summary?.allocation_count || 0}
              suffix="个"
              loading={loading}
            />
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card size="small">
            <Statistic
              title="门店状态"
              value={summary?.status === "active" ? "启用" : "停用"}
              loading={loading}
            />
          </Card>
        </Col>
      </Row>

      <Card title="门店资料" size="small" className="mb-5">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="门店编码">
            {summary?.scope.code || "-"}
          </Descriptions.Item>
          <Descriptions.Item label="所属区域">
            {summary?.region_name || "未绑定"}
          </Descriptions.Item>
          <Descriptions.Item label="所属经销商">
            {summary?.distributor_name || "未绑定"}
          </Descriptions.Item>
          <Descriptions.Item label="地址">
            {summary?.address || "未填写"}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="本店收货批次" size="small">
        {summary?.recent_allocations?.length ? (
          <Table
            columns={columns}
            dataSource={summary.recent_allocations}
            rowKey="id"
            pagination={false}
            loading={loading}
          />
        ) : (
          <Space className="flex justify-center py-10">
            <Empty description="暂无分配记录" />
          </Space>
        )}
      </Card>
    </div>
  );
}
