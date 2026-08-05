"use client";

import { useEffect, useState, useCallback } from "react";
import {
  App,
  Button,
  Card,
  Col,
  DatePicker,
  Progress,
  Row,
  Select,
  Skeleton,
  Space,
  Statistic,
  Table,
  Typography,
} from "antd";
import {
  ScanOutlined,
  UserOutlined,
  RocketOutlined,
  RedoOutlined,
  DownloadOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import api from "@/lib/api";
import ScanTrendChart from "@/components/ScanTrendChart";

const { Title } = Typography;
const { RangePicker } = DatePicker;

interface ScanTrendRow {
  date: string;
  total_scans: number;
  uv: number;
  first_scans: number;
  rescans: number;
}

interface CodeStats {
  total: number;
  by_status: Record<string, number>;
}

interface CodeBatch {
  id: string;
  batch_code: string;
  quantity: number;
  product_name?: string;
}

interface Campaign {
  id: string;
  name: string;
}

export default function CampaignAnalyticsContent({
  restrictedToAnalytics = false,
}: {
  restrictedToAnalytics?: boolean;
}) {
  const { message } = App.useApp();
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(30, "day"),
    dayjs(),
  ]);
  const [trend, setTrend] = useState<ScanTrendRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [codeBatches, setCodeBatches] = useState<CodeBatch[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selectedCampaign, setSelectedCampaign] = useState<string | undefined>(
    undefined
  );
  const [selectedBatch, setSelectedBatch] = useState<string | undefined>(
    undefined
  );
  const [codeStats, setCodeStats] = useState<CodeStats | null>(null);
  const [codeStatsLoading, setCodeStatsLoading] = useState(false);
  const [exporting, setExporting] = useState(false);

  const fetchTrend = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = {
        start_date: dateRange[0].format("YYYY-MM-DD"),
        end_date: dateRange[1].format("YYYY-MM-DD"),
      };
      if (selectedCampaign) {
        params.campaign_id = selectedCampaign;
      }
      const endpoint = selectedCampaign
        ? "/analytics/campaign-scan-stats"
        : "/analytics/scan-stats";
      const { data } = await api.get(endpoint, { params });
      setTrend(Array.isArray(data) ? data : data?.details || []);
    } catch {
      setTrend([]);
    } finally {
      setLoading(false);
    }
  }, [dateRange, selectedCampaign]);

  const fetchCodeBatches = useCallback(async () => {
    try {
      const { data } = await api.get("/code-batches", {
        params: { page: 1, page_size: 100 },
      });
      setCodeBatches(data.items || []);
    } catch {
      /* ignore */
    }
  }, []);

  const fetchCampaigns = useCallback(async () => {
    try {
      const { data } = await api.get("/campaigns", {
        params: { page: 1, page_size: 100 },
      });
      setCampaigns(
        (data.items || []).map((c: Campaign) => ({ id: c.id, name: c.name }))
      );
    } catch {
      /* ignore */
    }
  }, []);

  const fetchCodeStats = useCallback(async (batchId: string) => {
    setCodeStatsLoading(true);
    try {
      const { data } = await api.get("/analytics/code-stats", {
        params: { code_batch_id: batchId },
      });
      setCodeStats(data);
    } catch {
      setCodeStats(null);
    } finally {
      setCodeStatsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTrend();
  }, [fetchTrend]);

  useEffect(() => {
    if (restrictedToAnalytics) return;
    fetchCodeBatches();
    fetchCampaigns();
  }, [fetchCodeBatches, fetchCampaigns, restrictedToAnalytics]);

  useEffect(() => {
    if (selectedBatch) {
      fetchCodeStats(selectedBatch);
    } else {
      setCodeStats(null);
    }
  }, [selectedBatch, fetchCodeStats]);

  const totals = trend.reduce(
    (acc, row) => ({
      total_scans: acc.total_scans + row.total_scans,
      uv: acc.uv + row.uv,
      first_scans: acc.first_scans + row.first_scans,
      rescans: acc.rescans + row.rescans,
    }),
    { total_scans: 0, uv: 0, first_scans: 0, rescans: 0 }
  );

  const handleExport = async () => {
    setExporting(true);
    try {
      const params: Record<string, string> = {
        export_type: "campaign_dashboard",
        format: "xlsx",
      };
      if (dateRange[0] && dateRange[1]) {
        params.start_date = dateRange[0].format("YYYY-MM-DD");
        params.end_date = dateRange[1].format("YYYY-MM-DD");
      }
      const response = await api.post("/analytics/exports", null, {
        params,
        responseType: "blob",
      });
      const blob = new Blob([response.data], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `活动看板_${dateRange[0].format("YYYYMMDD")}-${dateRange[1].format("YYYYMMDD")}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      message.success("导出成功");
    } catch {
      message.error("导出失败，请确认您有管理员权限");
    } finally {
      setExporting(false);
    }
  };

  const trendColumns: ColumnsType<ScanTrendRow> = [
    { title: "日期", dataIndex: "date", key: "date" },
    { title: "扫码数", dataIndex: "total_scans", key: "total_scans" },
    { title: "UV", dataIndex: "uv", key: "uv" },
    { title: "首扫", dataIndex: "first_scans", key: "first_scans" },
    { title: "复扫", dataIndex: "rescans", key: "rescans" },
  ];

  const activatedPercent = codeStats?.total
    ? Math.round(
        ((codeStats.by_status?.activated ?? 0) / codeStats.total) * 100
      )
    : 0;
  const boundPercent = codeStats?.total
    ? Math.round(((codeStats.by_status?.bound ?? 0) / codeStats.total) * 100)
    : 0;

  return (
    <div>
      <Title level={4}>活动看板</Title>

      <div className="mb-4">
        <Space wrap>
          {!restrictedToAnalytics && (
            <Select
              placeholder="全部活动"
              allowClear
              showSearch
              optionFilterProp="label"
              style={{ width: 200 }}
              value={selectedCampaign}
              onChange={(v) => setSelectedCampaign(v)}
              options={campaigns.map((c) => ({
                value: c.id,
                label: c.name,
              }))}
            />
          )}
          <RangePicker
            value={dateRange}
            onChange={(dates) => {
              if (dates && dates[0] && dates[1]) {
                setDateRange([dates[0], dates[1]]);
              }
            }}
          />
          <Button
            icon={<DownloadOutlined />}
            onClick={handleExport}
            loading={exporting}
          >
            导出 Excel
          </Button>
        </Space>
      </div>

      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic
              title="总扫码"
              value={totals.total_scans}
              prefix={<ScanOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic title="UV" value={totals.uv} prefix={<UserOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic
              title="首扫"
              value={totals.first_scans}
              prefix={<RocketOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic
              title="复扫"
              value={totals.rescans}
              prefix={<RedoOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Card title="扫码趋势" className="mb-6" size="small">
        {loading ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : (
          trend.length > 0 && (
            <ScanTrendChart data={trend} height={300} showMulti />
          )
        )}
        <Table
          columns={trendColumns}
          dataSource={trend}
          rowKey="date"
          loading={loading}
          pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 条` }}
          size="small"
        />
      </Card>

      {!restrictedToAnalytics && (
        <Card title="码批次统计" size="small" loading={codeStatsLoading}>
          <div className="mb-4">
            <Select
              placeholder="选择码批次"
              allowClear
              showSearch
              optionFilterProp="label"
              style={{ width: 300 }}
              value={selectedBatch}
              onChange={(v) => setSelectedBatch(v)}
              options={codeBatches.map((b) => ({
                value: b.id,
                label: `${b.batch_code} (${b.quantity} 码)`,
              }))}
            />
          </div>
          {selectedBatch && codeStats && (
            <Row gutter={[24, 16]}>
              <Col xs={24} md={8}>
                <Statistic title="总码数" value={codeStats.total} />
                <Progress percent={100} size="small" className="mt-2" />
              </Col>
              <Col xs={24} md={8}>
                <Statistic
                  title="已激活"
                  value={codeStats.by_status?.activated ?? 0}
                />
                <Progress
                  percent={activatedPercent}
                  size="small"
                  className="mt-2"
                  status={activatedPercent === 100 ? "success" : "active"}
                />
              </Col>
              <Col xs={24} md={8}>
                <Statistic
                  title="已绑定"
                  value={codeStats.by_status?.bound ?? 0}
                />
                <Progress
                  percent={boundPercent}
                  size="small"
                  className="mt-2"
                  status={boundPercent === 100 ? "success" : "active"}
                />
              </Col>
            </Row>
          )}
          {selectedBatch && !codeStats && !codeStatsLoading && (
            <div className="text-text-muted">暂无统计数据</div>
          )}
        </Card>
      )}
    </div>
  );
}
