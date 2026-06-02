"use client";

import { useCallback, useEffect, useState } from "react";
import { App, Button, Card, Col, DatePicker, Row, Space, Statistic, Table, Tag, Typography } from "antd";
import {
  DownloadOutlined,
  RiseOutlined,
  RocketOutlined,
  ScanOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import api, { extractErrorMessage } from "@/lib/api";
import ScanTrendChart from "@/components/ScanTrendChart";
import EnvBreakdownChart from "@/components/EnvBreakdownChart";

const { Title } = Typography;
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

  useEffect(() => {
    api
      .get("/analytics/dashboard")
      .then((res) => {
        setData(res.data);
        setTrend(Array.isArray(res.data.trend) ? res.data.trend.slice(-7) : []);
      })
      .catch((err) => {
        setData(null);
        message.error(extractErrorMessage(err, "加载工作台数据失败"));
      })
      .finally(() => setLoading(false));
  }, [message]);

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
    { title: "创建时间", dataIndex: "created_at", key: "created_at" },
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
    { title: "开始时间", dataIndex: "start_at", key: "start_at" },
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
        <Title level={4} className="!mb-0">工作台</Title>
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
          <Button icon={<DownloadOutlined />} onClick={handleExport} loading={exporting}>
            导出扫码数据
          </Button>
        </Space>
      </div>
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic title="今日扫码" value={data?.today_scans ?? 0} prefix={<ScanOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic title="累计扫码" value={data?.cumulative_scans ?? 0} prefix={<RiseOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic title="累计首扫" value={data?.cumulative_first_scans ?? 0} prefix={<RocketOutlined />} />
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
          <Card title="最近 7 天扫码趋势" size="small">
            {loading ? (
              <div style={{ height: 250 }} className="flex items-center justify-center text-gray-400">
                加载中...
              </div>
            ) : (
              <ScanTrendChart data={trend} height={250} />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="最近码批次" size="small">
            <Table columns={batchColumns} dataSource={batches} rowKey="id" pagination={false} size="small" />
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="扫码环境占比" size="small">
            {data?.environment_breakdown && Object.keys(data.environment_breakdown).length > 0 ? (
              <EnvBreakdownChart data={data.environment_breakdown} height={250} />
            ) : (
              <div style={{ height: 250 }} className="flex items-center justify-center text-gray-400">
                暂无环境数据
              </div>
            )}
          </Card>
        </Col>
      </Row>

      <Card title="最近活动" size="small">
        <Table columns={campaignColumns} dataSource={campaigns} rowKey="id" pagination={false} size="small" />
      </Card>
    </div>
  );
}
