"use client";

import { Button, Card, Space } from "antd";
import {
  LinkOutlined,
  GiftOutlined,
  DownloadOutlined,
  LineChartOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";

export default function QuickActions() {
  const router = useRouter();

  return (
    <Card title="快速操作" size="small" style={{ height: "100%" }}>
      <Space orientation="vertical" className="w-full">
        <Button block icon={<LinkOutlined />} onClick={() => router.push("/codes")}>
          创建码批次
        </Button>
        <Button block icon={<GiftOutlined />} onClick={() => router.push("/campaigns")}>
          创建营销活动
        </Button>
        <Button block icon={<DownloadOutlined />} onClick={() => router.push("/exports")}>
          导出数据
        </Button>
        <Button block type="link" icon={<LineChartOutlined />} onClick={() => router.push("/analytics")}>
          查看全部活动分析
        </Button>
      </Space>
    </Card>
  );
}
