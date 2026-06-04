"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Empty, Progress, Spin, Typography } from "antd";
import api from "@/lib/api";

const { Text, Title } = Typography;

interface FunnelStep {
  name: string;
  value: number;
  rate: number;
}

interface FunnelData {
  steps: FunnelStep[];
  period_days: number;
}

const STEP_COLORS = ["#1677ff", "#52c41a", "#faad14", "#fa8c16", "#f5222d"];

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
      <Card title="核心转化漏斗（近 30 天）" size="small">
        <div className="flex items-center justify-center" style={{ height: 120 }}>
          <Spin />
        </div>
      </Card>
    );
  }

  const steps = Array.isArray(data?.steps) ? data.steps : [];

  if (steps.length === 0 || steps[0].value === 0) {
    return (
      <Card title="核心转化漏斗（近 30 天）" size="small">
        <div className="flex items-center justify-center" style={{ minHeight: 148 }}>
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无转化数据" />
        </div>
      </Card>
    );
  }

  const maxValue = Math.max(steps[0].value, 1);

  return (
    <Card title="核心转化漏斗（近 30 天）" size="small">
      <div
        className="grid gap-3"
        style={{
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
        }}
      >
        {steps.map((step, idx) => {
          const progressPercent = Math.max((step.value / maxValue) * 100, step.value > 0 ? 6 : 0);
          return (
            <div
              key={step.name}
              className="rounded-md border px-3 py-3"
              style={{
                minWidth: 0,
                background: "var(--ant-color-fill-quaternary)",
                borderColor: "var(--ant-color-border-secondary)",
              }}
            >
              <div className="mb-2 flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {idx + 1}. {step.name}
                  </Text>
                  <Title level={4} className="!mb-0 !mt-1">
                    {step.value.toLocaleString()}
                  </Title>
                </div>
                <Text strong style={{ color: STEP_COLORS[idx] || "#1677ff", whiteSpace: "nowrap" }}>
                  {step.rate}%
                </Text>
              </div>
              <Progress
                percent={progressPercent}
                showInfo={false}
                strokeColor={STEP_COLORS[idx] || "#1677ff"}
                railColor="var(--ant-color-fill-secondary)"
                size={["100%", 8]}
              />
            </div>
          );
        })}
      </div>
    </Card>
  );
}
