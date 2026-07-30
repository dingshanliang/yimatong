"use client";

import { Card, Col, Row, Statistic } from "antd";
import {
  CheckCircleOutlined,
  CheckSquareOutlined,
  ExclamationCircleOutlined,
  TeamOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import type { WorkbenchSummary } from "./types";

interface StatsCardsProps {
  summary: WorkbenchSummary;
  onCardClick: (
    filterType: "overdue" | "blocked" | "ready" | "pending"
  ) => void;
}

export function StatsCards({ summary, onCardClick }: StatsCardsProps) {
  return (
    <Row gutter={[16, 16]} className="mb-6">
      <Col xs={24} sm={12} xl={5}>
        <Card>
          <Statistic
            title="客户总数"
            value={summary.total_clients}
            prefix={<TeamOutlined />}
          />
        </Card>
      </Col>
      <Col xs={24} sm={12} xl={5}>
        <Card
          className="cursor-pointer transition-shadow hover:shadow-md"
          onClick={() => onCardClick("ready")}
        >
          <Statistic
            title="已具备上线条件"
            value={summary.ready_clients}
            prefix={<CheckCircleOutlined />}
            styles={{ content: { color: "var(--ymt-color-feedback-success)" } }}
          />
        </Card>
      </Col>
      <Col xs={24} sm={12} xl={5}>
        <Card
          className="cursor-pointer transition-shadow hover:shadow-md"
          onClick={() => onCardClick("blocked")}
        >
          <Statistic
            title="需补齐配置"
            value={summary.blocked_clients}
            prefix={<ToolOutlined />}
            styles={{ content: { color: "var(--ymt-color-feedback-warning)" } }}
          />
        </Card>
      </Col>
      <Col xs={24} sm={12} xl={4}>
        <Card
          className="cursor-pointer transition-shadow hover:shadow-md"
          onClick={() => onCardClick("overdue")}
        >
          <Statistic
            title="逾期任务"
            value={summary.overdue_tasks}
            prefix={<ExclamationCircleOutlined />}
            styles={{ content: { color: "var(--ymt-color-feedback-danger)" } }}
          />
        </Card>
      </Col>
      <Col xs={24} sm={12} xl={5}>
        <Card
          className="cursor-pointer transition-shadow hover:shadow-md"
          onClick={() => onCardClick("pending")}
        >
          <Statistic
            title="待办任务"
            value={summary.pending_tasks}
            prefix={<CheckSquareOutlined />}
            styles={{ content: { color: "var(--ymt-color-feedback-info)" } }}
          />
        </Card>
      </Col>
    </Row>
  );
}
