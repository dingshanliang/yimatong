"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Dayjs } from "dayjs";

type DailyTrend = { date: string; gmv: number; orders: number };
type ChannelBreakdown = { channel: string; gmv: number; orders: number };
type CampaignBreakdown = {
  campaign_id: string;
  campaign_name: string;
  gmv: number;
  orders: number;
};

interface DashboardData {
  total_gmv: number;
  attributed_orders: number;
  total_orders: number;
  unattributed_orders: number;
  quarantined_order_count: number;
  order_data_quality: "complete" | "incomplete";
  attribution_rate: null;
  attribution_rate_status: "unavailable_non_cohort";
  daily_trend: DailyTrend[];
  by_channel: ChannelBreakdown[];
  by_campaign: CampaignBreakdown[];
}

const trendColumns: ColumnsType<DailyTrend> = [
  { title: "日期", dataIndex: "date", key: "date" },
  {
    title: "归因 GMV",
    dataIndex: "gmv",
    key: "gmv",
    render: (v: number) => `¥${v.toLocaleString()}`,
  },
  { title: "归因订单", dataIndex: "orders", key: "orders" },
];

const channelColumns: ColumnsType<ChannelBreakdown> = [
  {
    title: "渠道",
    dataIndex: "channel",
    key: "channel",
    render: (v: string) => <Tag>{v}</Tag>,
  },
  {
    title: "归因 GMV",
    dataIndex: "gmv",
    key: "gmv",
    render: (v: number) => `¥${v.toLocaleString()}`,
  },
  { title: "归因订单", dataIndex: "orders", key: "orders" },
];

const campaignColumns: ColumnsType<CampaignBreakdown> = [
  { title: "活动名称", dataIndex: "campaign_name", key: "campaign_name" },
  {
    title: "归因 GMV",
    dataIndex: "gmv",
    key: "gmv",
    render: (v: number) => `¥${v.toLocaleString()}`,
  },
  { title: "归因订单", dataIndex: "orders", key: "orders" },
];

export function DashboardTab() {
  const [data, setData] = useState<DashboardData>({
    total_gmv: 0,
    attributed_orders: 0,
    total_orders: 0,
    unattributed_orders: 0,
    quarantined_order_count: 0,
    order_data_quality: "complete",
    attribution_rate: null,
    attribution_rate_status: "unavailable_non_cohort",
    daily_trend: [],
    by_channel: [],
    by_campaign: [],
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
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
      setError(false);
    } catch {
      // 加载失败要区分于“暂无数据”，不能静默吞掉
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetch();
  }, [dateRange]);

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <DatePicker.RangePicker
          value={dateRange}
          onChange={(v) => setDateRange(v as [Dayjs, Dayjs] | null)}
          allowClear
          placeholder={["开始日期", "结束日期"]}
        />
        <Button size="small" onClick={fetch} loading={loading}>
          刷新
        </Button>
      </div>

      {error && (
        <Alert
          className="mb-4"
          type="error"
          showIcon
          message="GMV 看板加载失败"
          description="数据暂时无法加载，这不代表当前没有数据。"
          action={
            <Button size="small" onClick={() => void fetch()}>
              重试
            </Button>
          }
        />
      )}

      {(data.unattributed_orders > 0 ||
        data.order_data_quality === "incomplete") && (
        <Alert
          className="mb-4"
          type="warning"
          showIcon
          message="订单归因数据仍在收集或隔离中"
          description={`待归因 ${data.unattributed_orders} 单；隔离 ${data.quarantined_order_count} 单。当前归因 GMV 和订单数仅包含账本有效且已确认的发生事实。`}
        />
      )}

      {data.attribution_rate_status === "unavailable_non_cohort" && (
        <Alert
          className="mb-4"
          type="info"
          showIcon
          message="归因率暂不可用"
          description="确认归因订单与账本订单不是同一访客群组，当前不计算跨口径转化率。"
        />
      )}

      <Row gutter={[16, 16]} className="mb-6">
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="确认归因 GMV（发生口径）"
              value={data.total_gmv}
              prefix="¥"
              loading={loading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="确认归因订单（发生口径）"
              value={data.attributed_orders}
              loading={loading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="账本有效订单（发生口径）"
              value={data.total_orders}
              loading={loading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="归因率" value="—" loading={loading} />
          </Card>
        </Col>
      </Row>

      <Space direction="vertical" size="large" className="w-full">
        <Card title="确认归因日趋势（按扫码收件时间）" size="small">
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
            <Card title="确认归因渠道分布" size="small">
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
            <Card title="确认归因活动分布" size="small">
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
