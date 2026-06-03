"use client";

import { Card, DatePicker, Spin } from "antd";
import { Line } from "@ant-design/charts";
import { useCallback, useEffect, useState } from "react";
import dayjs, { type Dayjs } from "dayjs";
import api from "@/lib/api";

interface TrendRow {
  date: string;
  total_scans: number;
  uv?: number;
}

interface DashboardChartsProps {
  initialTrend?: TrendRow[];
  loading?: boolean;
}

export default function DashboardCharts({ initialTrend, loading: parentLoading }: DashboardChartsProps) {
  const [trend, setTrend] = useState<TrendRow[]>(initialTrend || []);
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(29, "day"),
    dayjs(),
  ]);
  const [innerLoading, setInnerLoading] = useState(false);

  const fetchTrend = useCallback(async (range: [Dayjs, Dayjs]) => {
    setInnerLoading(true);
    try {
      const res = await api.get("/analytics/scan-stats", {
        params: {
          start_date: range[0].format("YYYY-MM-DD"),
          end_date: range[1].format("YYYY-MM-DD"),
        },
      });
      setTrend(res.data || []);
    } catch {
      // silent
    } finally {
      setInnerLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!initialTrend || initialTrend.length === 0) {
      void fetchTrend(dateRange);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const chartData = trend.flatMap((row) => [
    { date: row.date, value: row.total_scans, type: "扫码量" },
    { date: row.date, value: row.uv || 0, type: "独立用户" },
  ]);

  const config = {
    data: chartData,
    xField: "date",
    yField: "value",
    colorField: "type",
    height: 280,
    smooth: true,
    point: { shapeField: "square", sizeField: 3 },
    interaction: { tooltip: { marker: false } },
    style: { lineWidth: 2 },
    scale: { color: { range: ["#1677ff", "#52c41a"] } },
    axis: {
      y: { title: "数量" },
      x: { title: false },
    },
  };

  const isLoading = parentLoading || innerLoading;

  return (
    <Card
      title="扫码趋势"
      size="small"
      extra={
        <DatePicker.RangePicker
          size="small"
          value={dateRange}
          onChange={(dates) => {
            if (dates && dates[0] && dates[1]) {
              setDateRange([dates[0], dates[1]]);
              void fetchTrend([dates[0], dates[1]]);
            }
          }}
          disabledDate={(current) => current && current.isAfter(dayjs().endOf("day"))}
        />
      }
    >
      {isLoading ? (
        <div className="flex items-center justify-center" style={{ height: 280 }}>
          <Spin />
        </div>
      ) : chartData.length === 0 ? (
        <div className="flex items-center justify-center text-gray-400" style={{ height: 280 }}>
          暂无趋势数据
        </div>
      ) : (
        <Line {...config} />
      )}
    </Card>
  );
}
