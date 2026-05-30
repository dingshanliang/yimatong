"use client";

import { Tabs, Typography } from "antd";
import { WebhooksTab } from "./_components/WebhooksTab";
import { ApiKeysTab } from "./_components/ApiKeysTab";
import { DeliveriesTab } from "./_components/DeliveriesTab";

const { Title } = Typography;

const tabItems = [
  { key: "webhooks", label: "Webhook 端点", children: <WebhooksTab /> },
  { key: "api-keys", label: "API Key", children: <ApiKeysTab /> },
  { key: "deliveries", label: "投递记录", children: <DeliveriesTab /> },
];

export default function IntegrationsPage() {
  return (
    <div>
      <Title level={4} className="!mb-4">集成管理</Title>
      <Tabs defaultActiveKey="webhooks" items={tabItems} />
    </div>
  );
}
