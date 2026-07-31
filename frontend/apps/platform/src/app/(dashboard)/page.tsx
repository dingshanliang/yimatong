"use client";

import { Card, Col, Row, Statistic, Typography, Tag, Timeline } from "antd";
import {
  TeamOutlined,
  CheckCircleOutlined,
  PauseCircleOutlined,
  WarningOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import dayjs from "dayjs";
import useSWR from "swr";

const { Title, Text } = Typography;

interface AuditLog {
  id: string;
  operator_id: string;
  target_tenant_id: string;
  action: string;
  resource: string;
  timestamp: string;
}

interface DashboardSummary {
  total_tenants: number;
  active_tenants: number;
  suspended_tenants: number;
  terminated_tenants: number;
  expiring_soon: number;
  recent_audit_logs: AuditLog[];
}

export default function PlatformDashboardPage() {
  const { data, isLoading } = useSWR<DashboardSummary>("/platform/dashboard");

  return (
    <div>
      <Title level={4} style={{ marginBottom: 24 }}>
        平台概览
      </Title>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={isLoading}>
            <Statistic
              title="租户总数"
              value={data?.total_tenants ?? 0}
              prefix={<TeamOutlined />}
              styles={{ value: { color: "var(--ymt-color-brand-primary)" } }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={isLoading}>
            <Statistic
              title="活跃租户"
              value={data?.active_tenants ?? 0}
              prefix={<CheckCircleOutlined />}
              styles={{ value: { color: "var(--ymt-color-feedback-success)" } }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={isLoading}>
            <Statistic
              title="暂停租户"
              value={data?.suspended_tenants ?? 0}
              prefix={<PauseCircleOutlined />}
              styles={{ value: { color: "var(--ymt-color-feedback-warning)" } }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={isLoading}>
            <Statistic
              title="即将过期（30天）"
              value={data?.expiring_soon ?? 0}
              prefix={<WarningOutlined />}
              styles={{ value: { color: "var(--ymt-color-feedback-danger)" } }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card
            title={
              <span>
                <FileTextOutlined style={{ marginRight: 8 }} />
                近期活动
              </span>
            }
            loading={isLoading}
          >
            {data?.recent_audit_logs?.length ? (
              <Timeline
                items={data.recent_audit_logs.map((log) => ({
                  color: "var(--ymt-color-feedback-info)",
                  children: (
                    <div key={log.id}>
                      <Text strong>{log.action}</Text>
                      <Tag style={{ marginLeft: 8 }}>{log.resource}</Tag>
                      <br />
                      <Text
                        type="secondary"
                        style={{ fontSize: "var(--ymt-font-size-xs)" }}
                      >
                        {dayjs(log.timestamp).format("YYYY-MM-DD HH:mm:ss")} ·{" "}
                        {log.operator_id}
                      </Text>
                    </div>
                  ),
                }))}
              />
            ) : (
              <Text type="secondary">暂无近期活动</Text>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
