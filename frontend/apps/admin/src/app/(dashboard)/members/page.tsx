"use client";

import { useEffect, useState } from "react";
import { App, Button, Descriptions, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tabs, Tag, Typography, Popconfirm } from "antd";
import { DeleteOutlined, EditOutlined, PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

const RULE_TYPES = [
  { value: "scan", label: "扫码积分" },
  { value: "first_scan", label: "首扫奖励" },
  { value: "register", label: "注册奖励" },
  { value: "checkin", label: "签到积分" },
  { value: "repurchase", label: "复购导入" },
  { value: "activity", label: "活动行为" },
];

/* ---------- Point Rules Tab (增强版) ---------- */

function RulesTab() {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editItem, setEditItem] = useState<Record<string, unknown> | null>(null);
  const [form] = Form.useForm();

  const fetch = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/members/point-rules");
      setItems(Array.isArray(data) ? data : []);
    } catch {
      App.useApp().message.error("加载积分规则失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, []);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      await api.post("/members/point-rules", values);
      App.useApp().message.success("规则创建成功");
      setOpen(false);
      form.resetFields();
      fetch();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      App.useApp().message.error(err.response?.data?.detail || "创建失败");
    }
  };

  const handleUpdate = async (values: Record<string, unknown>) => {
    if (!editItem) return;
    try {
      await api.put(`/members/point-rules/${editItem.id}`, values);
      App.useApp().message.success("规则更新成功");
      setEditItem(null);
      form.resetFields();
      fetch();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      App.useApp().message.error(err.response?.data?.detail || "更新失败");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/members/point-rules/${id}`);
      App.useApp().message.success("规则已删除");
      fetch();
    } catch {
      App.useApp().message.error("删除失败");
    }
  };

  const handleToggle = async (id: string, enabled: boolean) => {
    try {
      await api.put(`/members/point-rules/${id}`, { enabled });
      fetch();
    } catch {
      App.useApp().message.error("操作失败");
    }
  };

  const openEdit = (item: Record<string, unknown>) => {
    setEditItem(item);
    form.setFieldsValue({
      points: item.points,
      daily_limit: item.daily_limit,
      description: item.description,
    });
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "规则类型", dataIndex: "rule_type", key: "rule_type", render: (t: string) => RULE_TYPES.find((o) => o.value === t)?.label || t },
    { title: "积分数", dataIndex: "points", key: "points" },
    { title: "每日上限", dataIndex: "daily_limit", key: "daily_limit", render: (v: number) => v > 0 ? v : "不限" },
    { title: "说明", dataIndex: "description", key: "description", render: (v: string) => v || "—" },
    { title: "状态", dataIndex: "enabled", key: "enabled", render: (v: boolean, record) => <Switch size="small" checked={v} onChange={(checked) => handleToggle(String(record.id), checked)} /> },
    {
      title: "操作", key: "actions", width: 100,
      render: (_: unknown, record: Record<string, unknown>) => (
        <Space size="small">
          <Button size="small" type="link" icon={<EditOutlined />} onClick={() => openEdit(record)} />
          <Popconfirm title="确定删除此规则？" onConfirm={() => handleDelete(String(record.id))}>
            <Button size="small" type="link" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); setOpen(true); }}>
          新建规则
        </Button>
      </div>
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading} pagination={false} />

      <Modal title="新建积分规则" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="rule_type" label="规则类型" rules={[{ required: true }]}>
            <Select options={RULE_TYPES} />
          </Form.Item>
          <Form.Item name="points" label="积分数" rules={[{ required: true }]}>
            <InputNumber min={1} className="w-full" />
          </Form.Item>
          <Form.Item name="daily_limit" label="每日上限（0=不限）">
            <InputNumber min={0} className="w-full" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input maxLength={200} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="编辑积分规则" open={!!editItem} onCancel={() => { setEditItem(null); form.resetFields(); }} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleUpdate}>
          <Form.Item name="points" label="积分数" rules={[{ required: true }]}>
            <InputNumber min={1} className="w-full" />
          </Form.Item>
          <Form.Item name="daily_limit" label="每日上限（0=不限）">
            <InputNumber min={0} className="w-full" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input maxLength={200} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

/* ---------- Point Products Tab (积分商城管理) ---------- */

function ProductsTab() {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editItem, setEditItem] = useState<Record<string, unknown> | null>(null);
  const [form] = Form.useForm();
  const { message } = App.useApp();

  const fetch = async (p = 1) => {
    setLoading(true);
    try {
      const { data } = await api.get("/members/point-products", { params: { page: p, page_size: 20 } });
      setItems(data.items || []);
      setTotal(data.total || 0);
      setPage(p);
    } catch {
      message.error("加载积分商品失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, []);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      await api.post("/members/point-products", values);
      message.success("商品创建成功");
      setOpen(false);
      form.resetFields();
      fetch();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "创建失败");
    }
  };

  const handleUpdate = async (values: Record<string, unknown>) => {
    if (!editItem) return;
    try {
      await api.put(`/members/point-products/${editItem.id}`, values);
      message.success("商品更新成功");
      setEditItem(null);
      form.resetFields();
      fetch();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "更新失败");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/members/point-products/${id}`);
      message.success("商品已删除");
      fetch();
    } catch {
      message.error("删除失败");
    }
  };

  const openEdit = (item: Record<string, unknown>) => {
    setEditItem(item);
    form.setFieldsValue({
      name: item.name,
      description: item.description,
      points_cost: item.points_cost,
      stock: item.stock,
      enabled: item.enabled,
    });
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "商品名称", dataIndex: "name", key: "name" },
    { title: "积分价格", dataIndex: "points_cost", key: "points_cost", render: (v: number) => <Tag color="orange">{v} 积分</Tag> },
    { title: "库存", dataIndex: "stock", key: "stock" },
    { title: "已兑换", dataIndex: "total_claimed", key: "total_claimed" },
    { title: "状态", dataIndex: "enabled", key: "enabled", render: (v: boolean) => <Tag color={v ? "green" : "default"}>{v ? "上架" : "下架"}</Tag> },
    {
      title: "操作", key: "actions", width: 100,
      render: (_: unknown, record: Record<string, unknown>) => (
        <Space size="small">
          <Button size="small" type="link" icon={<EditOutlined />} onClick={() => openEdit(record)} />
          <Popconfirm title="确定删除此商品？" onConfirm={() => handleDelete(String(record.id))}>
            <Button size="small" type="link" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const productForm = (
    <Form form={form} layout="vertical" onFinish={editItem ? handleUpdate : handleCreate}>
      <Form.Item name="name" label="商品名称" rules={[{ required: true }]}>
        <Input maxLength={200} />
      </Form.Item>
      <Form.Item name="description" label="描述">
        <Input.TextArea rows={3} />
      </Form.Item>
      <Form.Item name="points_cost" label="积分价格" rules={[{ required: true }]}>
        <InputNumber min={1} className="w-full" />
      </Form.Item>
      <Form.Item name="stock" label="库存" rules={[{ required: true }]}>
        <InputNumber min={0} className="w-full" />
      </Form.Item>
      <Form.Item name="enabled" label="上架" valuePropName="checked">
        <Switch defaultChecked />
      </Form.Item>
    </Form>
  );

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); setEditItem(null); setOpen(true); }}>
          新增商品
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={{ current: page, total, pageSize: 20, onChange: fetch, showTotal: (t) => `共 ${t} 个商品` }}
      />

      <Modal
        title={editItem ? "编辑积分商品" : "新增积分商品"}
        open={open || !!editItem}
        onCancel={() => { setOpen(false); setEditItem(null); form.resetFields(); }}
        onOk={() => form.submit()}
        width={500}
      >
        {productForm}
      </Modal>
    </>
  );
}

