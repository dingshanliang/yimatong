"use client";

import { Tabs, Typography } from "antd";
import { DistributorTab } from "./_components/DistributorTab";
import { RegionTab } from "./_components/RegionTab";
import { StoreTab } from "./_components/StoreTab";
import { AssignTab } from "./_components/AssignTab";
import { DiversionTab } from "./_components/DiversionTab";

const { Title } = Typography;

const tabItems = [
  { key: "distributors", label: "经销商", children: <DistributorTab /> },
  { key: "regions", label: "区域", children: <RegionTab /> },
  { key: "stores", label: "门店", children: <StoreTab /> },
  { key: "assign", label: "码段分配", children: <AssignTab /> },
  { key: "diversion", label: "窜货线索", children: <DiversionTab /> },
];

export default function ChannelsPage() {
  return (
    <div>
      <Title level={4} className="!mb-4">渠道管理</Title>
      <Tabs defaultActiveKey="distributors" items={tabItems} />
    </div>
  );
}
