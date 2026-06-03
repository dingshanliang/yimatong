"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Spin, Typography } from "antd";
import api from "@/lib/api";

const { Text } = Typography;

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

  if (!data || data.steps.length === 0 || data.steps[0].value === 0) {
    return (
      <Card title="核心转化漏斗（近 30 天）" size="small">
        <div className="flex items-center justify-center text-gray-400" style={{ height: 120 }}>
          暂无转化数据
        </div>
      </Card>
    );
  }

  const maxValue = data.steps[0].value;

  return (
    <Card title="核心转化漏斗（近 30 天）" size="small">
      <div className="flex items-end gap-2">
        {data.steps.map((step, idx) => {
          const widthPercent = Math.max((step.value / maxValue) * 100, 8);
          return (
            <div
              key={step.name}
              className="flex flex-col items-center"
              style={{ flex: `0 0 ${widthPercent}%`, minWidth: 60 }}
            >
              <div
                style={{
                  background: STEP_COLORS[idx] || "#d9d9d9",
                  height: 80,
                  width: "100%",
                  borderRadius: "4px 4px 0 0",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "white",
                  fontWeight: 600,
                }}
              >
                <span style={{ fontSize: 16 }}>{step.value.toLocaleString()}</span>
              </div>
              <Text strong className="mt-1" style={{ fontSize: 12 }}>
                {step.name}
              </Text>
              <Text type="secondary" style={{ fontSize: 11 }}>
                {step.rate}%
              </Text>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
