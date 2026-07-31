"use client";

import { useState } from "react";
import { useCrud } from "@/lib/hooks";
import api from "@/lib/api";
import { Button, Form, Input, Modal, Select, Table, Tag, message } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { STATUS_COLORS } from "@/lib/status-colors";

type Order = Record<string, unknown> & { id: string; matched: boolean };

const columns: ColumnsType<Order> = [
  { title: "外部订单号", dataIndex: "external_id", key: "external_id" },
  {
    title: "金额",
    dataIndex: "amount",
    key: "amount",
    render: (v: number) => `¥${v}`,
  },
  {
    title: "产品",
    dataIndex: "product_name",
    key: "product_name",
    render: (v: string) => v || "—",
  },
  {
    title: "渠道",
    dataIndex: "channel",
    key: "channel",
    render: (v: string) => (v ? <Tag>{v}</Tag> : "—"),
  },
  {
    title: "来源",
    dataIndex: "source_system",
    key: "source_system",
    render: (v: string) => v || "—",
  },
  {
    title: "匹配状态",
    dataIndex: "matched",
    key: "matched",
    render: (v: boolean) => (
      <Tag color={v ? STATUS_COLORS.success : STATUS_COLORS.neutral}>
        {v ? "已归因" : "待匹配"}
      </Tag>
    ),
  },
];

export function OrdersTab() {
  const [filter, setFilter] = useState<Record<string, string>>({});
  const {
    items,
    total,
    page,
    loading,
    setPage,
    mutate,
    setFilter: setCrudFilter,
  } = useCrud<Order>("/gmv/orders");
  const [importOpen, setImportOpen] = useState(false);
  const [form] = Form.useForm();

  const handleImport = async (values: Record<string, unknown>) => {
    try {
      const orders = values.orders_json
        ? JSON.parse(values.orders_json as string)
        : [];
      await api.post("/gmv/orders/import", { orders });
      message.success("导入成功");
      setImportOpen(false);
      form.resetFields();
      mutate();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "导入失败");
    }
  };

  const handleFilterMatched = (value: string | undefined) => {
    const next = { ...filter };
    if (value) {
      next.matched = value;
    } else {
      delete next.matched;
    }
    setFilter(next);
    setCrudFilter(next);
  };

  return (
    <>
      <div className="mb-4 flex items-center justify-between">
        <Select
          placeholder="匹配状态"
          allowClear
          style={{ width: 140 }}
          onChange={handleFilterMatched}
          options={[
            { label: "已归因", value: "true" },
            { label: "待匹配", value: "false" },
          ]}
        />
        <Button
          type="primary"
          icon={<UploadOutlined />}
          onClick={() => setImportOpen(true)}
        >
          导入订单
        </Button>
      </div>
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
      <Modal
        title="导入外部订单"
        open={importOpen}
        onCancel={() => setImportOpen(false)}
        onOk={() => form.submit()}
        width={600}
      >
        <Form form={form} layout="vertical" onFinish={handleImport}>
          <Form.Item
            name="orders_json"
            label="订单数据 (JSON 数组)"
            rules={[{ required: true }]}
          >
            <Input.TextArea
              rows={8}
              placeholder='[{"external_id":"ORD001","amount":99.9,"phone":"13800138000","channel":"taobao","source_system":"erp"}]'
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
