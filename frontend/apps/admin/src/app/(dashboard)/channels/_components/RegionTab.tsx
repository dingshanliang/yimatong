"use client";

import { useState } from "react";
import { useCrud } from "@/lib/hooks";
import { Button, Form, Input, message, Modal, Table } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

type Region = Record<string, unknown> & { id: string };

const columns: ColumnsType<Region> = [
  { title: "名称", dataIndex: "name", key: "name" },
  { title: "编码", dataIndex: "code", key: "code" },
  { title: "城市", dataIndex: "city", key: "city" },
];

export function RegionTab() {
  const { items, total, page, loading, setPage, create } = useCrud<Region>("/channels/regions");
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const handleCreate = async (values: Region) => {
    try {
      await create(values);
      message.success("区域创建成功");
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
          新建区域
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
      />
      <Modal title="新建区域" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="code" label="编码" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="province" label="省份">
            <Input />
          </Form.Item>
          <Form.Item name="city" label="城市">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
