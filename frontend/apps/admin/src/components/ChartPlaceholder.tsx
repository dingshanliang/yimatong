"use client";

import { InboxOutlined } from "@ant-design/icons";
import { Spin } from "antd";

interface ChartPlaceholderProps {
  height?: number;
  loading?: boolean;
  emptyText?: string;
}

export default function ChartPlaceholder({
  height = 250,
  loading = false,
  emptyText = "暂无数据",
}: ChartPlaceholderProps) {
  return (
    <div
      style={{ height }}
      className="flex items-center justify-center text-text-muted"
    >
      {loading ? (
        <Spin />
      ) : (
        <div className="text-center">
          <InboxOutlined style={{ fontSize: 32, marginBottom: 8 }} />
          <div>{emptyText}</div>
        </div>
      )}
    </div>
  );
}
