"use client";

import { Line } from "@ant-design/charts";

interface TrendRow {
  date: string;
  total_scans: number;
  uv?: number;
  first_scans?: number;
}

interface ScanTrendChartProps {
  data: TrendRow[];
  height?: number;
  showMulti?: boolean;
}

export default function ScanTrendChart({ data, height = 250, showMulti = false }: ScanTrendChartProps) {
  if (!data || data.length === 0) {
    return null;
  }

  if (showMulti) {
    const multiData = data.flatMap((row) => [
      { date: row.date, value: row.total_scans, metric: "扫码量" },
      { date: row.date, value: row.uv ?? 0, metric: "UV" },
      { date: row.date, value: row.first_scans ?? 0, metric: "首扫" },
    ]);

    return (
      <Line
        data={multiData}
        xField="date"
        yField="value"
        colorField="metric"
        height={height}
        style={{ lineWidth: 2 }}
        axis={{
          x: { title: "日期" },
          y: { title: "数量" },
        }}
      />
    );
  }

  return (
    <Line
      data={data}
      xField="date"
      yField="total_scans"
      height={height}
      style={{ lineWidth: 2 }}
      axis={{
        x: { title: "日期" },
        y: { title: "扫码量" },
      }}
    />
  );
}
