"use client";

import { App, Button, Table, Tag } from "antd";
import { CheckOutlined } from "@ant-design/icons";
import { useCrud } from "@/lib/hooks";
import { STATUS_COLORS } from "@/lib/status-colors";
import api from "@/lib/api";
import type { ColumnsType } from "antd/es/table";

type DiversionClue = Record<string, unknown> & {
  id: string;
  public_id: string;
  expected_region: string;
  detected_city: string;
  resolved: boolean;
};

export function DiversionTab() {
  const { items, total, page, loading, setPage, mutate } =
    useCrud<DiversionClue>("/channels/diversion-clues");
  const { message } = App.useApp();

  const handleResolve = async (id: string) => {
    try {
      await api.put(`/risk-dashboard/diversion-clues/${id}/resolve`, {
        resolution_action: "confirmed",
      });
      message.success("已标记为处理");
      mutate();
    } catch {
      message.error("操作失败");
    }
  };

  const columns: ColumnsType<DiversionClue> = [
    { title: "码 ID", dataIndex: "public_id", key: "public_id" },
    {
      title: "预期区域",
      dataIndex: "expected_region",
      key: "expected_region",
      render: (v: string) => v || "—",
    },
    {
      title: "实际城市",
      dataIndex: "detected_city",
      key: "detected_city",
      render: (v: string) => v || "—",
    },
    {
      title: "状态",
      dataIndex: "resolved",
      key: "resolved",
      render: (v: boolean) => (
        <Tag color={v ? STATUS_COLORS.success : STATUS_COLORS.error}>
          {v ? "已处理" : "待处理"}
        </Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 80,
      render: (_: unknown, record: DiversionClue) =>
        !record.resolved && (
          <Button
            size="small"
            type="link"
            icon={<CheckOutlined />}
            onClick={() => handleResolve(record.id)}
          >
            处理
          </Button>
        ),
    },
  ];

  return (
    <Table
      columns={columns}
      dataSource={items}
      rowKey="id"
      loading={loading}
      pagination={{
        current: page,
        total,
        pageSize: 20,
        onChange: setPage,
        showTotal: (t) => `共 ${t} 条`,
      }}
    />
  );
}
