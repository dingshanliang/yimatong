"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, Card, Empty, Spin, Tag, Typography } from "antd";
import api from "@/lib/api";

const { Text, Title } = Typography;

interface FunnelStep {
  name: string;
  value: number;
  unit: "count" | "yuan";
  rate: null;
}

interface VisitorCohort {
  status: "collecting" | "complete";
  visitors: number;
  collecting_visitors: number;
  confirmed_order_visitors: number;
  confirmed_order_visitor_rate: number | null;
}

interface FunnelData {
  steps: FunnelStep[];
  period_days: number;
  report_type: "result_occurrence";
  order_data_quality: "complete" | "incomplete";
  quarantined_order_count: number;
  unattributed_order_count: number;
  visitor_cohort: VisitorCohort;
}

export default function ConversionFunnel() {
  const [data, setData] = useState<FunnelData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const res = await api.get("/analytics/conversion-funnel", {
        params: { days_back: 30 },
      });
      setData(res.data);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <Card title="近 30 天结果发生概览" size="small">
        <div
          className="flex items-center justify-center"
          style={{ height: 120 }}
        >
          <Spin />
        </div>
      </Card>
    );
  }

  const steps = Array.isArray(data?.steps) ? data.steps : [];

  if (steps.length === 0 || steps.every((step) => step.value === 0)) {
    return (
      <Card
        title="近 30 天结果发生概览"
        size="small"
        data-testid="conversion-summary"
      >
        <div
          className="flex items-center justify-center"
          style={{ minHeight: 148 }}
        >
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="暂无转化数据"
          />
        </div>
      </Card>
    );
  }

  const cohort = data?.visitor_cohort;

  return (
    <Card
      title="近 30 天结果发生概览"
      size="small"
      data-testid="conversion-summary"
      extra={<Tag color="blue">按结果发生时间</Tag>}
    >
      <Text type="secondary" className="mb-3 block text-xs">
        各指标单位不同，不共用一个转化率。访客群组结果在归因窗口结束后才会定稿。
      </Text>
      {data?.order_data_quality === "incomplete" ? (
        <Alert
          className="mb-3"
          type="warning"
          showIcon
          message={`订单归因待完善：${data.unattributed_order_count} 笔未归因，${data.quarantined_order_count} 笔历史数据待核验`}
        />
      ) : null}
      <div
        className="grid gap-3"
        style={{
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
        }}
      >
        {steps.map((step, idx) => (
          <div
            key={step.name}
            className="rounded-md border px-3 py-3"
            style={{
              minWidth: 0,
              background: "var(--ant-color-fill-quaternary)",
              borderColor: "var(--ant-color-border-secondary)",
            }}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {idx + 1}. {step.name}
                </Text>
                <Title level={4} className="!mb-0 !mt-1">
                  {step.unit === "yuan"
                    ? `¥${step.value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                    : step.value.toLocaleString()}
                </Title>
              </div>
            </div>
          </div>
        ))}
      </div>
      {cohort ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <Tag color={cohort.status === "collecting" ? "gold" : "green"}>
            访客群组：{cohort.status === "collecting" ? "归因收集中" : "已定稿"}
          </Tag>
          <Text type="secondary">
            {cohort.visitors.toLocaleString()} 位访客；已确认订单访客{" "}
            {cohort.confirmed_order_visitors.toLocaleString()} 位
            {cohort.confirmed_order_visitor_rate === null
              ? "（窗口结束后计算转化率）"
              : `（${cohort.confirmed_order_visitor_rate}%）`}
          </Text>
        </div>
      ) : null}
    </Card>
  );
}
