"use client";

import { useEffect, useState } from "react";
import { App, Button, Card, Col, DatePicker, Row, Space, Statistic, Table, Typography } from "antd";
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
      .catch(() => setData([]))
      .finally(() => setLoading(false));
  }, [dateRange]);

  const totals = data.reduce(
    (acc, row) => ({
      total_scans: acc.total_scans + row.total_scans,
      uv: acc.uv + row.uv,
      first_scans: acc.first_scans + row.first_scans,
      rescans: acc.rescans + row.rescans,
    }),
    { total_scans: 0, uv: 0, first_scans: 0, rescans: 0 },
  );

  const handleExportCSV = () => {
    if (data.length === 0) {
      message.warning("暂无数据可导出");
      return;
    }
    const header = "日期,扫码量,独立用户,首扫数,重扫数";
    const rows = data.map(
      (r) => `${r.date},${r.total_scans},${r.uv},${r.first_scans},${r.rescans}`
    );
    const csv = [header, ...rows].join("\n");
    const BOM = "﻿";
    const blob = new Blob([BOM + csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `扫码统计_${dateRange[0].format("YYYYMMDD")}-${dateRange[1].format("YYYYMMDD")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    message.success("导出成功");
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
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="总扫码"
              value={totals.total_scans}
              prefix={<ScanOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="独立用户"
              value={totals.uv}
              prefix={<UserOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="首扫数"
              value={totals.first_scans}
              prefix={<RocketOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="重扫数"
              value={totals.rescans}
              prefix={<RedoOutlined />}
            />
          </Card>
        </Col>
      </Row>
      <div className="mb-4">
        <Space>
          <RangePicker
            value={dateRange}
            onChange={(dates) => {
              if (dates && dates[0] && dates[1]) {
                setDateRange([dates[0], dates[1]]);
              }
            }}
          />
          <Button icon={<DownloadOutlined />} onClick={handleExportCSV}>
            导出 CSV
          </Button>
        </Space>
      </div>
      <Table
        columns={columns}
        dataSource={data}
        rowKey="date"
        loading={loading}
        pagination={false}
      />
    </div>
  );
}
