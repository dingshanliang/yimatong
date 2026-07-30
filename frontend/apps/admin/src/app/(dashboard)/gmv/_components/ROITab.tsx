"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import {
  OrderConversionRateHeader,
  OrderConversionRateTitle,
} from "../../_components/MetricHeaders";
import {
  Button,
  Card,
  DatePicker,
  Row,
  Col,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Dayjs } from "dayjs";

const { Text } = Typography;

type ROIItem = {
  campaign_id: string;
  campaign_name: string;
  status: string;
  budget: number | null;
  attributed_gmv: number;
  attributed_orders: number;
  scan_count: number;
  scan_uv: number;
  scan_cost: number;
  conversion_rate: number;
  roi: number;
  avg_confidence: number;
};

const columns: ColumnsType<ROIItem> = [
  {
    title: "活动名称",
    dataIndex: "campaign_name",
    key: "campaign_name",
    width: 160,
  },
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    width: 80,
    render: (v: string) => {
      const map: Record<string, string> = {
        active: "#16a34a",
        draft: "#8c8c8c",
        paused: "#f59e0b",
        ended: "#b91c1c",
      };
      return <Tag color={map[v] || "#8c8c8c"}>{v}</Tag>;
    },
  },
  {
    title: "预算",
    dataIndex: "budget",
    key: "budget",
    width: 100,
    render: (v: number | null) => (v ? `¥${v.toLocaleString()}` : "—"),
  },
  {
    title: "归因 GMV",
    dataIndex: "attributed_gmv",
    key: "attributed_gmv",
    width: 110,
    render: (v: number) => <Text strong>¥{v.toLocaleString()}</Text>,
  },
  {
    title: "归因订单",
    dataIndex: "attributed_orders",
    key: "attributed_orders",
    width: 90,
  },
  { title: "扫码次数", dataIndex: "scan_count", key: "scan_count", width: 90 },
  { title: "扫码 UV", dataIndex: "scan_uv", key: "scan_uv", width: 80 },
  {
    title: "扫码成本",
    dataIndex: "scan_cost",
    key: "scan_cost",
    width: 100,
    render: (v: number) => (v ? `¥${v.toFixed(2)}` : "—"),
  },
  {
    title: <OrderConversionRateHeader />,
    dataIndex: "conversion_rate",
    key: "conversion_rate",
    width: 90,
    render: (v: number) => (
      <Text type={v >= 5 ? "success" : v >= 1 ? "warning" : "danger"}>
        {v}%
      </Text>
    ),
  },
  {
    title: "ROI",
    dataIndex: "roi",
    key: "roi",
    width: 80,
    render: (v: number) => {
      const color = v >= 3 ? "success" : v >= 1 ? "warning" : "danger";
      return (
        <Text type={color} strong>
          {v}x
        </Text>
      );
    },
  },
  {
    title: "置信度",
    dataIndex: "avg_confidence",
    key: "avg_confidence",
    width: 80,
    render: (v: number) => (
      <Text type={v >= 0.8 ? "success" : "warning"}>
        {(v * 100).toFixed(0)}%
      </Text>
    ),
  },
];

export function ROITab() {
  const [data, setData] = useState<ROIItem[]>([]);
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
      const { data: d } = await api.get(`/gmv/roi?${params}`);
      setData(Array.isArray(d) ? d : []);
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetch();
  }, [dateRange]);

  // 汇总统计
  const totalGmv = data.reduce((s, r) => s + r.attributed_gmv, 0);
  const totalOrders = data.reduce((s, r) => s + r.attributed_orders, 0);
  const avgRoi = data.length
    ? data.reduce((s, r) => s + r.roi, 0) / data.length
    : 0;
  const avgConversion = data.length
    ? data.reduce((s, r) => s + r.conversion_rate, 0) / data.length
    : 0;

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

      <Row gutter={[16, 16]} className="mb-4">
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="总归因 GMV"
              value={totalGmv}
              prefix="¥"
              loading={loading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="总归因订单"
              value={totalOrders}
              loading={loading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="平均 ROI"
              value={avgRoi}
              suffix="x"
              precision={2}
              loading={loading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title={<OrderConversionRateTitle />}
              value={avgConversion}
              suffix="%"
              precision={2}
              loading={loading}
            />
          </Card>
        </Col>
      </Row>

      <Table
        columns={columns}
        dataSource={data}
        rowKey="campaign_id"
        loading={loading}
        size="small"
        pagination={false}
        scroll={{ x: 1200 }}
      />
    </div>
  );
}
