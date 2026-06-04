"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Empty, Spin, Table, Tag } from "antd";
import { useRouter } from "next/navigation";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

interface RankingItem {
  campaign_id: string;
  campaign_name: string;
  campaign_status: string;
  scan_count: number;
  claim_count: number;
  conversion_rate: number;
}

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  draft: { label: "草稿", color: "default" },
  active: { label: "进行中", color: "blue" },
  paused: { label: "已暂停", color: "orange" },
  ended: { label: "已结束", color: "gray" },
};

export default function CampaignRanking() {
  const [items, setItems] = useState<RankingItem[]>([]);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const fetchData = useCallback(async () => {
    try {
      const res = await api.get("/analytics/campaign-ranking", {
        params: { limit: 5 },
      });
      setItems(res.data?.items || []);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const columns: ColumnsType<RankingItem> = [
    {
      title: "排名",
      width: 50,
      render: (_, __, idx) => (
        <span style={{ fontWeight: 600, color: idx < 3 ? "#1677ff" : undefined }}>
          {idx + 1}
        </span>
      ),
    },
    { title: "活动名称", dataIndex: "campaign_name", key: "name", ellipsis: true, width: 180 },
    {
      title: "状态",
      dataIndex: "campaign_status",
      key: "status",
      width: 80,
      render: (status: string) => {
        const info = STATUS_MAP[status] || { label: status, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "转化率",
      dataIndex: "conversion_rate",
      key: "rate",
      width: 80,
      render: (rate: number) => (
        <span style={{ fontWeight: 600, color: rate > 10 ? "#3f8600" : rate > 5 ? "#faad14" : "#cf1322" }}>
          {rate}%
        </span>
      ),
    },
  ];

  return (
    <Card title="活动排行 Top 5" size="small" style={{ height: "100%" }} styles={{ body: { overflow: "hidden" } }}>
      {loading ? (
        <div className="flex items-center justify-center" style={{ height: 200 }}>
          <Spin />
        </div>
      ) : items.length === 0 ? (
        <div className="flex items-center justify-center" style={{ height: 200 }}>
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无活动数据" />
        </div>
      ) : (
        <Table
          columns={columns}
          dataSource={items}
          rowKey="campaign_id"
          pagination={false}
          size="small"
          scroll={{ x: 390 }}
          tableLayout="fixed"
          onRow={(record) => ({
            onClick: () => router.push(`/campaigns/${record.campaign_id}`),
            style: { cursor: "pointer" },
          })}
        />
      )}
    </Card>
  );
}
