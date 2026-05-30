"use client";

import { Tabs, Typography } from "antd";
import type { TabsProps } from "antd";
import { CopyOutlined, ExperimentOutlined, FileTextOutlined, GiftOutlined } from "@ant-design/icons";
import { ExtractTab } from "./_components/ExtractTab";
import { CopywritingTab } from "./_components/CopywritingTab";
import { PagePlanTab } from "./_components/PagePlanTab";
import { CampaignTab } from "./_components/CampaignTab";

const { Title, Text } = Typography;

const tabItems: TabsProps["items"] = [
  { key: "extract", label: <span><ExperimentOutlined /> 资料识别</span>, children: <ExtractTab /> },
  { key: "copywriting", label: <span><CopyOutlined /> 文案生成</span>, children: <CopywritingTab /> },
  { key: "page-plan", label: <span><FileTextOutlined /> 页面方案</span>, children: <PagePlanTab /> },
  { key: "campaign", label: <span><GiftOutlined /> 活动方案</span>, children: <CampaignTab /> },
];

export default function AIAssistantPage() {
  return (
    <div>
      <div className="mb-4">
        <Title level={4} className="!mb-1">AI 助手</Title>
        <Text type="secondary">利用 AI 智能识别产品信息、生成营销文案、推荐页面方案和策划活动方案</Text>
      </div>
      <Tabs items={tabItems} size="large" />
    </div>
  );
}
