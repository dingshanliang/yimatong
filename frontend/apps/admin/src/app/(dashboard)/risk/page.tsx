"use client";

import { useEffect, useState } from "react";
import { usePaginatedList } from "@/lib/hooks";
import { App, Button, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tabs, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

const RULE_TYPES = [
  { value: "frequency", label: "频率限制" },
  { value: "ip_diversity", label: "IP 多样性" },
  { value: "geo_anomaly", label: "地域异常" },
  { value: "budget", label: "预算控制" },
  { value: "time_window", label: "时间窗口" },
];

const ACTIONS = [
  { value: "block", label: "拦截" },
  { value: "warn", label: "预警" },
  { value: "flag", label: "标记" },
];

/* ---------- Risk Rules Tab ---------- */

function RulesTab() {
  const { message } = App.useApp();
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const fetch = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/risk-rules");
      setItems(Array.isArray(data) ? data : []);
    } catch {
      message.error("加载风控规则失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, []);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      const config = values.config_json
        ? JSON.parse(values.config_json as string)
        : {};
      await api.post("/risk-rules", { ...values, config });
      message.success("规则创建成功");
      setOpen(false);
      form.resetFields();
      fetch();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "创建失败");
    }
  };

  const toggleEnabled = async (id: string, enabled: boolean) => {
    try {
      await api.patch(`/risk-rules/${id}`, { enabled });
      message.success(enabled ? "已启用" : "已禁用");
      fetch();
    } catch {
      message.error("操作失败");
    }
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "规则名称", dataIndex: "name", key: "name" },
    {
      title: "类型",
      dataIndex: "rule_type",
      key: "rule_type",
      render: (t: string) => RULE_TYPES.find((o) => o.value === t)?.label || t,
    },
    {
      title: "动作",
      dataIndex: "action",
      key: "action",
      render: (a: string) => {
        const color = a === "block" ? "red" : a === "warn" ? "orange" : "blue";
        return <Tag color={color}>{ACTIONS.find((o) => o.value === a)?.label || a}</Tag>;
      },
    },
    {
      title: "启用",
      dataIndex: "enabled",
      key: "enabled",
      render: (v: boolean, record: Record<string, unknown>) => (
        <Switch checked={v} onChange={(checked) => toggleEnabled(record.id as string, checked)} />
      ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Record<string, unknown>) => (
        <Popconfirm
          title="确认删除此规则？"
          onConfirm={async () => {
            await api.delete(`/risk-rules/${record.id as string}`);
            message.success("已删除");
            fetch();
          }}
        >
          <Button size="small" danger>删除</Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建规则
        </Button>
      </div>
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading} pagination={false} />
      <Modal title="新建风控规则" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={550}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="规则名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Space className="w-full" orientation="vertical">
            <div className="grid grid-cols-2 gap-4">
              <Form.Item name="rule_type" label="规则类型" rules={[{ required: true }]}>
                <Select options={RULE_TYPES} />
              </Form.Item>
              <Form.Item name="action" label="执行动作" rules={[{ required: true }]}>
                <Select options={ACTIONS} />
              </Form.Item>
            </div>
          </Space>
          <Form.Item name="config_json" label="规则配置 (JSON)">
            <Input.TextArea rows={4} placeholder='{"max_count": 5, "window_minutes": 60}' />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

/* ---------- Interceptions Tab ---------- */

function InterceptionsTab() {
  const { message } = App.useApp();
  const { items, total, page, loading, setPage } = usePaginatedList<Record<string, unknown>>(
    async ({ page, page_size }) => {
      try {
        const { data } = await api.get("/risk-rules/interceptions", { params: { page, page_size } });
        return { items: data.items || [], total: data.total || 0 };
      } catch {
        message.error("加载拦截记录失败");
        return { items: [], total: 0 };
      }
    }
  );

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "规则 ID", dataIndex: "risk_rule_id", key: "risk_rule_id", render: (v: string) => v?.slice(0, 8) + "..." },
    { title: "活动 ID", dataIndex: "campaign_id", key: "campaign_id", render: (v: string) => v ? v.slice(0, 8) + "..." : "—" },
    {
      title: "动作",
      dataIndex: "action",
      key: "action",
      render: (a: string) => <Tag color={a === "block" ? "red" : "orange"}>{a === "block" ? "拦截" : "预警"}</Tag>,
    },
    { title: "消费者", dataIndex: "consumer_id", key: "consumer_id", render: (v: string) => v?.slice(0, 8) + "..." || "—" },
  ];

  return (
    <Table
      columns={columns}
      dataSource={items}
      rowKey="id"
      loading={loading}
      pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
    />
  );
}

/* ---------- Alerts Tab ---------- */

function AlertsTab() {
  const { message } = App.useApp();
  const { items, total, page, loading, setPage, refresh } = usePaginatedList<Record<string, unknown>>(
    async ({ page, page_size }) => {
      try {
        const { data } = await api.get("/risk-alerts", { params: { page, page_size } });
        return { items: data.items || [], total: data.total || 0 };
      } catch {
        message.error("加载预警列表失败");
        return { items: [], total: 0 };
      }
    }
  );

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "码 ID", dataIndex: "public_id", key: "public_id" },
    {
      title: "类型",
      dataIndex: "alert_type",
      key: "alert_type",
      render: (t: string) => <Tag color="orange">{t}</Tag>,
    },
    { title: "详情", dataIndex: "detail", key: "detail", ellipsis: true },
    {
      title: "状态",
      dataIndex: "resolved",
      key: "resolved",
      render: (v: boolean) => <Tag color={v ? "green" : "red"}>{v ? "已处理" : "待处理"}</Tag>,
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Record<string, unknown>) => (
        !record.resolved ? (
          <Popconfirm
            title="确认标记为已处理？"
            onConfirm={async () => {
              await api.post(`/risk-alerts/${record.id as string}/resolve`);
              message.success("已处理");
              refresh();
            }}
          >
            <Button size="small" type="link">处理</Button>
          </Popconfirm>
        ) : null
      ),
    },
  ];

  return (
    <Table
      columns={columns}
      dataSource={items}
      rowKey="id"
      loading={loading}
      pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
    />
  );
}

/* ---------- Main ---------- */

const tabItems = [
  { key: "rules", label: "风控规则", children: <RulesTab /> },
  { key: "interceptions", label: "拦截记录", children: <InterceptionsTab /> },
  { key: "alerts", label: "预警列表", children: <AlertsTab /> },
];

export default function RiskPage() {
  return (
    <div>
      <Title level={4} className="!mb-4">风控中心</Title>
      <Tabs defaultActiveKey="rules" items={tabItems} />
    </div>
  );
}
