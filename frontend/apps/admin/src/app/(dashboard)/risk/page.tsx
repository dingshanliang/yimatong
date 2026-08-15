"use client";

import { Alert, Tabs, Typography } from "antd";
import { useAuthStore } from "@/lib/auth";
import { riskAccessForPrincipal } from "@/lib/risk-access";
import { RulesTab } from "./_components/RulesTab";
import { InterceptionsTab } from "./_components/InterceptionsTab";
import { AlertsTab } from "./_components/AlertsTab";
import { NotificationsTab } from "./_components/NotificationsTab";
import { PausesTab } from "./_components/PausesTab";

const { Title } = Typography;

export default function RiskPage() {
  const user = useAuthStore((state) => state.user);
  const access = riskAccessForPrincipal(user);

  if (!access.canRead) {
    return <Alert type="warning" showIcon title="当前账号无权访问风控中心" />;
  }

  const tabItems = [
    { key: "rules", label: "风控规则", children: <RulesTab access={access} /> },
    { key: "interceptions", label: "拦截记录", children: <InterceptionsTab /> },
    { key: "alerts", label: "预警列表", children: <AlertsTab /> },
    {
      key: "pauses",
      label: "活动暂停",
      children: <PausesTab access={access} />,
    },
    { key: "notifications", label: "风控通知", children: <NotificationsTab /> },
  ];

  return (
    <div>
      <Title level={4} className="!mb-4">
        风控中心
      </Title>
      <Tabs defaultActiveKey="rules" items={tabItems} />
    </div>
  );
}
