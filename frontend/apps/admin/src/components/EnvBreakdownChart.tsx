"use client";

import { Pie } from "@ant-design/charts";

interface EnvBreakdownChartProps {
  data: Record<string, number>;
  height?: number;
}

const ENV_LABELS: Record<string, string> = {
  wechat: "微信",
  alipay: "支付宝",
  browser: "浏览器",
  unknown: "未知",
};

export default function EnvBreakdownChart({ data, height = 250 }: EnvBreakdownChartProps) {
  const chartData = Object.entries(data).map(([key, value]) => ({
    env: ENV_LABELS[key] || key,
    value,
  }));

  if (chartData.length === 0) {
    return null;
  }

  return (
    <Pie
      data={chartData}
      angleField="value"
      colorField="env"
      height={height}
      label={{
        text: (d: { env: string; value: number }) => `${d.env}: ${d.value}`,
        position: "outside" as const,
      }}
      legend={{ color: { position: "bottom" as const } }}
    />
  );
}
