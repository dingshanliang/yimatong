"use client";

import { useState, useCallback, useEffect } from "react";
import { App, Button, Table, Tag } from "antd";
import { SendOutlined, ReloadOutlined } from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";
import type { Delivery } from "./types";

export function DeliveriesTab() {
  const { message } = App.useApp();
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchDeliveries = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/connectors/deliveries/pending-retries");
      setDeliveries(data);
    } catch {
      message.error("加载发放记录失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDeliveries();
  }, [fetchDeliveries]);

  const handleRetry = async (deliveryId: string) => {
    try {
      await api.post(`/connectors/deliveries/${deliveryId}/retry`);
      message.success("重试已触发");
      fetchDeliveries();
    } catch (err) {
      message.error(extractErrorMessage(err, "重试失败"));
    }
  };

  const columns = [
    {
      title: "ID",
      dataIndex: "id",
      key: "id",
      render: (v: string) => v.slice(0, 8) + "...",
    },
    { title: "消费者", dataIndex: "consumer_id", key: "consumer" },
    { title: "权益类型", dataIndex: "benefit_type", key: "type" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (status: string) => {
        const colors: Record<string, string> = {
          pending: "#f59e0b",
          success: "#16a34a",
          failed: "#b91c1c",
        };
        return <Tag color={colors[status] || "#8c8c8c"}>{status}</Tag>;
      },
    },
    {
      title: "重试次数",
      key: "retries",
      render: (_: unknown, record: Delivery) =>
        `${record.retry_count}/${record.max_retries}`,
    },
    {
      title: "下次重试",
      dataIndex: "next_retry_at",
      key: "next_retry",
      render: (v: string) => (v ? new Date(v).toLocaleString("zh-CN") : "-"),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Delivery) => (
        <Button
          size="small"
          icon={<SendOutlined />}
          onClick={() => handleRetry(record.id)}
          disabled={record.status !== "pending"}
        >
          重试
        </Button>
      ),
    },
  ];

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <Button icon={<ReloadOutlined />} onClick={fetchDeliveries}>
          刷新
        </Button>
      </div>
      <Table
        dataSource={deliveries}
        columns={columns}
        rowKey="id"
        loading={loading}
        pagination={false}
      />
    </>
  );
}
