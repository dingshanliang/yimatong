"use client";

import { Tabs, Typography } from "antd";
import { DashboardTab } from "./DashboardTab";
import { OrdersTab } from "./OrdersTab";
import { AttributionsTab } from "./AttributionsTab";
import { ROITab } from "./ROITab";

const { Title } = Typography;

const tabItems = [
  { key: "dashboard", label: "GMV 看板", children: <DashboardTab /> },
  { key: "orders", label: "外部订单", children: <OrdersTab /> },
  { key: "attributions", label: "归因记录", children: <AttributionsTab /> },
  { key: "roi", label: "ROI 报表", children: <ROITab /> },
];

export default function GmvContent() {
  return (
    <div>
      <Title level={4} className="!mb-4">GMV 归因</Title>
      <Tabs defaultActiveKey="dashboard" items={tabItems} />
    </div>
  );
}