/* ---------- Consumer Lookup + Points Ops ---------- */

function ConsumersTab() {
  const [searchId, setSearchId] = useState("");
  const [profile, setProfile] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [txns, setTxns] = useState<Record<string, unknown>[]>([]);
  const [txnTotal, setTxnTotal] = useState(0);
  const [txnPage, setTxnPage] = useState(1);
  const [awardOpen, setAwardOpen] = useState(false);
  const [spendOpen, setSpendOpen] = useState(false);
  const [form] = Form.useForm();
  const { message } = App.useApp();

  const fetchProfile = async () => {
    if (!searchId) return;
    setLoading(true);
    try {
      const { data } = await api.get(`/members/consumers/${searchId}`);
      setProfile(data);
      fetchTxns(searchId, 1);
    } catch {
      message.error("未找到消费者");
      setProfile(null);
    } finally {
      setLoading(false);
    }
  };

  const fetchTxns = async (consumerId: string, p: number) => {
    try {
      const { data } = await api.get(`/members/consumers/${consumerId}/transactions`, { params: { page: p, page_size: 20 } });
      setTxns(data.items || []);
      setTxnTotal(data.total || 0);
      setTxnPage(p);
    } catch {
      /* silent */
    }
  };

  const handleAward = async (values: Record<string, unknown>) => {
    try {
      await api.post("/members/points/award", { ...values, consumer_id: searchId });
      message.success("积分发放成功");
      setAwardOpen(false);
      form.resetFields();
      fetchProfile();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "操作失败");
    }
  };

  const handleSpend = async (values: Record<string, unknown>) => {
    try {
      await api.post("/members/points/spend", { ...values, consumer_id: searchId });
      message.success("积分扣减成功");
      setSpendOpen(false);
      form.resetFields();
      fetchProfile();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "操作失败");
    }
  };

  const txnColumns: ColumnsType<Record<string, unknown>> = [
    {
      title: "类型", dataIndex: "txn_type", key: "txn_type",
      render: (t: string) => <Tag color={t === "earning" ? "green" : t === "expired" ? "default" : "red"}>
        {t === "earning" ? "获得" : t === "expired" ? "过期" : "消费"}</Tag>,
    },
    { title: "数量", dataIndex: "amount", key: "amount" },
    { title: "余额", dataIndex: "balance_after", key: "balance_after" },
    { title: "原因", dataIndex: "reason", key: "reason" },
    {
      title: "过期时间", dataIndex: "expires_at", key: "expires_at",
      render: (v: string) => v ? new Date(v).toLocaleDateString() : "—",
    },
  ];

  return (
    <>
      <Space className="mb-4">
        <Input
          placeholder="输入消费者 ID"
          value={searchId}
          onChange={(e) => setSearchId(e.target.value)}
          style={{ width: 300 }}
          onPressEnter={fetchProfile}
        />
        <Button type="primary" onClick={fetchProfile} loading={loading}>查询</Button>
      </Space>

      {profile && (
        <>
          <Descriptions bordered column={2} size="small" className="mb-4">
            <Descriptions.Item label="消费者 ID">{String(profile.id)}</Descriptions.Item>
            <Descriptions.Item label="会员等级">{String(profile.member_level || "普通")}</Descriptions.Item>
            <Descriptions.Item label="积分余额">{Number(profile.total_points)}</Descriptions.Item>
          </Descriptions>
          <Space className="mb-4">
            <Button type="primary" onClick={() => setAwardOpen(true)}>发放积分</Button>
            <Button onClick={() => setSpendOpen(true)}>扣减积分</Button>
          </Space>
          <Table
            columns={txnColumns}
            dataSource={txns}
            rowKey="id"
            size="small"
            pagination={{ current: txnPage, total: txnTotal, pageSize: 20, onChange: (p) => fetchTxns(searchId, p), showTotal: (t) => `共 ${t} 条` }}
          />
        </>
      )}

      <Modal title="发放积分" open={awardOpen} onCancel={() => setAwardOpen(false)} onOk={() => form.submit()}>
        <Form form={form} layout="vertical" onFinish={handleAward}>
          <Form.Item name="points" label="积分数" rules={[{ required: true }]}>
            <InputNumber min={1} className="w-full" />
          </Form.Item>
          <Form.Item name="reason" label="原因" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="扣减积分" open={spendOpen} onCancel={() => setSpendOpen(false)} onOk={() => form.submit()}>
        <Form form={form} layout="vertical" onFinish={handleSpend}>
          <Form.Item name="points" label="积分数" rules={[{ required: true }]}>
            <InputNumber min={1} className="w-full" />
          </Form.Item>
          <Form.Item name="reason" label="原因" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

/* ---------- Main ---------- */

const tabItems = [
  { key: "consumers", label: "消费者查询", children: <ConsumersTab /> },
  { key: "rules", label: "积分规则", children: <RulesTab /> },
  { key: "products", label: "积分商城", children: <ProductsTab /> },
];

export default function MembersPage() {
  return (
    <div>
      <Title level={4} className="!mb-4">会员积分</Title>
      <Tabs defaultActiveKey="consumers" items={tabItems} />
    </div>
  );
}

