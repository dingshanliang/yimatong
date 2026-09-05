"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Empty, message, Spin, Table, Tag } from "antd";
import { useRouter } from "next/navigation";
import type { ColumnsType } from "antd/es/table";
import api, { extractErrorMessage } from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";

interface RankingItem {
  campaign_id: string;
  campaign_name: string;
  campaign_status: string;
  scan_count: null;
  claim_count: number;
  conversion_rate: null;
  conversion_status: "unavailable";
}

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  draft: { label: "草稿", color: STATUS_COLORS.neutral },
  active: { label: "进行中", color: STATUS_COLORS.processing },
  paused: { label: "已暂停", color: STATUS_COLORS.warning },
  ended: { label: "已结束", color: STATUS_COLORS.neutral },
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
    } catch (err) {
      // 加载失败时给出错误提示，保留空态展示以区分“暂无数据”
      message.error(extractErrorMessage(err, "数据加载失败"));
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
        <span
          style={{
            fontWeight: 600,
            color: idx < 3 ? "var(--ymt-color-feedback-info)" : undefined,
          }}
        >
          {idx + 1}
        </span>
      ),
    },
    {
      title: "活动名称",
      dataIndex: "campaign_name",
      key: "name",
      ellipsis: true,
      width: 180,
    },
    {
      title: "状态",
      dataIndex: "campaign_status",
      key: "status",
      width: 80,
      render: (status: string) => {
        const info = STATUS_MAP[status] || {
          label: status,
          color: STATUS_COLORS.neutral,
        };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "确认权益",
      dataIndex: "claim_count",
      key: "claims",
      width: 80,
      render: (claims: number) => (
        <span style={{ fontWeight: 600 }}>{claims}</span>
      ),
    },
  ];

  return (
    <Card
      title="活动确认结果 Top 5"
      size="small"
      style={{ height: "100%" }}
      styles={{ body: { overflow: "hidden" } }}
    >
      {loading ? (
        <div
          className="flex items-center justify-center"
          style={{ height: 200 }}
        >
          <Spin />
        </div>
      ) : items.length === 0 ? (
        <div
          className="flex items-center justify-center"
          style={{ height: 200 }}
        >
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="暂无活动数据"
          />
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
