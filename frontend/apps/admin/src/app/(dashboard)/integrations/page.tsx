"use client";

import React, { useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  InputNumber,
  Popconfirm,
} from "antd";
import { PlusOutlined, CopyOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title, Text } = Typography;

const EVENT_OPTIONS = [
  { value: "scan.created", label: "扫码事件" },
  { value: "claim.created", label: "领券事件" },
  { value: "claim.used", label: "核销事件" },
  { value: "claim.expired", label: "过期事件" },
  { value: "consumer.created", label: "新消费者" },
  { value: "consumer.profile_updated", label: "信息变更" },
  { value: "risk.alert", label: "风险告警" },
  { value: "campaign.started", label: "活动开始" },
  { value: "campaign.ended", label: "活动结束" },
];

const ROLE_OPTIONS = [
  { value: "data_reader", label: "数据只读 (data_reader)" },
  { value: "coupon_operator", label: "券操作 (coupon_operator)" },
  { value: "webhook_admin", label: "Webhook 管理 (webhook_admin)" },
  { value: "full_access", label: "完全访问 (full_access)" },
];

const STATUS_COLORS: Record<string, string> = {
  delivered: "green",
  pending: "blue",
  retrying: "orange",
  failed: "red",
};

/* ---------- Webhook Endpoints Tab ---------- */

