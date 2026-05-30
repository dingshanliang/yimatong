"use client";

import React, { useEffect, useState } from "react";
import { App, Button, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { STATUS_COLORS } from "./constants";

const { Text } = Typography;

export function DeliveriesTab() {
  const { message } = App.useApp();
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();

  const fetch = async (p: number = 1, status?: string) => {
    setLoading(true);
    try {
      const { data } = await api.get("/webhooks/deliveries", { params: { page: p, page_size: 20, status } });
      setItems(data.items || []);
      setTotal(data.total || 0);
      setPage(p);
    } catch { message.error("加载投递记录失败"); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetch(1, statusFilter); }, [statusFilter]);

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "事件类型", dataIndex: "event_type", key: "event_type", render: (t: string) => <Tag>{t}</Tag> },
    { title: "状态", dataIndex: "status", key: "status", render: (s: string) => <Tag color={STATUS_COLORS[s] || "default"}>{s}</Tag> },
    { title: "重试次数", dataIndex: "retry_count", key: "retry_count" },
    { title: "HTTP 状态", dataIndex: "last_response_code", key: "last_response_code", render: (v: number | null) => (v ? String(v) : "-") },
    { title: "创建时间", dataIndex: "created_at", key: "created_at", render: (v: string) => (v ? new Date(v).toLocaleString() : "-") },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Space>
          <Text>状态筛选：</Text>
          <Select allowClear placeholder="全部" style={{ width: 150 }} value={statusFilter} onChange={(v) => setStatusFilter(v)}
            options={[
              { value: "delivered", label: "已送达" },
              { value: "pending", label: "待发送" },
              { value: "retrying", label: "重试中" },
              { value: "failed", label: "失败" },
            ]}
          />
        </Space>
      </div>
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading}
        pagination={{ current: page, total, pageSize: 20, onChange: (p) => fetch(p, statusFilter), showTotal: (t) => `共 ${t} 条` }}
      />
    </>
  );
}
