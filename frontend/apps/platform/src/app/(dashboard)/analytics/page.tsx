"use client";

import { Card, Col, Row, Statistic, Typography, Empty } from "antd";
import { BarChartOutlined, ScanOutlined, TeamOutlined } from "@ant-design/icons";
import useSWR from "swr";

const { Title, Text } = Typography;

interface HealthTenant {
  tenant_id: string;
  tenant_name: string;
  scans_last_30d: number;
  health_score: number;
}

export default function AnalyticsPage() {
  const { data: healthTenants } = useSWR<HealthTenant[]>("/platform/health-tenants");
  const { data: overview } = useSWR<{ healthy: number; warning: number; critical: number; dormant: number }>("/platform/health-overview");

  // Derive analytics from health data for now
  const totalScans30d = healthTenants?.reduce((sum, t) => sum + t.scans_last_30d, 0) ?? 0;
  const totalTenants = healthTenants?.length ?? 0;
  const avgScore = healthTenants?.length
    ? Math.round(healthTenants.reduce((sum, t) => sum + t.health_score, 0) / healthTenants.length)
    : 0;

  // Top tenants by scans
  const topTenants = [...(healthTenants ?? [])]
    .sort((a, b) => b.scans_last_30d - a.scans_last_30d)
    .slice(0, 10);

  return (
    <div>
      <Title level={4} style={{ marginBottom: 24 }}>数据分析</Title>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title="30日总扫码量"
              value={totalScans30d}
              prefix={<ScanOutlined />}
              styles={{ value: { color: "#722ed1" } }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title="活跃租户数"
              value={totalTenants}
              prefix={<TeamOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title="平均健康评分"
              value={avgScore}
              prefix={<BarChartOutlined />}
              suffix="/ 100"
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card title="租户扫码排名（30日）">
            {topTenants.length > 0 ? (
              <div>
                {topTenants.map((tenant, i) => (
                  <div
                    key={tenant.tenant_id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      padding: "8px 0",
                      borderBottom: "1px solid #f0f0f0",
                    }}
                  >
                    <span style={{ width: 30, fontWeight: "bold", color: i < 3 ? "#722ed1" : "#666" }}>
                      #{i + 1}
                    </span>
                    <span style={{ flex: 1 }}>{tenant.tenant_name}</span>
                    <span style={{ fontWeight: "bold" }}>{tenant.scans_last_30d.toLocaleString()}</span>
                    <span style={{ marginLeft: 8, color: "#999", fontSize: 12 }}>次扫码</span>
                  </div>
                ))}
              </div>
            ) : (
              <Empty description="暂无扫码数据" />
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card title="健康度分布">
            {overview && (
              <div style={{ display: "flex", gap: 24, justifyContent: "center", padding: "20px 0" }}>
                <div style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 28, fontWeight: "bold", color: "#52c41a" }}>{overview.healthy}</div>
                  <Text type="secondary">健康</Text>
                </div>
                <div style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 28, fontWeight: "bold", color: "#faad14" }}>{overview.warning}</div>
                  <Text type="secondary">警告</Text>
                </div>
                <div style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 28, fontWeight: "bold", color: "#ff4d4f" }}>{overview.critical}</div>
                  <Text type="secondary">危急</Text>
                </div>
                <div style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 28, fontWeight: "bold", color: "#d9d9d9" }}>{overview.dormant}</div>
                  <Text type="secondary">休眠</Text>
                </div>
              </div>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
