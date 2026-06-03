"use client";

import { useEffect, useState } from "react";
import { App, Button, Card, Col, DatePicker, Empty, Row, Statistic, Table, Typography } from "antd";
import {
  ScanOutlined,
  UserOutlined,
  RocketOutlined,
  RedoOutlined,
  DownloadOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import api, { extractErrorMessage } from "@/lib/api";
import ScanTrendChart from "@/components/ScanTrendChart";

const { Title } = Typography;
const { RangePicker } = DatePicker;

interface ScanStatsRow {
  date: string;
  total_scans: number;
  uv: number;
  first_scans: number;
  rescans: number;
}

export default function StatsPage() {
  const { message } = App.useApp();
  const [data, setData] = useState<ScanStatsRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(7, "day"),
    dayjs(),
  ]);

  useEffect(() => {
    setLoading(true);
    const params: Record<string, string> = {
      start_date: dateRange[0].format("YYYY-MM-DD"),
      end_date: dateRange[1].format("YYYY-MM-DD"),
    };
    api
      .get("/analytics/scan-stats", { params })
      .then((res) => setData(res.data || []))
      .catch((err) => {
        setData([]);
        message.error(extractErrorMessage(err, "加载统计数据失败"));
      })
      .finally(() => setLoading(false));
  }, [dateRange, message]);

  const totals = data.reduce(
    (acc, row) => ({
      total_scans: acc.total_scans + row.total_scans,
      uv: acc.uv + row.uv,
      first_scans: acc.first_scans + row.first_scans,
      rescans: acc.rescans + row.rescans,
    }),
    { total_scans: 0, uv: 0, first_scans: 0, rescans: 0 },
  );

  const handleExport = async () => {
    if (data.length === 0) {
      message.warning("暂无数据可导出");
      return;
    }
    setExporting(true);
    try {
      const params: Record<string, string> = {
        export_type: "scan_stats",
        format: "xlsx",
        start_date: dateRange[0].format("YYYY-MM-DD"),
        end_date: dateRange[1].format("YYYY-MM-DD"),
      };
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
      a.download = `扫码统计_${dateRange[0].format("YYYYMMDD")}-${dateRange[1].format("YYYYMMDD")}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      message.success("导出成功");
    } catch (err) {
      message.error(extractErrorMessage(err, "导出失败，请重试"));
    } finally {
      setExporting(false);
    }
  };

  const disabledDate = (current: Dayjs) => {
    return current && current.isAfter(dayjs().endOf("day"));
  };

  const columns: ColumnsType<ScanStatsRow> = [
    { title: "日期", dataIndex: "date", key: "date" },
    { title: "扫码量", dataIndex: "total_scans", key: "total_scans" },
    { title: "独立用户", dataIndex: "uv", key: "uv" },
    { title: "首扫数", dataIndex: "first_scans", key: "first_scans" },
    { title: "重扫数", dataIndex: "rescans", key: "rescans" },
  ];

  return (
    <div>
      <Title level={4}>扫码统计</Title>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <RangePicker
          value={dateRange}
          onChange={(dates) => {
            if (dates && dates[0] && dates[1]) {
              setDateRange([dates[0], dates[1]]);
            }
          }}
          disabledDate={disabledDate}
        />
        <Button icon={<DownloadOutlined />} onClick={handleExport} loading={exporting}>
          导出 Excel
        </Button>
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
            <Statistic
              title="独立用户"
              value={totals.uv}
              prefix={<UserOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic
              title="首扫数"
              value={totals.first_scans}
              prefix={<RocketOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic
              title="重扫数"
              value={totals.rescans}
              prefix={<RedoOutlined />}
            />
          </Card>
        </Col>
      </Row>
      {data.length > 0 && (
        <Card title="扫码趋势" size="small" className="mb-6">
          <ScanTrendChart data={data} height={300} showMulti />
        </Card>
      )}
      <Table
        columns={columns}
        dataSource={data}
        rowKey="date"
        loading={loading}
        pagination={{ pageSize: 30, showTotal: (t) => `共 ${t} 天` }}
        locale={{
          emptyText: loading ? undefined : (
            <Empty description="该日期范围内暂无扫码数据" />
          ),
        }}
      />
    </div>
  );
}
