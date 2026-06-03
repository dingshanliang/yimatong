"use client";

import { useCallback, useEffect, useState } from "react";
import { App, Button, Card, Col, DatePicker, Row, Space, Statistic, Table, Tag, Tooltip, Typography } from "antd";
import {
  DownloadOutlined,
  GiftOutlined,
  LinkOutlined,
  ReloadOutlined,
  RiseOutlined,
  RocketOutlined,
  ScanOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import api, { extractErrorMessage } from "@/lib/api";
import ScanTrendChart from "@/components/ScanTrendChart";
import EnvBreakdownChart from "@/components/EnvBreakdownChart";
import ChartPlaceholder from "@/components/ChartPlaceholder";

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

interface DashboardData {
  today_scans: number;
  cumulative_scans: number;
  cumulative_first_scans: number;
  first_scan_rate: number;
  environment_breakdown?: Record<string, number>;
  trend?: TrendRow[];
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

export default function DashboardHome() {
  const { message } = App.useApp();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [trend, setTrend] = useState<TrendRow[]>([]);
  const [batches, setBatches] = useState<CodeBatch[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [exportRange, setExportRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(6, "day"),
    dayjs(),
  ]);
  const [exporting, setExporting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [comparison, setComparison] = useState<{
    weekly_scans_change: { value: number; direction: string } | null;
    weekly_first_scans_change: { value: number; direction: string } | null;
  } | null>(null);
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  const fetchDashboard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get("/analytics/dashboard");
      setData(res.data);
      if (res.data?.comparison) setComparison(res.data.comparison);
      setTrend(Array.isArray(res.data.trend) ? res.data.trend.slice(-7) : []);
    } catch (err) {
      setData(null);
      setError(extractErrorMessage(err, "加载工作台数据失败"));
      message.error(extractErrorMessage(err, "加载工作台数据失败"));
    } finally {
      setLoading(false);
    }
  }, [message]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      const res = await api.get("/analytics/dashboard", { params: { days_back: "30" } });
      setData(res.data);
      if (res.data.trend) setTrend(res.data.trend);
      message.success("数据已刷新");
    } catch {
      message.error("刷新失败");
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    void fetchDashboard();
  }, [fetchDashboard]);

  const fetchRecentBatches = useCallback(async () => {
    try {
      const { data } = await api.get("/code-batches", {
        params: { page: 1, page_size: 5 },
      });
      setBatches(data.items || []);
    } catch (err) {
      message.error(extractErrorMessage(err, "加载最近批次失败"));
    }
  }, [message]);

  const fetchRecentCampaigns = useCallback(async () => {
    try {
      const { data } = await api.get("/campaigns", {
        params: { page: 1, page_size: 5 },
      });
      setCampaigns(data.items || []);
    } catch (err) {
      message.error(extractErrorMessage(err, "加载最近活动失败"));
    }
  }, [message]);

  useEffect(() => {
    const loadRecent = async () => {
      await Promise.all([fetchRecentBatches(), fetchRecentCampaigns()]);
    };
    void loadRecent();
  }, [fetchRecentBatches, fetchRecentCampaigns]);

  const firstScanRate = data?.cumulative_scans
    ? ((data.cumulative_first_scans / data.cumulative_scans) * 100).toFixed(1)
    : "0";

  const batchColumns: ColumnsType<CodeBatch> = [
    { title: "批次号", dataIndex: "batch_code", key: "batch_code" },
    { title: "数量", dataIndex: "quantity", key: "quantity" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (status: string) => {
        const info = BATCH_STATUS_MAP[status] || { label: status, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (val: string) => (val ? dayjs(val).format("YYYY-MM-DD HH:mm") : "—"),
    },
  ];

  const campaignColumns: ColumnsType<Campaign> = [
    { title: "活动名称", dataIndex: "name", key: "name" },
    {
      title: "类型",
      dataIndex: "campaign_type",
      key: "campaign_type",
      render: (type: string) => CAMPAIGN_TYPE_MAP[type] || type,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (status: string) => {
        const info = CAMPAIGN_STATUS_MAP[status] || { label: status, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "开始时间",
      dataIndex: "start_at",
      key: "start_at",
      render: (val: string) => (val ? dayjs(val).format("YYYY-MM-DD HH:mm") : "—"),
    },
  ];

  const handleExport = async () => {
    setExporting(true);
    try {
      const end = exportRange[1].format("YYYY-MM-DD");
      const start = exportRange[0].format("YYYY-MM-DD");
      const response = await api.post("/analytics/exports", null, {
        params: { export_type: "scan_events", start_date: start, end_date: end },
        responseType: "blob",
      });
      const blob = new Blob([response.data], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `scan-events-${start}-${end}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      message.success("导出成功");
    } catch (err) {
      message.error(extractErrorMessage(err, "导出失败，请确认您有管理员权限"));
    } finally {
      setExporting(false);
    }
  };

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} style={{ marginBottom: 0 }}>工作台</Title>
        <Space>
          <RangePicker
            value={exportRange}
            onChange={(dates) => {
              if (dates && dates[0] && dates[1]) {
                setExportRange([dates[0], dates[1]]);
              }
            }}
            disabledDate={(current) => current && current.isAfter(dayjs().endOf("day"))}
          />
          <Tooltip title="导出扫码数据">
            <Button icon={<DownloadOutlined />} onClick={handleExport} loading={exporting} />
          </Tooltip>
          <Tooltip title="刷新数据">
            <Button icon={<ReloadOutlined />} onClick={() => { void handleRefresh(); }} loading={refreshing} size="small">刷新</Button>
          </Tooltip>
        </Space>
      </div>
      {error && !loading && (
        <Card className="mb-6">
          <div className="flex items-center justify-between">
            <span className="text-red-500">{error}</span>
            <Button size="small" onClick={() => { void fetchDashboard(); }}>重试</Button>
          </div>
        </Card>
      )}

      {!error && !loading && data && data.cumulative_scans === 0 && batches.length === 0 && campaigns.length === 0 && (
        <Card className="mb-6">
          <div className="text-center py-8">
            <Title level={5}>开始使用一码通</Title>
            <p className="text-text-muted mb-4">创建第一个码批次，开始追踪产品扫码数据</p>
            <Space>
              <Button type="primary" icon={<LinkOutlined />} onClick={() => router.push("/batches")}>创建码批次</Button>
              <Button icon={<GiftOutlined />} onClick={() => router.push("/campaigns")}>创建营销活动</Button>
            </Space>
          </div>
        </Card>
      )}

      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading} style={{ borderLeft: "3px solid var(--color-primary)" }}>
            <Statistic title="今日扫码" value={data?.today_scans ?? 0} prefix={<ScanOutlined />} styles={{ value: { color: "var(--color-primary)", fontWeight: 600 } }} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic title="累计扫码" value={data?.cumulative_scans ?? 0} prefix={<RiseOutlined />} />
            {comparison?.weekly_scans_change && (
              <Text type={comparison.weekly_scans_change.direction === "up" ? "success" : "danger"} className="text-xs">
                {comparison.weekly_scans_change.direction === "up" ? "↑" : "↓"} 较上周 {Math.abs(comparison.weekly_scans_change.value)}%
              </Text>
            )}
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic title="累计首扫" value={data?.cumulative_first_scans ?? 0} prefix={<RocketOutlined />} />
            {comparison?.weekly_first_scans_change && (
              <Text type={comparison.weekly_first_scans_change.direction === "up" ? "success" : "danger"} className="text-xs">
                {comparison.weekly_first_scans_change.direction === "up" ? "↑" : "↓"} 较上周 {Math.abs(comparison.weekly_first_scans_change.value)}%
              </Text>
            )}
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic title="首扫率" value={firstScanRate} suffix="%" prefix={<TeamOutlined />} />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} lg={8}>
          <Card title="最近 7 天扫码趋势" size="small" style={{ height: "100%" }}>
            {loading ? (
              <ChartPlaceholder loading height={250} />
            ) : (
              <ScanTrendChart data={trend} height={250} />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="最近码批次" size="small" style={{ height: "100%" }}>
            <div style={{ height: 250, overflow: "auto" }}>
              <Table columns={batchColumns} dataSource={batches} rowKey="id" pagination={false} size="small" />
            </div>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="扫码环境占比" size="small" style={{ height: "100%" }}>
            {data?.environment_breakdown && Object.keys(data.environment_breakdown).length > 0 ? (
              <EnvBreakdownChart data={data.environment_breakdown} height={250} />
            ) : (
              <ChartPlaceholder height={250} emptyText="暂无环境数据" />
            )}
          </Card>
        </Col>
      </Row>

      <Card title="最近活动" size="small" className="mb-2">
        <Table columns={campaignColumns} dataSource={campaigns} rowKey="id" pagination={false} size="small" />
      </Card>
    </div>
  );
}
