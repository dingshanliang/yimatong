"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";
import {
  Alert,
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
  scan_count: null;
  scan_uv: null;
  scan_cost: null;
  conversion_rate: null;
  conversion_rate_status: "unavailable_missing_campaign_eligible_cohort";
  roi: number | null;
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
        active: STATUS_COLORS.success,
        draft: STATUS_COLORS.neutral,
        paused: STATUS_COLORS.warning,
        ended: STATUS_COLORS.error,
      };
      return <Tag color={map[v] || STATUS_COLORS.neutral}>{v}</Tag>;
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
    title: "确认归因 GMV（发生口径）",
    dataIndex: "attributed_gmv",
    key: "attributed_gmv",
    width: 110,
    render: (v: number) => <Text strong>¥{v.toLocaleString()}</Text>,
  },
  {
    title: "确认归因订单（发生口径）",
    dataIndex: "attributed_orders",
    key: "attributed_orders",
    width: 90,
  },
  {
    title: "ROI",
    dataIndex: "roi",
    key: "roi",
    width: 80,
    render: (v: number | null) => {
      // 无预算时后端返回 roi=null，此时 ROI 无法计算，展示占位而非红色 0x
      if (v === null || v === undefined) {
        return <Text type="secondary">—</Text>;
      }
      const color = v >= 3 ? "success" : v >= 1 ? "warning" : "danger";
      return (
        <Text type={color} strong>
          {v}x
        </Text>
      );
    },
  },
];

export function ROITab() {
  const [data, setData] = useState<ROIItem[]>([]);
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
      const { data: d } = await api.get(`/gmv/roi?${params}`);
      setData(Array.isArray(d) ? d : []);
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

  // 汇总统计
  const totalGmv = data.reduce((s, r) => s + r.attributed_gmv, 0);
  const totalOrders = data.reduce((s, r) => s + r.attributed_orders, 0);
  // 平均 ROI 只统计可计算的行（无预算的 roi=null 跳过）
  const roiValues = data
    .map((r) => r.roi)
    .filter((v): v is number => v !== null && v !== undefined);
  const avgRoi = roiValues.length
    ? roiValues.reduce((s, v) => s + v, 0) / roiValues.length
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

      {error && (
        <Alert
          className="mb-4"
          type="error"
          showIcon
          message="ROI 报表加载失败"
          description="数据暂时无法加载，这不代表当前没有数据。"
          action={
            <Button size="small" onClick={() => void fetch()}>
              重试
            </Button>
          }
        />
      )}

      <Alert
        className="mb-4"
        type="info"
        showIcon
        message="活动转化率暂不可用"
        description="当前仅展示已确认归因的活动发生金额和订单数；缺少活动级完整可归因访客群组，因此不展示扫码分母、扫码成本或转化率。"
      />

      <Row gutter={[16, 16]} className="mb-4">
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="确认归因 GMV 合计（发生口径）"
              value={totalGmv}
              prefix="¥"
              loading={loading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="确认归因订单合计（发生口径）"
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
            <Statistic title="活动转化率" value="—" loading={loading} />
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
        scroll={{ x: 760 }}
      />
    </div>
  );
}
