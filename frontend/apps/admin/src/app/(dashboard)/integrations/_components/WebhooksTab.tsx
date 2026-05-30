"use client";

import React, { useEffect, useState } from "react";
import { Alert, App, Button, Form, Input, Modal, Select, Space, Switch, Table, Tag, Typography, InputNumber, Popconfirm } from "antd";
import { PlusOutlined, CopyOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { EVENT_OPTIONS } from "./constants";

const { Text } = Typography;

export function WebhooksTab() {
  const { message } = App.useApp();
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [secretVisible, setSecretVisible] = useState<string | null>(null);

  const fetch = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/webhooks/endpoints");
      setItems(Array.isArray(data) ? data : []);
    } catch {
      message.error("加载 Webhook 端点失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, []);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      const { data } = await api.post("/webhooks/endpoints", values);
      message.success("Webhook 端点创建成功");
      setSecretVisible(data.secret as string);
      setOpen(false);
      form.resetFields();
      fetch();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "创建失败");
    }
  };

  const handleToggle = async (id: string, enabled: boolean) => {
    try {
      await api.patch(`/webhooks/endpoints/${id}`, { enabled });
      message.success(enabled ? "已启用" : "已禁用");
      fetch();
    } catch { message.error("操作失败"); }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/webhooks/endpoints/${id}`);
      message.success("已删除");
      fetch();
    } catch { message.error("删除失败"); }
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "URL", dataIndex: "url", key: "url", ellipsis: true },
    { title: "事件", dataIndex: "events", key: "events", render: (events: string[]) => events?.map((e) => <Tag key={e}>{e}</Tag>) },
    { title: "描述", dataIndex: "description", key: "description", ellipsis: true },
    { title: "状态", dataIndex: "enabled", key: "enabled", render: (v: boolean, record) => <Switch checked={v} onChange={(checked) => handleToggle(record.id as string, checked)} /> },
    { title: "批量", key: "batch", render: (_, record) => record.batch_mode ? <Tag color="blue">批量 ({String(record.batch_size)})</Tag> : <Tag>逐条</Tag> },
    { title: "操作", key: "actions", render: (_, record) => <Popconfirm title="确认删除？" onConfirm={() => handleDelete(record.id as string)}><Button danger size="small">删除</Button></Popconfirm> },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>新建端点</Button>
      </div>
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading} />
      {secretVisible && (
        <Alert type="success" message="Webhook Secret（仅展示一次，请立即复制）" description={
          <Space><Text code>{secretVisible}</Text>
            <Button size="small" icon={<CopyOutlined />} onClick={() => { navigator.clipboard.writeText(secretVisible); message.success("已复制"); }}>复制</Button>
            <Button size="small" onClick={() => setSecretVisible(null)}>关闭</Button>
          </Space>
        } className="mt-4" />
      )}
      <Modal title="新建 Webhook 端点" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={550}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="url" label="回调 URL" rules={[{ required: true }, { type: "url" }]}>
            <Input placeholder="https://your-server.com/webhook" />
          </Form.Item>
          <Form.Item name="events" label="订阅事件" rules={[{ required: true }]}>
            <Select mode="multiple" options={EVENT_OPTIONS} placeholder="选择要接收的事件" />
          </Form.Item>
          <Form.Item name="description" label="描述"><Input placeholder="可选，方便识别" /></Form.Item>
          <Form.Item name="batch_mode" label="批量模式" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="batch_size" label="批量大小" hidden={false}><InputNumber min={1} max={1000} className="w-full" /></Form.Item>
        </Form>
      </Modal>
    </>
  );
}
