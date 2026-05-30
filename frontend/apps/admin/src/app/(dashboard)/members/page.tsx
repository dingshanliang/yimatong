"use client";

import { useEffect, useState } from "react";
import { App, Button, Descriptions, Form, Input, InputNumber, message, Modal, Select, Space, Table, Tabs, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

const RULE_TYPES = [
  { value: "scan", label: "扫码积分" },
  { value: "first_scan", label: "首扫奖励" },
  { value: "repurchase", label: "复购导入" },
  { value: "activity", label: "活动行为" },
];

/* ---------- Point Rules Tab ---------- */

function RulesTab() {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const fetch = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/members/point-rules");
      setItems(Array.isArray(data) ? data : []);
    } catch {
      message.error("加载积分规则失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, []);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      await api.post("/members/point-rules", values);
      message.success("规则创建成功");
      setOpen(false);
      form.resetFields();
      fetch();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "创建失败");
    }
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "规则类型", dataIndex: "rule_type", key: "rule_type", render: (t: string) => RULE_TYPES.find((o) => o.value === t)?.label || t },
    { title: "积分数", dataIndex: "points", key: "points" },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建规则
        </Button>
      </div>
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading} pagination={false} />
      <Modal title="新建积分规则" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={450}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="rule_type" label="规则类型" rules={[{ required: true }]}>
            <Select options={RULE_TYPES} />
          </Form.Item>
          <Form.Item name="points" label="积分数" rules={[{ required: true }]}>
            <InputNumber min={1} className="w-full" />
          </Form.Item>
        </Form>
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
    { title: "类型", dataIndex: "txn_type", key: "txn_type", render: (t: string) => <Tag color={t === "earn" ? "green" : "red"}>{t === "earn" ? "获得" : "消费"}</Tag> },
    { title: "数量", dataIndex: "amount", key: "amount" },
    { title: "余额", dataIndex: "balance_after", key: "balance_after" },
    { title: "原因", dataIndex: "reason", key: "reason" },
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
];

export default function MembersPage() {
  const { message } = App.useApp();
  return (
    <div>
      <Title level={4} className="!mb-4">会员积分</Title>
      <Tabs defaultActiveKey="consumers" items={tabItems} />
    </div>
  );
}
