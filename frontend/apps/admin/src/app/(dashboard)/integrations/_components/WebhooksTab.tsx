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
  Tag,
  Typography,
  InputNumber,
  Popconfirm,
} from "antd";
import { PlusOutlined, CopyOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api, { extractErrorMessage } from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";
import { EVENT_OPTIONS } from "./constants";

const { Text } = Typography;

const WEBHOOK_CONFLICT_MESSAGE = "配置已被他人更新，已刷新请重试";

interface ApiErrorShape {
  response?: {
    status?: number;
    data?: { detail?: unknown };
  };
}

interface WebhookEndpoint {
  id: string;
  url: string;
  events: string[];
  description: string | null;
  enabled: boolean;
  config_version: number;
  batch_mode: boolean;
  batch_size: number | null;
}

function isVersionConflict(error: unknown): boolean {
  return (error as ApiErrorShape)?.response?.status === 409;
}

export function WebhooksTab() {
  const { message } = App.useApp();
  const [items, setItems] = useState<WebhookEndpoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [secretVisible, setSecretVisible] = useState<string | null>(null);
  const batchMode = Form.useWatch("batch_mode", form) ?? false;

  const fetch = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/webhooks/endpoints");
      setItems(Array.isArray(data) ? data : []);
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, "加载 Webhook 端点失败"));
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
      message.error(extractErrorMessage(e, "创建失败"));
    }
  };

  const handleToggle = async (record: WebhookEndpoint, enabled: boolean) => {
    try {
      await api.patch(
        `/webhooks/endpoints/${record.id}`,
        { enabled },
        {
          headers: { "If-Match": String(record.config_version) },
        }
      );
      message.success(enabled ? "已启用" : "已禁用");
      fetch();
    } catch (e: unknown) {
      if (isVersionConflict(e)) {
        message.warning(WEBHOOK_CONFLICT_MESSAGE);
        fetch();
      } else {
        message.error(extractErrorMessage(e, "操作失败"));
      }
    }
  };

  const handleDelete = async (record: WebhookEndpoint) => {
    try {
      await api.delete(`/webhooks/endpoints/${record.id}`, {
        headers: { "If-Match": String(record.config_version) },
      });
      message.success("已删除");
      fetch();
    } catch (e: unknown) {
      if (isVersionConflict(e)) {
        message.warning(WEBHOOK_CONFLICT_MESSAGE);
        fetch();
      } else {
        message.error(extractErrorMessage(e, "删除失败"));
      }
    }
  };

  const columns: ColumnsType<WebhookEndpoint> = [
    { title: "URL", dataIndex: "url", key: "url", ellipsis: true },
    {
      title: "事件",
      dataIndex: "events",
      key: "events",
      render: (events: string[]) => events?.map((e) => <Tag key={e}>{e}</Tag>),
    },
    {
      title: "描述",
      dataIndex: "description",
      key: "description",
      ellipsis: true,
    },
    {
      title: "状态",
      dataIndex: "enabled",
      key: "enabled",
      render: (v: boolean, record) => (
        <Switch
          checked={v}
          onChange={(checked) => handleToggle(record, checked)}
        />
      ),
    },
    {
      title: "批量",
      key: "batch",
      render: (_, record) =>
        record.batch_mode ? (
          <Tag color={STATUS_COLORS.processing}>
            批量 ({String(record.batch_size)})
          </Tag>
        ) : (
          <Tag>逐条</Tag>
        ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_, record) => (
        <Popconfirm
          title="确认删除？"
          okText="确认"
          cancelText="取消"
          onConfirm={() => handleDelete(record)}
        >
          <Button danger size="small">
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  const copySecret = async () => {
    if (!secretVisible) return;
    try {
      await navigator.clipboard.writeText(secretVisible);
      message.success("已复制");
    } catch {
      message.warning("浏览器未允许复制，请手动选择密钥复制");
    }
  };

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setOpen(true)}
        >
          新建端点
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
      />
      {secretVisible && (
        <Alert
          type="success"
          title="Webhook Secret（仅展示一次，请立即复制）"
          description={
            <Space>
              <Text code>{secretVisible}</Text>
              <Button size="small" icon={<CopyOutlined />} onClick={copySecret}>
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
      <Modal
        title="新建 Webhook 端点"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        okText="确定"
        cancelText="取消"
        width={550}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item
            name="url"
            label="回调 URL"
            rules={[{ required: true }, { type: "url" }]}
          >
            <Input placeholder="https://your-server.com/webhook" />
          </Form.Item>
          <Form.Item
            name="events"
            label="订阅事件"
            rules={[{ required: true }]}
          >
            <Select
              mode="multiple"
              options={EVENT_OPTIONS}
              placeholder="选择要接收的事件"
            />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input placeholder="可选，方便识别" />
          </Form.Item>
          <Form.Item name="batch_mode" label="批量模式" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item
            name="batch_size"
            label="批量大小"
            hidden={!batchMode}
            rules={[{ required: batchMode }]}
          >
            <InputNumber min={1} max={1000} className="w-full" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
