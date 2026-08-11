"use client";

import { useState, useCallback, useEffect } from "react";
import { App, Button, Table, Tag } from "antd";
import { SendOutlined, ReloadOutlined } from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";
import type { Delivery } from "./types";

export function DeliveriesTab() {
  const { message } = App.useApp();
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const pageSize = 20;
  const [loading, setLoading] = useState(false);

  const fetchDeliveries = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/connectors/deliveries/pending-retries", {
        params: { page, page_size: pageSize },
      });
      setDeliveries(data.items || []);
      setTotal(data.total || 0);
    } catch {
      message.error("加载发放记录失败");
    } finally {
      setLoading(false);
    }
  }, [message, page]);

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
          pending: STATUS_COLORS.warning,
          success: STATUS_COLORS.success,
          failed: STATUS_COLORS.error,
        };
        return (
          <Tag color={colors[status] || STATUS_COLORS.neutral}>{status}</Tag>
        );
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
        pagination={{
          current: page,
          pageSize,
          total,
          onChange: setPage,
          showTotal: (count) => `共 ${count} 条待处理记录`,
        }}
      />
    </>
  );
}
