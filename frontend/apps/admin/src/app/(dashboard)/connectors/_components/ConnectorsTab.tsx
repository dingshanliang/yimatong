"use client";

import { useState, useCallback, useEffect } from "react";
import {
  App,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
} from "antd";
import {
  ExperimentOutlined,
  PlusOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";
import type { Connector } from "./types";
import { TYPE_LABELS } from "./types";

interface ConnectorsTabProps {
  connectors: Connector[];
  loading: boolean;
  connectorTypes: string[];
  onRefresh: () => void;
}

export function ConnectorsTab({
  connectors,
  loading,
  connectorTypes,
  onRefresh,
}: ConnectorsTabProps) {
  const { message } = App.useApp();
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Connector | null>(null);
  const [form] = Form.useForm();

  const handleCreate = () => {
    setEditing(null);
    form.resetFields();
    setModalOpen(true);
  };

  const handleEdit = (record: Connector) => {
    setEditing(record);
    form.setFieldsValue({
      name: record.name,
      connector_type: record.connector_type,
      enabled: record.enabled,
    });
    setModalOpen(true);
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      if (editing) {
        await api.patch(`/connectors/connectors/${editing.id}`, {
          name: values.name,
          enabled: values.enabled,
        });
        message.success("更新成功");
      } else {
        await api.post("/connectors/connectors", {
          name: values.name,
          connector_type: values.connector_type,
          config: {},
        });
        message.success("创建成功");
      }
      setModalOpen(false);
      onRefresh();
    } catch (err) {
      const axiosErr = err as Parameters<typeof extractErrorMessage>[0];
      if (axiosErr && typeof axiosErr === "object" && "response" in axiosErr) {
        message.error(extractErrorMessage(axiosErr, "操作失败"));
      }
    }
  };

  const handleTestConnection = async (record: Connector) => {
    try {
      const { data } = await api.post(
        `/connectors/connectors/${record.id}/test`
      );
      data.success
        ? message.success("连接测试成功")
        : message.warning(`连接测试: ${data.message}`);
    } catch (err) {
      message.error(extractErrorMessage(err, "连接测试失败"));
    }
  };

  const handleSyncStock = async (record: Connector) => {
    try {
      const { data } = await api.post(
        `/connectors/connectors/${record.id}/sync-stock`
      );
      message.success(`库存同步成功: 可用 ${data.available}`);
      onRefresh();
    } catch (err) {
      message.error(extractErrorMessage(err, "库存同步失败"));
    }
  };

  const columns = [
    { title: "名称", dataIndex: "name", key: "name" },
    {
      title: "类型",
      dataIndex: "connector_type",
      key: "type",
      render: (type: string) => (
        <Tag color={STATUS_COLORS.processing}>{TYPE_LABELS[type] || type}</Tag>
      ),
    },
    {
      title: "状态",
      dataIndex: "enabled",
      key: "enabled",
      render: (enabled: boolean, record: Connector) => (
        <Switch
          checked={enabled}
          checkedChildren="启用"
          unCheckedChildren="禁用"
          onChange={async () => {
            try {
              await api.patch(`/connectors/connectors/${record.id}`, {
                enabled: !record.enabled,
              });
              message.success(record.enabled ? "已禁用" : "已启用");
              onRefresh();
            } catch (err) {
              message.error(extractErrorMessage(err, "操作失败"));
            }
          }}
        />
      ),
    },
    {
      title: "库存",
      key: "stock",
      render: (_: unknown, record: Connector) => {
        const stock = record.config?.stock as
          { available?: number } | undefined;
        return stock ? `${stock.available}` : "-";
      },
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v: string) => (v ? new Date(v).toLocaleString("zh-CN") : "-"),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Connector) => (
        <Space>
          <Button size="small" onClick={() => handleTestConnection(record)}>
            <ExperimentOutlined /> 测试
          </Button>
          {record.connector_type === "generic_http" && (
            <Button size="small" onClick={() => handleSyncStock(record)}>
              <ReloadOutlined /> 同步库存
            </Button>
          )}
          <Button size="small" onClick={() => handleEdit(record)}>
            编辑
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
          新建连接器
        </Button>
        <Button
          icon={<ReloadOutlined />}
          onClick={onRefresh}
          style={{ marginLeft: 8 }}
        >
          刷新
        </Button>
      </div>
      <Table
        dataSource={connectors}
        columns={columns}
        rowKey="id"
        loading={loading}
        pagination={false}
      />
      <Modal
        title={editing ? "编辑连接器" : "新建连接器"}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        okText="保存"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="连接器名称"
            rules={[{ required: true, message: "请输入名称" }]}
          >
            <Input placeholder="如：微信支付商家券" />
          </Form.Item>
          <Form.Item
            name="connector_type"
            label="连接器类型"
            rules={[{ required: true, message: "请选择类型" }]}
          >
            <Select disabled={!!editing} placeholder="选择连接器类型">
              {connectorTypes.map((t) => (
                <Select.Option key={t} value={t}>
                  {TYPE_LABELS[t] || t}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
