"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Card,
  Col,
  Row,
  Statistic,
  Table,
  Typography,
  Tag,
} from "antd";
import {
  ScanOutlined,
  TeamOutlined,
  RocketOutlined,
  RiseOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import api from "@/lib/api";

const { Title } = Typography;

interface DashboardData {
  today_scans: number;
  cumulative_scans: number;
  cumulative_first_scans: number;
  first_scan_rate: number;
}

interface TrendRow {
  date: string;
  total_scans: number;
}

interface CodeBatch {
  id: string;
  batch_code: string;
  quantity: number;
  status: string;
  created_at: string;
}

interface Campaign {
  id: string;
  name: string;
  campaign_type: string;
  status: string;
  start_at: string;
}

const BATCH_STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待生成", color: "default" },
  generating: { label: "生成中", color: "blue" },
  completed: { label: "已完成", color: "green" },
  failed: { label: "失败", color: "red" },
};

const CAMPAIGN_STATUS_MAP: Record<string, { label: string; color: string }> = {
  draft: { label: "草稿", color: "default" },
  active: { label: "进行中", color: "blue" },
  paused: { label: "已暂停", color: "orange" },
  ended: { label: "已结束", color: "gray" },
};

const CAMPAIGN_TYPE_MAP: Record<string, string> = {
  coupon: "优惠券",
  lottery: "抽奖",
  points: "积分",
};

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [trend, setTrend] = useState<TrendRow[]>([]);
  const [trendLoading, setTrendLoading] = useState(false);
  const [batches, setBatches] = useState<CodeBatch[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);

  useEffect(() => {
    api
      .get("/analytics/scan-summary")
      .then((res) => setData(res.data))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  const fetchTrend = useCallback(async () => {
    setTrendLoading(true);
    try {
      const end = dayjs().format("YYYY-MM-DD");
      const start = dayjs().subtract(6, "day").format("YYYY-MM-DD");
      const { data } = await api.get("/analytics/scan-details", {
        params: { start_date: start, end_date: end },
      });
      setTrend(Array.isArray(data) ? data.slice(-7) : []);
    } catch {
      setTrend([]);
    } finally {
      setTrendLoading(false);
    }
  }, []);

  const fetchRecentBatches = useCallback(async () => {
    try {
      const { data } = await api.get("/code-batches", {
        params: { page: 1, page_size: 5 },
      });
      setBatches(data.items || []);
    } catch {
      /* ignore */
    }
  }, []);

  const fetchRecentCampaigns = useCallback(async () => {
    try {
      const { data } = await api.get("/campaigns", {
        params: { page: 1, page_size: 5 },
      });
      setCampaigns(data.items || []);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    fetchTrend();
    fetchRecentBatches();
    fetchRecentCampaigns();
  }, [fetchTrend, fetchRecentBatches, fetchRecentCampaigns]);

  const firstScanRate = data?.cumulative_scans
    ? ((data.cumulative_first_scans / data.cumulative_scans) * 100).toFixed(1)
    : "0";

  const trendColumns: ColumnsType<TrendRow> = [
    { title: "日期", dataIndex: "date", key: "date" },
    { title: "扫码量", dataIndex: "total_scans", key: "total_scans" },
  ];

  const batchColumns: ColumnsType<CodeBatch> = [
    { title: "批次号", dataIndex: "batch_code", key: "batch_code" },
    { title: "数量", dataIndex: "quantity", key: "quantity" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = BATCH_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    { title: "创建时间", dataIndex: "created_at", key: "created_at" },
  ];

  const campaignColumns: ColumnsType<Campaign> = [
    { title: "活动名称", dataIndex: "name", key: "name" },
    {
      title: "类型",
      dataIndex: "campaign_type",
      key: "campaign_type",
      render: (t: string) => CAMPAIGN_TYPE_MAP[t] || t,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = CAMPAIGN_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    { title: "开始时间", dataIndex: "start_at", key: "start_at" },
  ];

  return (
    <div>
      <Title level={4}>工作台</Title>
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic
              title="今日扫码"
              value={data?.today_scans ?? 0}
              prefix={<ScanOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic
              title="累计扫码"
              value={data?.cumulative_scans ?? 0}
              prefix={<RiseOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic
              title="累计首扫"
              value={data?.cumulative_first_scans ?? 0}
              prefix={<RocketOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic
              title="首扫率"
              value={firstScanRate}
              suffix="%"
              prefix={<TeamOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} lg={12}>
          <Card title="最近 7 天扫码趋势" size="small">
            <Table
              columns={trendColumns}
              dataSource={trend}
              rowKey="date"
              loading={trendLoading}
              pagination={false}
              size="small"
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="最近码批次" size="small">
            <Table
              columns={batchColumns}
              dataSource={batches}
              rowKey="id"
              pagination={false}
              size="small"
            />
          </Card>
        </Col>
      </Row>

      <Card title="最近活动" size="small">
        <Table
          columns={campaignColumns}
          dataSource={campaigns}
          rowKey="id"
          pagination={false}
          size="small"
        />
      </Card>
    </div>
  );
}
