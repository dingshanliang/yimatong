"use client";

import { Tabs, Typography } from "antd";
import { RulesTab } from "./_components/RulesTab";
import { InterceptionsTab } from "./_components/InterceptionsTab";
import { AlertsTab } from "./_components/AlertsTab";
import { NotificationsTab } from "./_components/NotificationsTab";

const { Title } = Typography;

const tabItems = [
  { key: "rules", label: "风控规则", children: <RulesTab /> },
  { key: "interceptions", label: "拦截记录", children: <InterceptionsTab /> },
  { key: "alerts", label: "预警列表", children: <AlertsTab /> },
  { key: "notifications", label: "风控通知", children: <NotificationsTab /> },
];

export default function RiskPage() {
  return (
    <div>
      <Title level={4} className="!mb-4">风控中心</Title>
      <Tabs defaultActiveKey="rules" items={tabItems} />
    </div>
  );
}
