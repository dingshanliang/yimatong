"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Select, Spin, Table } from "antd";
import { useRouter } from "next/navigation";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

interface HealthScore {
  name: string;
  health_score: number;
  repeat_rate: number;
  anomaly_rate: number;
}

export default function ChannelHealth() {
  const [scores, setScores] = useState<HealthScore[]>([]);
  const [loading, setLoading] = useState(true);
  const [dimension, setDimension] = useState<string>("distributor");
  const router = useRouter();

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get("/channel-analytics/health-scores", {
        params: { dimension, days_back: 30 },
      });
      const raw = res.data?.scores || [];
      setScores(raw.slice(0, 5));
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [dimension]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const columns: ColumnsType<HealthScore> = [
    { title: "渠道名称", dataIndex: "name", key: "name", ellipsis: true },
    {
      title: "健康评分",
      dataIndex: "health_score",
      key: "score",
      width: 90,
      render: (score: number) => {
        const color = score >= 80 ? "#3f8600" : score >= 60 ? "#faad14" : "#cf1322";
        return <span style={{ fontWeight: 600, color }}>{score}</span>;
      },
    },
    {
      title: "异常率",
      dataIndex: "anomaly_rate",
      key: "anomaly",
      width: 80,
      render: (rate: number) => (
        <span style={{ color: rate > 0.1 ? "#cf1322" : undefined }}>
          {(rate * 100).toFixed(1)}%
        </span>
      ),
    },
  ];

  return (
    <Card
      title={
        <div className="flex items-center justify-between">
          <span>渠道健康 Top 5</span>
          <Select
            size="small"
            value={dimension}
            onChange={setDimension}
            style={{ width: 100 }}
            options={[
              { value: "distributor", label: "经销商" },
              { value: "region", label: "区域" },
              { value: "store", label: "门店" },
            ]}
          />
        </div>
      }
      size="small"
      style={{ height: "100%" }}
    >
      {loading ? (
        <div className="flex items-center justify-center" style={{ height: 200 }}>
          <Spin />
        </div>
      ) : scores.length === 0 ? (
        <div className="flex items-center justify-center text-gray-400" style={{ height: 200 }}>
          暂无渠道数据
        </div>
      ) : (
        <Table
          columns={columns}
          dataSource={scores}
          rowKey="name"
          pagination={false}
          size="small"
          onRow={() => ({
            onClick: () => router.push("/risk-center"),
            style: { cursor: "pointer" },
          })}
        />
      )}
    </Card>
  );
}
