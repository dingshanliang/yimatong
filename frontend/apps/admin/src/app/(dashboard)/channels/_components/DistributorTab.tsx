"use client";

import { useState } from "react";
import { useCrud } from "@/lib/hooks";
import { Button, Form, Input, message, Modal, Table, Tag } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

type Distributor = Record<string, unknown> & { id: string };

const columns: ColumnsType<Distributor> = [
  { title: "名称", dataIndex: "name", key: "name" },
  { title: "编码", dataIndex: "code", key: "code" },
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    render: (s: string) => <Tag color={s === "active" ? "green" : "default"}>{s === "active" ? "启用" : s || "—"}</Tag>,
  },
];

export function DistributorTab() {
  const { items, total, page, loading, setPage, create } = useCrud<Distributor>("/channels/distributors");
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const handleCreate = async (values: Distributor) => {
    try {
      await create(values);
      message.success("经销商创建成功");
      setOpen(false);
      form.resetFields();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "创建失败");
    }
  };

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建经销商
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
      />
      <Modal title="新建经销商" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="code" label="编码" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="contact_name" label="联系人">
            <Input />
          </Form.Item>
          <Form.Item name="contact_phone" label="联系电话">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
