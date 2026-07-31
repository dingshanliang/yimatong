"use client";

import { useCrud } from "@/lib/hooks";
import { STATUS_COLORS } from "@/lib/status-colors";
import { Descriptions, Modal, Select, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";

const { Text } = Typography;

type Attribution = Record<string, unknown> & {
  id: string;
  match_type: string;
  amount: number;
  public_id: string | null;
  code_item_id: string | null;
  campaign_id: string | null;
  consumer_id: string | null;
  scan_time: string | null;
  confidence_score: number;
  attribution_window_hours: number;
};

const columns: ColumnsType<Attribution> = [
  {
    title: "码",
    dataIndex: "public_id",
    key: "public_id",
    width: 120,
    render: (v: string) => v || "—",
  },
  {
    title: "金额",
    dataIndex: "amount",
    key: "amount",
    width: 100,
    render: (v: number) => `¥${v.toLocaleString()}`,
  },
  {
    title: "匹配方式",
    dataIndex: "match_type",
    key: "match_type",
    width: 100,
    render: (v: string) => <Tag color={STATUS_COLORS.processing}>{v}</Tag>,
  },
  {
    title: "置信度",
    dataIndex: "confidence_score",
    key: "confidence_score",
    width: 100,
    render: (v: number) => (
      <Text type={v >= 0.8 ? "success" : v >= 0.5 ? "warning" : "danger"}>
        {(v * 100).toFixed(0)}%
      </Text>
    ),
  },
  {
    title: "扫码时间",
    dataIndex: "scan_time",
    key: "scan_time",
    width: 160,
    render: (v: string) => v || "—",
  },
];

export function AttributionsTab() {
  const { items, total, page, loading, setPage, setFilter } =
    useCrud<Attribution>("/gmv/attributions");
  const [detail, setDetail] = useState<Attribution | null>(null);

  const handleMatchTypeFilter = (value: string | undefined) => {
    setFilter(value ? { match_type: value } : {});
  };

  return (
    <>
      <div className="mb-4">
        <Select
          placeholder="匹配方式"
          allowClear
          style={{ width: 140 }}
          onChange={handleMatchTypeFilter}
          options={[{ label: "手机号匹配", value: "phone" }]}
        />
      </div>
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        onRow={(record) => ({
          onClick: () => setDetail(record),
          style: { cursor: "pointer" },
        })}
        pagination={{
          current: page,
          total,
          pageSize: 20,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
      />
      <Modal
        open={!!detail}
        title="归因详情"
        onCancel={() => setDetail(null)}
        footer={null}
        width={600}
      >
        {detail && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="归因 ID">
              {detail.id.slice(0, 12)}...
            </Descriptions.Item>
            <Descriptions.Item label="匹配方式">
              {detail.match_type}
            </Descriptions.Item>
            <Descriptions.Item label="置信度">
              {(detail.confidence_score * 100).toFixed(0)}%
            </Descriptions.Item>
            <Descriptions.Item label="归因窗口">
              {detail.attribution_window_hours} 小时
            </Descriptions.Item>
            <Descriptions.Item label="归因金额">
              ¥{detail.amount.toLocaleString()}
            </Descriptions.Item>
            <Descriptions.Item label="码 public_id">
              {detail.public_id || "—"}
            </Descriptions.Item>
            <Descriptions.Item label="码 ID">
              {detail.code_item_id || "—"}
            </Descriptions.Item>
            <Descriptions.Item label="活动 ID">
              {detail.campaign_id || "—"}
            </Descriptions.Item>
            <Descriptions.Item label="消费者 ID">
              {detail.consumer_id || "—"}
            </Descriptions.Item>
            <Descriptions.Item label="扫码时间">
              {detail.scan_time || "—"}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </>
  );
}
