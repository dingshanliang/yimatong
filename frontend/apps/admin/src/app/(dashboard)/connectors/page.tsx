"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Table, Button, Modal, Form, Input, Select, Switch, Tag, Space, App,
  Popconfirm, Card, Statistic, Row, Col, Tabs, Progress,
} from "antd";
import {
  PlusOutlined, ReloadOutlined, ExperimentOutlined,
  SendOutlined, EyeOutlined,
} from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";
import { usePaginatedList } from "@/lib/hooks";

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

interface Pool {
  id: string;
  name: string;
  total_codes: number;
  remaining: number;
}

interface PoolCode {
  id: string;
  code: string;
  consumer_id: string | null;
  distributed: boolean;
}

const TYPE_LABELS: Record<string, string> = {
  generic_http: "通用 HTTP",
  coupon_pool: "券码池",
  youzan: "有赞",
  wechat_pay: "微信支付商家券",
  alipay: "支付宝商家券",
};

export default function ConnectorsPage() {
  const { message } = App.useApp();
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [loading, setLoading] = useState(false);
  const [deliveryLoading, setDeliveryLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Connector | null>(null);
  const [form] = Form.useForm();
  const [connectorTypes, setConnectorTypes] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState("connectors");

  // Coupon pool state
  const [poolModalOpen, setPoolModalOpen] = useState(false);
  const [poolForm] = Form.useForm();
  const [codeModalOpen, setCodeModalOpen] = useState(false);
  const [selectedPool, setSelectedPool] = useState<Pool | null>(null);

  const {
    items: pools, loading: poolsLoading, refresh: refreshPools,
  } = usePaginatedList<Pool>(
    async () => {
      try {
        const { data } = await api.get("/connectors/coupon-pools");
        return { items: data.items || [], total: data.total || 0 };
      } catch {
        message.error("加载券码池失败");
        return { items: [], total: 0 };
      }
    },
    []
  );

  const {
    items: poolCodes, total: poolCodesTotal, loading: poolCodesLoading,
    page: poolCodesPage, setPage: setPoolCodesPage,
  } = usePaginatedList<PoolCode>(
    async ({ page, page_size }) => {
      if (!selectedPool) return { items: [], total: 0 };
      try {
        const { data } = await api.get(`/connectors/coupon-pools/${selectedPool.id}/codes`, {
          params: { page, page_size },
        });
        return { items: data.items || [], total: data.total || 0 };
      } catch {
        return { items: [], total: 0 };
      }
    },
    [selectedPool]
  );

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
      const axiosErr = err as Parameters<typeof extractErrorMessage>[0];
      if (axiosErr && typeof axiosErr === "object" && "response" in axiosErr) {
        message.error(extractErrorMessage(axiosErr, "操作失败"));
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

  // Coupon pool handlers
  const handleCreatePool = async () => {
    try {
      const values = await poolForm.validateFields();
      const codes = (values.codes as string)
        .split("\n")
        .map((c: string) => c.trim())
        .filter(Boolean);
      if (codes.length === 0) {
        message.error("请输入至少一个券码");
        return;
      }
      await api.post("/connectors/coupon-pools", {
        name: values.name,
        codes,
      });
      message.success(`券码池创建成功，共 ${codes.length} 个券码`);
      setPoolModalOpen(false);
      poolForm.resetFields();
      refreshPools();
    } catch (err) {
      const axiosErr = err as Parameters<typeof extractErrorMessage>[0];
      if (axiosErr && typeof axiosErr === "object" && "response" in axiosErr) {
        message.error(extractErrorMessage(axiosErr, "创建失败"));
      }
    }
  };

  const openCodeViewer = (pool: Pool) => {
    setSelectedPool(pool);
    setCodeModalOpen(true);
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
    { title: "权益类型", dataIndex: "benefit_type", key: "type" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (status: string) => {
        const colors: Record<string, string> = {
          pending: "orange", success: "green", failed: "red",
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

  const poolColumns = [
    { title: "池名称", dataIndex: "name", key: "name" },
    {
      title: "总数",
      dataIndex: "total_codes",
      key: "total_codes",
    },
    {
      title: "剩余",
      key: "remaining",
      render: (_: unknown, record: Pool) => {
        const used = record.total_codes - record.remaining;
        const percent = record.total_codes > 0
          ? Math.round((used / record.total_codes) * 100)
          : 0;
        return (
          <div className="min-w-[100px]">
            <div className="mb-1 text-xs text-gray-500">
              {record.remaining} / {record.total_codes}
            </div>
            <Progress
              percent={percent}
              size="small"
              status={record.remaining === 0 ? "exception" : undefined}
            />
          </div>
        );
      },
    },
    {
      title: "状态",
      key: "status",
      render: (_: unknown, record: Pool) => (
        <Tag color={record.remaining > 0 ? "green" : "red"}>
          {record.remaining > 0 ? "有库存" : "已耗尽"}
        </Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Pool) => (
        <Button
          size="small"
          icon={<EyeOutlined />}
          onClick={() => openCodeViewer(record)}
        >
          查看码
        </Button>
      ),
    },
  ];

  const poolCodeColumns = [
    { title: "券码", dataIndex: "code", key: "code" },
    {
      title: "消费者",
      dataIndex: "consumer_id",
      key: "consumer_id",
      render: (v: string | null) => v || "-",
    },
    {
      title: "已分配",
      dataIndex: "distributed",
      key: "distributed",
      render: (v: boolean) => (
        <Tag color={v ? "blue" : "default"}>{v ? "是" : "否"}</Tag>
      ),
    },
  ];

  const tabItems = [
    {
      key: "connectors",
      label: "连接器管理",
      children: (
        <>
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
        </>
      ),
    },
    {
      key: "coupon-pools",
      label: "券码池",
      children: (
        <>
          <div style={{ marginBottom: 16 }}>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => {
                poolForm.resetFields();
                setPoolModalOpen(true);
              }}
            >
              创建券码池
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => refreshPools()} style={{ marginLeft: 8 }}>
              刷新
            </Button>
          </div>
          <Table
            dataSource={pools}
            columns={poolColumns}
            rowKey="id"
            loading={poolsLoading}
            pagination={false}
          />
        </>
      ),
    },
    {
      key: "deliveries",
      label: "发放记录",
      children: (
        <>
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
        </>
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
            <Statistic
              title="待重试发放"
              value={pendingCount}
              valueStyle={{ color: pendingCount > 0 ? "#cf1322" : undefined }}
            />
          </Card>
        </Col>
      </Row>

      <Tabs activeKey={activeTab} onChange={handleTabChange} items={tabItems} />

      {/* 连接器创建/编辑 Modal */}
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
          {editing && (
            <Form.Item name="enabled" label="启用状态" valuePropName="checked">
              <Switch />
            </Form.Item>
          )}
        </Form>
      </Modal>

      {/* 券码池创建 Modal */}
      <Modal
        title="创建券码池"
        open={poolModalOpen}
        onOk={handleCreatePool}
        onCancel={() => setPoolModalOpen(false)}
        okText="创建"
      >
        <Form form={poolForm} layout="vertical">
          <Form.Item
            name="name"
            label="池名称"
            rules={[{ required: true, message: "请输入池名称" }]}
          >
            <Input placeholder="如：2026年6月优惠券" />
          </Form.Item>
          <Form.Item
            name="codes"
            label="券码列表"
            rules={[{ required: true, message: "请输入券码" }]}
            extra="每行一个券码"
          >
            <Input.TextArea
              rows={10}
              placeholder={"COUPON001\nCOUPON002\nCOUPON003"}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* 券码查看 Modal */}
      <Modal
        title={selectedPool ? `券码池：${selectedPool.name}` : "券码明细"}
        open={codeModalOpen}
        onCancel={() => {
          setCodeModalOpen(false);
          setSelectedPool(null);
        }}
        footer={null}
        width={700}
      >
        <Table
          dataSource={poolCodes}
          columns={poolCodeColumns}
          rowKey="id"
          loading={poolCodesLoading}
          size="small"
          pagination={{
            current: poolCodesPage,
            total: poolCodesTotal,
            pageSize: 50,
            onChange: setPoolCodesPage,
            showTotal: (t) => `共 ${t} 条`,
          }}
        />
      </Modal>
    </div>
  );
}
