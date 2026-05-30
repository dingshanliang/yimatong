"use client";

import { Card, Col, Row, Statistic } from "antd";
import { TeamOutlined, UserOutlined, FileTextOutlined, CheckSquareOutlined } from "@ant-design/icons";

export function StatsCards({ totalClients, activeClients, onboardingClients, pendingTasks }: {
  totalClients: number; activeClients: number; onboardingClients: number; pendingTasks: number;
}) {
  return (
    <Row gutter={[16, 16]} className="mb-6">
      <Col xs={24} sm={12} lg={6}><Card><Statistic title="客户总数" value={totalClients} prefix={<TeamOutlined />} /></Card></Col>
      <Col xs={24} sm={12} lg={6}><Card><Statistic title="活跃客户" value={activeClients} prefix={<UserOutlined />} valueStyle={{ color: "#52c41a" }} /></Card></Col>
      <Col xs={24} sm={12} lg={6}><Card><Statistic title="配置中客户" value={onboardingClients} prefix={<FileTextOutlined />} valueStyle={{ color: "#faad14" }} /></Card></Col>
      <Col xs={24} sm={12} lg={6}><Card><Statistic title="待办任务" value={pendingTasks} prefix={<CheckSquareOutlined />} valueStyle={{ color: "#1890ff" }} /></Card></Col>
    </Row>
  );
}
