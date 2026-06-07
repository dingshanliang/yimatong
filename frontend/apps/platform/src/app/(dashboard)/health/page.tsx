"use client";

import { Button, Card, Col, Row, Statistic, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  HeartOutlined,
  WarningOutlined,
  StopOutlined,
  PauseCircleOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import dayjs from "dayjs";
import useSWR, { mutate } from "swr";
import api from "@/lib/api";
import { extractErrorMessage } from "@/lib/api";
import { useState } from "react";
import { TenantHealthScoreHeader } from "../_components/HealthScoreHeader";
import { message } from "antd";

const { Title } = Typography;

interface HealthOverview {
  healthy: number;
  warning: number;
  critical: number;
  dormant: number;
}

interface HealthTenant {
  tenant_id: string;
  tenant_name: string;
  health_score: number;
  health_status: string;
  last_scan_at: string | null;
  scans_last_7d: number;
  scans_last_30d: number;
  active_campaigns: number;
  days_until_expiry: number | null;
  last_login_at: string | null;
}

const STATUS_CONFIG: Record<string, { color: string; label: string }> = {
  healthy: { color: "green", label: "健康" },
  warning: { color: "orange", label: "警告" },
  critical: { color: "red", label: "危急" },
  dormant: { color: "default", label: "休眠" },
};

export default function HealthPage() {
  const router = useRouter();
  const { data: overview, isLoading: overviewLoading } = useSWR<HealthOverview>("/platform/health-overview");
  const { data: tenants, isLoading: tenantsLoading } = useSWR<HealthTenant[]>("/platform/health-tenants");
  const [refreshing, setRefreshing] = useState(false);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await api.post("/platform/health/refresh");
      message.success("健康度数据已刷新");
      mutate("/platform/health-overview");
      mutate("/platform/health-tenants");
    } catch (err) {
      message.error(extractErrorMessage(err, "刷新失败"));
    } finally {
      setRefreshing(false);
    }
  };

  const columns: ColumnsType<HealthTenant> = [
    {
      title: "租户",
      dataIndex: "tenant_name",
      key: "tenant_name",
      render: (name: string, record: HealthTenant) => (
        <a onClick={() => router.push(`/tenants/${record.tenant_id}`)}>{name}</a>
      ),
    },
    {
      title: <TenantHealthScoreHeader />,
      dataIndex: "health_score",
      key: "health_score",
      width: 100,
      sorter: (a, b) => a.health_score - b.health_score,
      render: (score: number) => {
        const color = score >= 75 ? "#52c41a" : score >= 50 ? "#faad14" : score >= 25 ? "#ff4d4f" : "#d9d9d9";
        return <span style={{ fontWeight: "bold", color }}>{score}</span>;
      },
    },
    {
      title: "状态",
      dataIndex: "health_status",
      key: "health_status",
      width: 80,
      filters: Object.entries(STATUS_CONFIG).map(([k, v]) => ({ text: v.label, value: k })),
      onFilter: (value, record) => record.health_status === value,
      render: (status: string) => {
        const cfg = STATUS_CONFIG[status];
        return <Tag color={cfg?.color}>{cfg?.label ?? status}</Tag>;
      },
    },
    { title: "7日扫码", dataIndex: "scans_last_7d", key: "scans_7d", width: 90 },
    { title: "30日扫码", dataIndex: "scans_last_30d", key: "scans_30d", width: 90 },
    { title: "活跃活动", dataIndex: "active_campaigns", key: "campaigns", width: 80 },
    {
      title: "到期天数",
      dataIndex: "days_until_expiry",
      key: "expiry",
      width: 90,
      render: (v: number | null) =>
        v !== null ? (
          v <= 30 ? <Tag color="red">{v} 天</Tag> : <span>{v} 天</span>
        ) : (
          <Tag>永久</Tag>
        ),
    },
    {
      title: "最后登录",
      dataIndex: "last_login_at",
      key: "last_login",
      width: 120,
      render: (v: string | null) => (v ? dayjs(v).format("MM-DD HH:mm") : "-"),
    },
  ];

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>客户健康度</Title>
        <Button icon={<ReloadOutlined />} loading={refreshing} onClick={handleRefresh}>
          刷新数据
        </Button>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={12} sm={6}>
          <Card loading={overviewLoading}>
            <Statistic title="健康" value={overview?.healthy ?? 0} prefix={<HeartOutlined />} styles={{ value: { color: "#52c41a" } }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={overviewLoading}>
            <Statistic title="警告" value={overview?.warning ?? 0} prefix={<WarningOutlined />} styles={{ value: { color: "#faad14" } }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={overviewLoading}>
            <Statistic title="危急" value={overview?.critical ?? 0} prefix={<StopOutlined />} styles={{ value: { color: "#ff4d4f" } }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={overviewLoading}>
            <Statistic title="休眠" value={overview?.dormant ?? 0} prefix={<PauseCircleOutlined />} />
          </Card>
        </Col>
      </Row>

      <Card style={{ marginTop: 16 }}>
        <Table<HealthTenant>
          rowKey="tenant_id"
          columns={columns}
          dataSource={tenants ?? []}
          loading={tenantsLoading}
          pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 个租户` }}
          size="middle"
        />
      </Card>
    </div>
  );
}
