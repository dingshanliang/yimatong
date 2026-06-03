"use client";

import { useState } from "react";
import { Tabs } from "antd";
import { GiftOutlined, DollarOutlined } from "@ant-design/icons";
import CampaignAnalyticsContent from "../campaign-analytics/_components/CampaignAnalyticsContent";
import GmvContent from "../gmv/_components/GmvContent";

export default function DeepAnalyticsPage() {
  const [activeTab, setActiveTab] = useState("campaign");

  return (
    <Tabs
      activeKey={activeTab}
      onChange={setActiveTab}
      items={[
        {
          key: "campaign",
          label: (
            <span>
              <GiftOutlined /> 活动分析
            </span>
          ),
          children: <CampaignAnalyticsContent />,
        },
        {
          key: "gmv",
          label: (
            <span>
              <DollarOutlined /> GMV 归因
            </span>
          ),
          children: <GmvContent />,
        },
      ]}
    />
  );
}