function WebhooksTab() {
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

  useEffect(() => {
    fetch();
  }, []);

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
    } catch {
      message.error("操作失败");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/webhooks/endpoints/${id}`);
      message.success("已删除");
      fetch();
    } catch {
      message.error("删除失败");
    }
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "URL", dataIndex: "url", key: "url", ellipsis: true },
    {
      title: "事件",
      dataIndex: "events",
      key: "events",
      render: (events: string[]) => events?.map((e) => <Tag key={e}>{e}</Tag>),
    },
    { title: "描述", dataIndex: "description", key: "description", ellipsis: true },
    {
      title: "状态",
      dataIndex: "enabled",
      key: "enabled",
      render: (v: boolean, record) => (
        <Switch checked={v} onChange={(checked) => handleToggle(record.id as string, checked)} />
      ),
    },
    {
      title: "批量",
      key: "batch",
      render: (_, record) =>
        record.batch_mode ? (
          <Tag color="blue">批量 ({String(record.batch_size)})</Tag>
        ) : (
          <Tag>逐条</Tag>
        ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_, record) => (
        <Popconfirm title="确认删除？" onConfirm={() => handleDelete(record.id as string)}>
          <Button danger size="small">
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建端点
        </Button>
      </div>
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading} />

      {secretVisible && (
        <Alert
          type="success"
          message="Webhook Secret（仅展示一次，请立即复制）"
          description={
            <Space>
              <Text code>{secretVisible}</Text>
              <Button
                size="small"
                icon={<CopyOutlined />}
                onClick={() => {
                  navigator.clipboard.writeText(secretVisible);
                  message.success("已复制");
                }}
              >
                复制
              </Button>
              <Button size="small" onClick={() => setSecretVisible(null)}>
                关闭
              </Button>
            </Space>
          }
          className="mt-4"
        />
      )}

      <Modal title="新建 Webhook 端点" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={550}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="url" label="回调 URL" rules={[{ required: true }, { type: "url" }]}>
            <Input placeholder="https://your-server.com/webhook" />
          </Form.Item>
          <Form.Item name="events" label="订阅事件" rules={[{ required: true }]}>
            <Select mode="multiple" options={EVENT_OPTIONS} placeholder="选择要接收的事件" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input placeholder="可选，方便识别" />
          </Form.Item>
          <Form.Item name="batch_mode" label="批量模式" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="batch_size" label="批量大小" hidden={false}>
            <InputNumber min={1} max={1000} className="w-full" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

/* ---------- API Keys Tab ---------- */

function ApiKeysTab() {
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
    } catch {
      message.error("加载 API Key 失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetch();
  }, []);

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
    } catch {
      message.error("吊销失败");
    }
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "名称", dataIndex: "name", key: "name" },
    {
      title: "角色",
      dataIndex: "role",
      key: "role",
      render: (role: string) => <Tag color="blue">{role}</Tag>,
    },
    {
      title: "Key",
      key: "key",
      render: (_, record) => <Text code>{String(record.name).replace(/./g, "*").slice(0, 8)}...</Text>,
    },
    {
      title: "过期时间",
      dataIndex: "expires_at",
      key: "expires_at",
      render: (v: string | null) => (v ? new Date(v).toLocaleDateString() : "永不过期"),
    },
    {
      title: "最后使用",
      dataIndex: "last_used_at",
      key: "last_used_at",
      render: (v: string | null) => (v ? new Date(v).toLocaleString() : "从未使用"),
    },
    {
      title: "操作",
      key: "actions",
      render: (_, record) => (
        <Popconfirm title="确认吊销此 Key？" onConfirm={() => handleRevoke(record.id as string)}>
          <Button danger size="small">
            吊销
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建 API Key
        </Button>
      </div>
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading} />

      {newKeyVisible && (
        <Alert
          type="success"
          message="API Key（仅展示一次，请立即复制）"
          description={
            <Space>
              <Text code>{newKeyVisible}</Text>
              <Button
                size="small"
                icon={<CopyOutlined />}
                onClick={() => {
                  navigator.clipboard.writeText(newKeyVisible);
                  message.success("已复制");
                }}
              >
                复制
              </Button>
              <Button size="small" onClick={() => setNewKeyVisible(null)}>
                关闭
              </Button>
            </Space>
          }
          className="mt-4"
        />
      )}

      <Modal title="新建 API Key" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={450}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="如：CRM 数据同步" />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]} initialValue="data_reader">
            <Select options={ROLE_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

/* ---------- Deliveries Tab ---------- */

function DeliveriesTab() {
  const { message } = App.useApp();
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();

  const fetch = async (p: number = 1, status?: string) => {
    setLoading(true);
    try {
      const { data } = await api.get("/webhooks/deliveries", {
        params: { page: p, page_size: 20, status },
      });
      setItems(data.items || []);
      setTotal(data.total || 0);
      setPage(p);
    } catch {
      message.error("加载投递记录失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetch(1, statusFilter);
  }, [statusFilter]);

  const columns: ColumnsType<Record<string, unknown>> = [
    {
      title: "事件类型",
      dataIndex: "event_type",
      key: "event_type",
      render: (t: string) => <Tag>{t}</Tag>,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => <Tag color={STATUS_COLORS[s] || "default"}>{s}</Tag>,
    },
    {
      title: "重试次数",
      dataIndex: "retry_count",
      key: "retry_count",
    },
    {
      title: "HTTP 状态",
      dataIndex: "last_response_code",
      key: "last_response_code",
      render: (v: number | null) => (v ? String(v) : "-"),
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v: string) => (v ? new Date(v).toLocaleString() : "-"),
    },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Space>
          <Text>状态筛选：</Text>
          <Select
            allowClear
            placeholder="全部"
            style={{ width: 150 }}
            value={statusFilter}
            onChange={(v) => setStatusFilter(v)}
            options={[
              { value: "delivered", label: "已送达" },
              { value: "pending", label: "待发送" },
              { value: "retrying", label: "重试中" },
              { value: "failed", label: "失败" },
            ]}
          />
        </Space>
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
          onChange: (p) => fetch(p, statusFilter),
          showTotal: (t) => `共 ${t} 条`,
        }}
      />
    </>
  );
}

/* ---------- Main ---------- */

const tabItems = [
  { key: "webhooks", label: "Webhook 端点", children: <WebhooksTab /> },
  { key: "api-keys", label: "API Key", children: <ApiKeysTab /> },
  { key: "deliveries", label: "投递记录", children: <DeliveriesTab /> },
];

export default function IntegrationsPage() {
  return (
    <div>
      <Title level={4} className="!mb-4">
        集成管理
      </Title>
      <Tabs defaultActiveKey="webhooks" items={tabItems} />
    </div>
  );
}
