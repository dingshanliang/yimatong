"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import {
  Button,
  Card,
  Col,
  DatePicker,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Dayjs } from "dayjs";

const { Text } = Typography;

type DailyTrend = { date: string; gmv: number; orders: number };
type ChannelBreakdown = { channel: string; gmv: number; orders: number };
type CampaignBreakdown = {
  campaign_id: string;
  campaign_name: string;
  gmv: number;
  orders: number;
  avg_confidence: number;
};

interface DashboardData {
  total_gmv: number;
  attributed_orders: number;
  total_orders: number;
  attribution_rate: number;
  daily_trend: DailyTrend[];
  by_channel: ChannelBreakdown[];
  by_campaign: CampaignBreakdown[];
}

const trendColumns: ColumnsType<DailyTrend> = [
  { title: "日期", dataIndex: "date", key: "date" },
  { title: "归因 GMV", dataIndex: "gmv", key: "gmv", render: (v: number) => `¥${v.toLocaleString()}` },
  { title: "归因订单", dataIndex: "orders", key: "orders" },
];

const channelColumns: ColumnsType<ChannelBreakdown> = [
  { title: "渠道", dataIndex: "channel", key: "channel", render: (v: string) => <Tag>{v}</Tag> },
  { title: "归因 GMV", dataIndex: "gmv", key: "gmv", render: (v: number) => `¥${v.toLocaleString()}` },
  { title: "归因订单", dataIndex: "orders", key: "orders" },
];

const campaignColumns: ColumnsType<CampaignBreakdown> = [
  { title: "活动名称", dataIndex: "campaign_name", key: "campaign_name" },
  { title: "归因 GMV", dataIndex: "gmv", key: "gmv", render: (v: number) => `¥${v.toLocaleString()}` },
  { title: "归因订单", dataIndex: "orders", key: "orders" },
  {
    title: "平均置信度", dataIndex: "avg_confidence", key: "avg_confidence",
    render: (v: number) => <Text type={v >= 0.8 ? "success" : v >= 0.5 ? "warning" : "danger"}>{(v * 100).toFixed(0)}%</Text>,
  },
];

export function DashboardTab() {
  const [data, setData] = useState<DashboardData>({
    total_gmv: 0, attributed_orders: 0, total_orders: 0,
    attribution_rate: 0, daily_trend: [], by_channel: [], by_campaign: [],
  });
  const [loading, setLoading] = useState(false);
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs] | null>(null);

  const fetch = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (dateRange) {
        params.set("start_date", dateRange[0].startOf("day").toISOString());
        params.set("end_date", dateRange[1].endOf("day").toISOString());
      }
      const { data: d } = await api.get(`/gmv/dashboard?${params}`);
      setData(d || {});
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, [dateRange]);

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <DatePicker.RangePicker
          value={dateRange}
          onChange={(v) => setDateRange(v as [Dayjs, Dayjs] | null)}
          allowClear
          placeholder={["开始日期", "结束日期"]}
        />
        <Button size="small" onClick={fetch} loading={loading}>刷新</Button>
      </div>

      <Row gutter={[16, 16]} className="mb-6">
        <Col span={6}>
          <Card size="small"><Statistic title="总 GMV" value={data.total_gmv} prefix="¥" loading={loading} /></Card>
        </Col>
        <Col span={6}>
          <Card size="small"><Statistic title="归因订单" value={data.attributed_orders} loading={loading} /></Card>
        </Col>
        <Col span={6}>
          <Card size="small"><Statistic title="总订单" value={data.total_orders} loading={loading} /></Card>
        </Col>
        <Col span={6}>
          <Card size="small"><Statistic title="归因率" value={data.attribution_rate} suffix="%" loading={loading} /></Card>
        </Col>
      </Row>

      <Space direction="vertical" size="large" className="w-full">
        <Card title="日趋势" size="small">
          <Table
            columns={trendColumns}
            dataSource={data.daily_trend}
            rowKey="date"
            loading={loading}
            size="small"
            pagination={false}
          />
        </Card>

        <Row gutter={16}>
          <Col span={12}>
            <Card title="渠道分布" size="small">
              <Table
                columns={channelColumns}
                dataSource={data.by_channel}
                rowKey="channel"
                loading={loading}
                size="small"
                pagination={false}
              />
            </Card>
          </Col>
          <Col span={12}>
            <Card title="活动分布" size="small">
              <Table
                columns={campaignColumns}
                dataSource={data.by_campaign}
                rowKey="campaign_id"
                loading={loading}
                size="small"
                pagination={false}
              />
            </Card>
          </Col>
        </Row>
      </Space>
    </div>
  );
}
