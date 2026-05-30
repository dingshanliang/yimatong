"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Table,
  Button,
  Modal,
  Form,
  Input,
  Select,
  Switch,
  Tag,
  Space,
  message,
  Popconfirm,
  Card,
  Statistic,
  Row,
  Col,
  Tabs,
} from "antd";
import {
  PlusOutlined,
  ReloadOutlined,
  ApiOutlined,
  ExperimentOutlined,
  SendOutlined,
} from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";

interface Connector {
  id: string;
  name: string;
  connector_type: string;
  config: Record<string, unknown>;
  enabled: boolean;
  secrets?: Record<string, string>;
  created_at: string;
  updated_at: string;
}

interface Delivery {
  id: string;
  connector_id: string;
  consumer_id: string;
  benefit_type: string;
  status: string;
  retry_count: number;
  max_retries: number;
  external_data: Record<string, unknown> | null;
  next_retry_at: string | null;
}

const TYPE_LABELS: Record<string, string> = {
  generic_http: "通用 HTTP",
  coupon_pool: "券码池",
  youzan: "有赞",
  wechat_pay: "微信支付商家券",
  alipay: "支付宝商家券",
};

export default function ConnectorsPage() {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [loading, setLoading] = useState(false);
  const [deliveryLoading, setDeliveryLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Connector | null>(null);
  const [form] = Form.useForm();
  const [connectorTypes, setConnectorTypes] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState("connectors");

  const fetchConnectors = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/connectors/connectors");
      setConnectors(data);
    } catch {
      message.error("加载连接器失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchDeliveries = useCallback(async () => {
    setDeliveryLoading(true);
    try {
      const { data } = await api.get("/connectors/deliveries/pending-retries");
      setDeliveries(data);
    } catch {
      message.error("加载发放记录失败");
    } finally {
      setDeliveryLoading(false);
    }
  }, []);

  const fetchTypes = useCallback(async () => {
    try {
      const { data } = await api.get("/connectors/connectors/types");
      setConnectorTypes(data.types || []);
    } catch {
      // fallback to known types
      setConnectorTypes(["generic_http", "coupon_pool"]);
    }
  }, []);

  useEffect(() => {
    fetchConnectors();
    fetchTypes();
  }, [fetchConnectors, fetchTypes]);

  const handleTabChange = (key: string) => {
    setActiveTab(key);
    if (key === "deliveries") {
      fetchDeliveries();
    }
  };

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
      fetchConnectors();
    } catch (err) {
      if (err instanceof Error || (err as { errorFields?: unknown }).errorFields) {
        const axiosErr = err as Parameters<typeof extractErrorMessage>[0];
        if (axiosErr && typeof axiosErr === "object" && "response" in axiosErr) {
          message.error(extractErrorMessage(axiosErr, "操作失败"));
        }
      }
    }
  };

  const handleToggleEnabled = async (record: Connector) => {
    try {
      await api.patch(`/connectors/connectors/${record.id}`, {
        enabled: !record.enabled,
      });
      message.success(record.enabled ? "已禁用" : "已启用");
      fetchConnectors();
    } catch (err) {
      message.error(extractErrorMessage(err, "操作失败"));
    }
  };

  const handleTestConnection = async (record: Connector) => {
    try {
      const { data } = await api.post(`/connectors/connectors/${record.id}/test`);
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
      const { data } = await api.post(`/connectors/connectors/${record.id}/sync-stock`);
      message.success(`库存同步成功: 可用 ${data.available}`);
      fetchConnectors();
    } catch (err) {
      message.error(extractErrorMessage(err, "库存同步失败"));
    }
  };

  const handleRetryDelivery = async (deliveryId: string) => {
    try {
      await api.post(`/connectors/deliveries/${deliveryId}/retry`);
      message.success("重试已触发");
      fetchDeliveries();
    } catch (err) {
      message.error(extractErrorMessage(err, "重试失败"));
    }
  };

  const enabledCount = connectors.filter((c) => c.enabled).length;
  const pendingCount = deliveries.length;

  const connectorColumns = [
    { title: "名称", dataIndex: "name", key: "name" },
    {
      title: "类型",
      dataIndex: "connector_type",
      key: "type",
      render: (type: string) => (
        <Tag color="blue">{TYPE_LABELS[type] || type}</Tag>
      ),
    },
    {
      title: "状态",
      dataIndex: "enabled",
      key: "enabled",
      render: (enabled: boolean) => (
        <Tag color={enabled ? "green" : "default"}>
          {enabled ? "启用" : "禁用"}
        </Tag>
      ),
    },
    {
      title: "库存",
      key: "stock",
      render: (_: unknown, record: Connector) => {
        const stock = record.config?.stock as { available?: number } | undefined;
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
          <Popconfirm
            title={record.enabled ? "确认禁用？" : "确认启用？"}
            onConfirm={() => handleToggleEnabled(record)}
          >
            <Button size="small" danger={record.enabled}>
              {record.enabled ? "禁用" : "启用"}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const deliveryColumns = [
    { title: "ID", dataIndex: "id", key: "id", render: (v: string) => v.slice(0, 8) + "..." },
    { title: "消费者", dataIndex: "consumer_id", key: "consumer" },
    {
      title: "权益类型",
      dataIndex: "benefit_type",
      key: "type",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (status: string) => {
        const colors: Record<string, string> = {
          pending: "orange",
          success: "green",
          failed: "red",
        };
        return <Tag color={colors[status] || "default"}>{status}</Tag>;
      },
    },
    {
      title: "重试次数",
      key: "retries",
      render: (_: unknown, record: Delivery) => `${record.retry_count}/${record.max_retries}`,
    },
    {
      title: "下次重试",
      dataIndex: "next_retry_at",
      key: "next_retry",
      render: (v: string) => (v ? new Date(v).toLocaleString("zh-CN") : "-"),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Delivery) => (
        <Button
          size="small"
          icon={<SendOutlined />}
          onClick={() => handleRetryDelivery(record.id)}
          disabled={record.status !== "pending"}
        >
          重试
        </Button>
      ),
    },
  ];

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Card>
            <Statistic title="连接器总数" value={connectors.length} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title="已启用" value={enabledCount} valueStyle={{ color: "#3f8600" }} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title="待重试发放" value={pendingCount} valueStyle={{ color: pendingCount > 0 ? "#cf1322" : undefined }} />
          </Card>
        </Col>
      </Row>

      <Tabs activeKey={activeTab} onChange={handleTabChange}>
        <Tabs.TabPane tab="连接器管理" key="connectors">
          <div style={{ marginBottom: 16 }}>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
              新建连接器
            </Button>
            <Button icon={<ReloadOutlined />} onClick={fetchConnectors} style={{ marginLeft: 8 }}>
              刷新
            </Button>
          </div>
          <Table
            dataSource={connectors}
            columns={connectorColumns}
            rowKey="id"
            loading={loading}
            pagination={false}
          />
        </Tabs.TabPane>

        <Tabs.TabPane tab="发放记录" key="deliveries">
          <div style={{ marginBottom: 16 }}>
            <Button icon={<ReloadOutlined />} onClick={fetchDeliveries}>
              刷新
            </Button>
          </div>
          <Table
            dataSource={deliveries}
            columns={deliveryColumns}
            rowKey="id"
            loading={deliveryLoading}
            pagination={false}
          />
        </Tabs.TabPane>
      </Tabs>

      <Modal
        title={editing ? "编辑连接器" : "新建连接器"}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        okText="保存"
        destroyOnClose
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
          {editing && (
            <Form.Item name="enabled" label="启用状态" valuePropName="checked">
              <Switch />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  );
}
