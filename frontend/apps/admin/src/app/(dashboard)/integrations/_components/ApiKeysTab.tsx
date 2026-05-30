"use client";

import React, { useEffect, useState } from "react";
import { Alert, App, Button, Form, Input, Modal, Select, Space, Table, Typography, Popconfirm, Tag } from "antd";
import { PlusOutlined, CopyOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { ROLE_OPTIONS } from "./constants";

const { Text } = Typography;

export function ApiKeysTab() {
  const { message } = App.useApp();
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [newKeyVisible, setNewKeyVisible] = useState<string | null>(null);

  const fetch = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/webhooks/api-keys");
      setItems(Array.isArray(data) ? data : []);
    } catch { message.error("加载 API Key 失败"); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetch(); }, []);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      const { data } = await api.post("/webhooks/api-keys", values);
      message.success("API Key 创建成功");
      setNewKeyVisible(data.key as string);
      setOpen(false);
      form.resetFields();
      fetch();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "创建失败");
    }
  };

  const handleRevoke = async (id: string) => {
    try {
      await api.delete(`/webhooks/api-keys/${id}`);
      message.success("已吊销");
      fetch();
    } catch { message.error("吊销失败"); }
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "名称", dataIndex: "name", key: "name" },
    { title: "角色", dataIndex: "role", key: "role", render: (role: string) => <Tag color="blue">{role}</Tag> },
    { title: "Key", key: "key", render: (_, record) => <Text code>{String(record.name).replace(/./g, "*").slice(0, 8)}...</Text> },
    { title: "过期时间", dataIndex: "expires_at", key: "expires_at", render: (v: string | null) => (v ? new Date(v).toLocaleDateString() : "永不过期") },
    { title: "最后使用", dataIndex: "last_used_at", key: "last_used_at", render: (v: string | null) => (v ? new Date(v).toLocaleString() : "从未使用") },
    { title: "操作", key: "actions", render: (_, record) => <Popconfirm title="确认吊销此 Key？" onConfirm={() => handleRevoke(record.id as string)}><Button danger size="small">吊销</Button></Popconfirm> },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>新建 API Key</Button>
      </div>
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading} />
      {newKeyVisible && (
        <Alert type="success" message="API Key（仅展示一次，请立即复制）" description={
          <Space><Text code>{newKeyVisible}</Text>
            <Button size="small" icon={<CopyOutlined />} onClick={() => { navigator.clipboard.writeText(newKeyVisible); message.success("已复制"); }}>复制</Button>
            <Button size="small" onClick={() => setNewKeyVisible(null)}>关闭</Button>
          </Space>
        } className="mt-4" />
      )}
      <Modal title="新建 API Key" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={450}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input placeholder="如：CRM 数据同步" /></Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]} initialValue="data_reader"><Select options={ROLE_OPTIONS} /></Form.Item>
        </Form>
      </Modal>
    </>
  );
}
