"use client";

import { Card, Col, Row, Statistic } from "antd";
import { CheckCircleOutlined, CheckSquareOutlined, ExclamationCircleOutlined, TeamOutlined, ToolOutlined } from "@ant-design/icons";
import type { WorkbenchSummary } from "./types";

export function StatsCards({ summary }: { summary: WorkbenchSummary }) {
  return (
    <Row gutter={[16, 16]} className="mb-6">
      <Col xs={24} sm={12} xl={5}><Card><Statistic title="客户总数" value={summary.total_clients} prefix={<TeamOutlined />} /></Card></Col>
      <Col xs={24} sm={12} xl={5}><Card><Statistic title="已具备上线条件" value={summary.ready_clients} prefix={<CheckCircleOutlined />} styles={{ content: { color: "#52c41a" } }} /></Card></Col>
      <Col xs={24} sm={12} xl={5}><Card><Statistic title="需补齐配置" value={summary.blocked_clients} prefix={<ToolOutlined />} styles={{ content: { color: "#faad14" } }} /></Card></Col>
      <Col xs={24} sm={12} xl={4}><Card><Statistic title="逾期任务" value={summary.overdue_tasks} prefix={<ExclamationCircleOutlined />} styles={{ content: { color: "#ff4d4f" } }} /></Card></Col>
      <Col xs={24} sm={12} xl={5}><Card><Statistic title="待办任务" value={summary.pending_tasks} prefix={<CheckSquareOutlined />} styles={{ content: { color: "#1890ff" } }} /></Card></Col>
    </Row>
  );
}
