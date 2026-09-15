"use client";

import { useState } from "react";
import {
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
} from "antd";
import {
  ApiOutlined,
  ExperimentOutlined,
  PlusOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import api, { API_BASE_URL, extractErrorMessage } from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";
import type { Connector } from "./types";
import { TYPE_LABELS } from "./types";

interface ConnectorsTabProps {
  connectors: Connector[];
  loading: boolean;
  connectorTypes: string[];
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onRefresh: () => void;
}

export function callbackUrlFor(connectorId: string) {
  return `${API_BASE_URL}/api/v1/connectors/connectors/${connectorId}/callback`;
}

export function ConnectorsTab({
  connectors,
  loading,
  connectorTypes,
  page,
  pageSize,
  total,
  onPageChange,
  onRefresh,
}: ConnectorsTabProps) {
  const { message } = App.useApp();
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Connector | null>(null);
  const [callbackInfo, setCallbackInfo] = useState<Connector | null>(null);
  const [form] = Form.useForm();
  const selectedType = Form.useWatch("connector_type", form);

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
      client_id: (record.config?.client_id as string) || "",
      shop_alias: (record.config?.shop_alias as string) || "",
      shop_id: (record.config?.shop_id as string) || "",
      shop_type: (record.config?.shop_type as string) || "",
      vid: (record.config?.vid as string) || "",
      vid_type: (record.config?.vid_type as string) || "",
    });
    setModalOpen(true);
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      const isYouzan = values.connector_type === "youzan";
      const isWeimob = values.connector_type === "weimob";
      if (editing) {
        const patch: Record<string, unknown> = {
          name: values.name,
          enabled: values.enabled,
        };
        if (editing.connector_type === "youzan") {
          patch.config = {
            ...(editing.config || {}),
            client_id: values.client_id,
            shop_alias: values.shop_alias || "",
          };
          if (values.client_secret) {
            patch.secrets = { client_secret: values.client_secret };
          }
        }
        if (editing.connector_type === "weimob") {
          patch.config = {
            ...(editing.config || {}),
            client_id: values.client_id,
            shop_id: values.shop_id,
            shop_type: values.shop_type,
            vid: values.vid,
            vid_type: values.vid_type,
          };
          if (values.client_secret) {
            patch.secrets = { client_secret: values.client_secret };
          }
        }
        await api.patch(`/connectors/connectors/${editing.id}`, patch);
        message.success("更新成功");
      } else {
        const payload: Record<string, unknown> = {
          name: values.name,
          connector_type: values.connector_type,
          config: {},
        };
        if (isYouzan) {
          payload.config = {
            client_id: values.client_id,
            shop_alias: values.shop_alias || "",
          };
          payload.secrets = { client_secret: values.client_secret };
        }
        if (isWeimob) {
          payload.config = {
            client_id: values.client_id,
            shop_id: values.shop_id,
            shop_type: values.shop_type,
            vid: values.vid,
            vid_type: values.vid_type,
          };
          payload.secrets = { client_secret: values.client_secret };
        }
        const { data } = await api.post<Connector>(
          "/connectors/connectors",
          payload
        );
        message.success("创建成功");
        if ((isYouzan || isWeimob) && data?.id) {
          setCallbackInfo(data);
        }
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
      if (data.success) {
        message.success("连接测试成功");
      } else {
        message.warning(`连接测试: ${data.message}`);
      }
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
          {(record.connector_type === "youzan" ||
            record.connector_type === "weimob") && (
            <Button size="small" onClick={() => setCallbackInfo(record)}>
              <ApiOutlined /> 回调地址
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
        pagination={{
          current: page,
          pageSize,
          total,
          onChange: onPageChange,
          showTotal: (count) => `共 ${count} 个连接器`,
        }}
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
            rules={[
              { required: true, message: "请输入名称" },
              { max: 200, message: "名称最多 200 个字符" },
            ]}
          >
            <Input placeholder="如：有赞优惠券" />
          </Form.Item>
          <Form.Item
            name="connector_type"
            label="连接器类型"
            rules={[{ required: true, message: "请选择类型" }]}
          >
            <Select
              disabled={!!editing}
              placeholder="选择连接器类型"
              virtual={false}
            >
              {connectorTypes.map((t) => (
                <Select.Option key={t} value={t}>
                  {TYPE_LABELS[t] || t}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          {(selectedType === "youzan" ||
            (editing && editing.connector_type === "youzan")) && (
            <>
              <Form.Item
                name="client_id"
                label="有赞应用 client_id"
                rules={[
                  { required: true, message: "请输入 client_id" },
                  { max: 64, message: "client_id 最多 64 个字符" },
                ]}
              >
                <Input placeholder="有赞开放平台自用型应用 client_id" />
              </Form.Item>
              <Form.Item
                name="client_secret"
                label="有赞应用 client_secret"
                rules={
                  editing
                    ? []
                    : [{ required: true, message: "请输入 client_secret" }]
                }
                extra={
                  editing
                    ? "留空表示保持已有密钥不变；密钥加密存储，不回显"
                    : "密钥加密存储，创建后不再回显"
                }
              >
                <Input.Password
                  autoComplete="new-password"
                  placeholder={
                    editing ? "留空保持不变" : "有赞开放平台应用密钥"
                  }
                />
              </Form.Item>
              <Form.Item
                name="shop_alias"
                label="店铺标识（可选）"
                rules={[{ max: 100, message: "最多 100 个字符" }]}
              >
                <Input placeholder="有赞店铺标识，仅用于运营辨识" />
              </Form.Item>
            </>
          )}
          {(selectedType === "weimob" ||
            (editing && editing.connector_type === "weimob")) && (
            <>
              <Form.Item
                name="client_id"
                label="微盟应用 client_id"
                rules={[
                  { required: true, message: "请输入 client_id" },
                  { max: 64, message: "client_id 最多 64 个字符" },
                ]}
              >
                <Input placeholder="微盟开放平台自用型应用 client_id" />
              </Form.Item>
              <Form.Item
                name="client_secret"
                label="微盟应用 client_secret"
                rules={
                  editing
                    ? []
                    : [{ required: true, message: "请输入 client_secret" }]
                }
                extra={
                  editing
                    ? "留空表示保持已有密钥不变；密钥加密存储，不回显"
                    : "密钥加密存储，创建后不再回显"
                }
              >
                <Input.Password
                  autoComplete="new-password"
                  placeholder={
                    editing ? "留空保持不变" : "微盟开放平台应用密钥"
                  }
                />
              </Form.Item>
              <Form.Item
                name="shop_id"
                label="店铺 ID（shop_id）"
                rules={[{ required: true, message: "请输入店铺 ID" }]}
              >
                <Input placeholder="新云 public_account_id 或 WOS business_operation_system_id" />
              </Form.Item>
              <Form.Item
                name="shop_type"
                label="店铺类型（shop_type）"
                rules={[{ required: true, message: "请选择店铺类型" }]}
              >
                <Select
                  virtual={false}
                  placeholder="与店铺 ID 对应的标识口径"
                  options={[
                    {
                      value: "public_account_id",
                      label: "新云 public_account_id",
                    },
                    {
                      value: "business_operation_system_id",
                      label: "WOS business_operation_system_id",
                    },
                  ]}
                />
              </Form.Item>
              <Form.Item
                name="vid"
                label="组织节点 ID（vid）"
                rules={[{ required: true, message: "请输入 vid" }]}
              >
                <Input placeholder="B 端 店铺设置 / 组织架构 节点 ID" />
              </Form.Item>
              <Form.Item
                name="vid_type"
                label="节点类型（vidType）"
                rules={[{ required: true, message: "请输入 vidType" }]}
              >
                <Select
                  virtual={false}
                  placeholder="组织架构节点类型"
                  options={[
                    { value: "1", label: "1 集团" },
                    { value: "2", label: "2 品牌" },
                    { value: "3", label: "3 区域" },
                    { value: "5", label: "5 商场" },
                    { value: "10", label: "10 门店" },
                    { value: "100", label: "100 自提点" },
                  ]}
                />
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>
      <Modal
        title={
          callbackInfo?.connector_type === "weimob"
            ? "微盟消息推送回调地址"
            : "有赞消息推送回调地址"
        }
        open={!!callbackInfo}
        onCancel={() => setCallbackInfo(null)}
        footer={
          <Button type="primary" onClick={() => setCallbackInfo(null)}>
            知道了
          </Button>
        }
      >
        <Typography.Paragraph>
          {callbackInfo?.connector_type === "weimob"
            ? "在微盟云控制台的应用「消息订阅」中，把推送地址配置为："
            : "在有赞云控制台的应用「消息订阅」中，把推送地址配置为："}
        </Typography.Paragraph>
        <Typography.Paragraph
          copyable={{ text: callbackUrlFor(callbackInfo?.id || "") }}
        >
          <Typography.Text code>
            {callbackUrlFor(callbackInfo?.id || "")}
          </Typography.Text>
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary">
          {callbackInfo?.connector_type === "weimob"
            ? "微盟侧领券/核销事件将经此地址回流一码通券钱包。"
            : "有赞侧核销事件将经此地址回流一码通券钱包。"}
        </Typography.Paragraph>
      </Modal>
    </>
  );
}
