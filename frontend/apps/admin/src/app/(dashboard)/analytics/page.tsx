"use client";

import { useState } from "react";
import { Alert, Tabs } from "antd";
import { GiftOutlined, DollarOutlined } from "@ant-design/icons";
import CampaignAnalyticsContent from "../campaign-analytics/_components/CampaignAnalyticsContent";
import GmvContent from "../gmv/_components/GmvContent";
import { useAuthStore } from "@/lib/auth";

export default function DeepAnalyticsPage() {
  const [activeTab, setActiveTab] = useState("campaign");
  const user = useAuthStore((state) => state.user);
  const isActingAgency =
    user?.tenant_type === "agency" && Boolean(user.acting_tenant_id);

  if (isActingAgency) {
    return (
      <>
        <Alert
          className="mb-4"
          type="info"
          showIcon
          message="当前仅显示已授权的扫码分析"
          description="活动和码批次筛选、GMV 归因未包含在本次代运营授权中，因此不会展示空数据。"
        />
        <CampaignAnalyticsContent restrictedToAnalytics />
      </>
    );
  }

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
