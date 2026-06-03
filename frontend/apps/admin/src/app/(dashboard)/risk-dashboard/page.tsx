"use client";

import { Button, Col, Row, Space, Typography } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import {
  AlertIndicator,
  CrossRegionCard,
  DiversionCard,
  RepeatScansCard,
  ChannelHealthCard,
  ConversionCard,
  useRiskExport,
  useAuthStore,
} from "./_components/RiskDashboardComponents";

const { Title } = Typography;

export default function RiskDashboardPage() {
  const { handleExport } = useRiskExport();
  const tenantId = useAuthStore((s) => s.user?.tenant_id ?? null);

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">风控看板</Title>
        <Space>
          <AlertIndicator tenantId={tenantId} />
          <Button icon={<DownloadOutlined />} onClick={() => handleExport("alerts")}>导出预警</Button>
          <Button icon={<DownloadOutlined />} onClick={() => handleExport("diversions")}>导出窜货</Button>
        </Space>
      </div>
      <Row gutter={[16, 16]}>
        <Col span={12}><RepeatScansCard /></Col>
        <Col span={12}><CrossRegionCard /></Col>
      </Row>
      <div className="mt-4">
        <Row gutter={[16, 16]}>
          <Col span={12}><ChannelHealthCard /></Col>
          <Col span={12}><ConversionCard /></Col>
        </Row>
      </div>
      <div className="mt-4">
        <DiversionCard />
      </div>
    </div>
  );
}
