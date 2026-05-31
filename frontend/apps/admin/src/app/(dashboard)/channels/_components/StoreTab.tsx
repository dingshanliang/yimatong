"use client";

import { useEffect, useState } from "react";
import { useCrud } from "@/lib/hooks";
import api from "@/lib/api";
import { Button, Form, Input, message, Modal, Select, Space, Table, Tag } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

type Store = Record<string, unknown> & { id: string; name: string; code: string; status: string; region_id?: string; distributor_id?: string };
type Option = { label: string; value: string };

const columns: ColumnsType<Store> = [
  { title: "名称", dataIndex: "name", key: "name" },
  { title: "编码", dataIndex: "code", key: "code" },
  { title: "地址", dataIndex: "address", key: "address", render: (v: string) => v || "—" },
  {
    title: "状态", dataIndex: "status", key: "status", width: 80,
    render: (v: string) => <Tag color={v === "active" ? "green" : "default"}>{v === "active" ? "活跃" : v}</Tag>,
  },
];

export function StoreTab() {
  const { items, total, page, loading, setPage, create, update, mutate } = useCrud<Store>("/channels/stores");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Store | null>(null);
  const [form] = Form.useForm();

  // 加载区域和经销商选项
  const [regionOptions, setRegionOptions] = useState<Option[]>([]);
  const [distOptions, setDistOptions] = useState<Option[]>([]);

  useEffect(() => {
    api.get("/channels/regions?page_size=100").then(({ data }) => {
      const items = data?.items || data || [];
      setRegionOptions(items.map((r: Record<string, unknown>) => ({ label: String(r.name), value: String(r.id) })));
    }).catch(() => {});
    api.get("/channels/distributors?page_size=100").then(({ data }) => {
      const items = data?.items || data || [];
      setDistOptions(items.map((d: Record<string, unknown>) => ({ label: String(d.name), value: String(d.id) })));
    }).catch(() => {});
  }, []);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      await create(values);
      message.success("门店创建成功");
      setOpen(false);
      form.resetFields();
    } catch {
      message.error("创建失败");
    }
  };

  const handleUpdate = async (values: Record<string, unknown>) => {
    if (!editing) return;
    try {
      await update(editing.id, values);
      message.success("门店更新成功");
      setEditing(null);
      form.resetFields();
    } catch {
      message.error("更新失败");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/channels/stores/${id}`);
      message.success("门店已删除");
      mutate();
    } catch {
      message.error("删除失败");
    }
  };

  const openEdit = (record: Store) => {
    setEditing(record);
    form.setFieldsValue(record);
  };

  const actionColumns: ColumnsType<Store> = [
    ...columns,
    {
      title: "操作", key: "actions", width: 140,
      render: (_: unknown, record: Store) => (
        <Space size="small">
          <Button size="small" type="link" onClick={() => openEdit(record)}>编辑</Button>
          <Button size="small" type="link" danger onClick={() => handleDelete(record.id)}>删除</Button>
        </Space>
      ),
    },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); setOpen(true); }}>
          新建门店
        </Button>
      </div>
      <Table
        columns={actionColumns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
      />

      {/* 新建 */}
      <Modal title="新建门店" open={open && !editing} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="code" label="编码" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="region_id" label="所属区域">
            <Select placeholder="选择区域" allowClear options={regionOptions} />
          </Form.Item>
          <Form.Item name="distributor_id" label="所属经销商">
            <Select placeholder="选择经销商" allowClear options={distOptions} />
          </Form.Item>
          <Form.Item name="address" label="地址">
            <Input />
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑 */}
      <Modal title="编辑门店" open={!!editing} onCancel={() => { setEditing(null); form.resetFields(); }} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleUpdate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="region_id" label="所属区域">
            <Select placeholder="选择区域" allowClear options={regionOptions} />
          </Form.Item>
          <Form.Item name="distributor_id" label="所属经销商">
            <Select placeholder="选择经销商" allowClear options={distOptions} />
          </Form.Item>
          <Form.Item name="address" label="地址">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
