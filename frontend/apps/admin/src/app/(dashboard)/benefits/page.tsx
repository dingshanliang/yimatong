"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { App, Button, Form, Input, InputNumber, Modal, Popconfirm, Progress, Select, Space, Table, Tabs, Tag, Typography, Alert } from "antd";
import { PlusOutlined, SendOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { useCrud } from "@/lib/hooks";
import { BenefitConfigFields } from "./_components/BenefitConfigFields";
import { BENEFIT_TYPE_MAP, BENEFIT_STATUS_MAP, CLAIM_STATUS_MAP, DELIVERY_STATUS_MAP } from "./_components/constants";
import { yuanToFen, fenToYuan } from "./_components/utils";
import type { Benefit, BenefitClaim, Campaign, Connector } from "./_components/types";

const { Title } = Typography;

export default function BenefitsPage() {
  const router = useRouter();
  const { message } = App.useApp();
  const {
    items: benefits, total: benefitsTotal, page: benefitsPage, loading: benefitsLoading,
    setPage: setBenefitsPage, update: updateBenefit, remove: removeBenefit, mutate: mutateBenefits,
  } = useCrud<Benefit>("/benefits");

  const {
    items: claims, total: claimsTotal, page: claimsPage, loading: claimsLoading,
    setPage: setClaimsPage,
  } = useCrud<BenefitClaim>("/benefits/admin/claims");

  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [allConnectors, setAllConnectors] = useState<Connector[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<Benefit | null>(null);
  const [form] = Form.useForm();
  const [activeTab, setActiveTab] = useState("benefits");
  const [benefitType, setBenefitType] = useState<string>("");

  const wechatPayConnectors = allConnectors.filter((c) => c.connector_type === "wechat_pay_transfer");
  const couponPoolConnectors = allConnectors.filter((c) => c.connector_type === "coupon_pool");

  const fetchCampaigns = useCallback(async () => {
    try { const { data } = await api.get("/campaigns", { params: { page_size: 100 } }); setCampaigns(data.items || []); } catch { /* ignore */ }
  }, []);

  const fetchConnectors = useCallback(async () => {
    try { const { data } = await api.get("/connectors/connectors"); setAllConnectors(Array.isArray(data) ? data : []); } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchCampaigns(); fetchConnectors(); }, [fetchCampaigns, fetchConnectors]);

  const openCreate = () => { setEditItem(null); form.resetFields(); setBenefitType(""); setModalOpen(true); };

  const openEdit = (record: Benefit) => {
    setEditItem(record);
    setBenefitType(record.benefit_type);
    const configJson = { ...record.config_json } as Record<string, unknown>;
    if (record.benefit_type === "cash_red_packet") {
      for (const field of ["fixed_amount", "min_amount", "max_amount", "lucky_min_per", "budget"] as const) {
        if (typeof configJson[field] === "number") configJson[field] = fenToYuan(configJson[field] as number);
      }
    }
    form.setFieldsValue({ name: record.name, benefit_type: record.benefit_type, stock_total: record.stock_total, per_person_limit: record.per_person_limit, campaign_id: record.campaign_id, status: record.status, connector_id: record.connector_id, config_json: configJson });
    setModalOpen(true);
  };

  const handleSubmit = async (values: Record<string, unknown>) => {
    try {
      let configJson = values.config_json as Record<string, number> || {};
      if (values.benefit_type === "cash_red_packet") {
        for (const field of ["fixed_amount", "min_amount", "max_amount", "lucky_min_per", "budget"]) {
          if (typeof configJson[field] === "number") configJson[field] = yuanToFen(configJson[field]);
        }
      }
      const payload = { name: values.name, benefit_type: values.benefit_type, stock_total: values.stock_total, per_person_limit: values.per_person_limit, config_json: configJson, connector_id: values.connector_id || null };
      if (editItem) { await updateBenefit(editItem.id, payload); message.success("权益更新成功"); }
      else { await api.post(`/campaigns/${values.campaign_id}/benefits`, payload); message.success("权益创建成功"); mutateBenefits(); }
      setModalOpen(false); form.resetFields();
    } catch (e: unknown) { const err = e as { response?: { data?: { detail?: string } } }; message.error(err.response?.data?.detail || (editItem ? "更新权益失败" : "创建权益失败")); }
  };

  const handleDelete = async (id: string) => { try { await removeBenefit(id); message.success("权益已删除"); } catch { message.error("删除失败"); } };
  const handleToggleStatus = async (record: Benefit) => {
    const newStatus = record.status === "active" ? "inactive" : "active";
    try { await updateBenefit(record.id, { status: newStatus }); message.success(newStatus === "active" ? "已启用" : "已停用"); } catch { message.error("操作失败"); }
  };

  const campaignMap = campaigns.reduce<Record<string, string>>((acc, c) => { acc[c.id] = c.name; return acc; }, {});
  const benefitMap = benefits.reduce<Record<string, string>>((acc, b) => { acc[b.id] = b.name; return acc; }, {});

  const benefitColumns: ColumnsType<Benefit> = [
    { title: "权益名称", dataIndex: "name", key: "name" },
    { title: "类型", dataIndex: "benefit_type", key: "benefit_type", render: (t: string) => { const info = BENEFIT_TYPE_MAP[t] || { label: t, color: "default" }; return <Tag color={info.color}>{info.label}</Tag>; } },
    { title: "关联活动", dataIndex: "campaign_id", key: "campaign_id", render: (v: string) => campaignMap[v] || v },
    { title: "库存", key: "stock", render: (_: unknown, record) => {
      const percent = record.stock_total > 0 ? Math.round((record.stock_used / record.stock_total) * 100) : 0;
      return <div className="min-w-[120px]"><div className="mb-1 text-xs text-gray-500">已用 {record.stock_used} / {record.stock_total}（剩余 {record.stock_total - record.stock_used}）</div><Progress percent={percent} size="small" status={percent >= 90 ? "exception" : percent >= 70 ? "active" : undefined} /></div>;
    }},
    { title: "每人限领", dataIndex: "per_person_limit", key: "per_person_limit" },
    { title: "状态", dataIndex: "status", key: "status", render: (s: string) => { const info = BENEFIT_STATUS_MAP[s] || { label: s, color: "default" }; return <Tag color={info.color}>{info.label}</Tag>; } },
    { title: "创建时间", dataIndex: "created_at", key: "created_at" },
    { title: "操作", key: "actions", render: (_: unknown, record) => (
      <Space>
        <Button size="small" onClick={() => openEdit(record)}>编辑</Button>
        <Popconfirm title={record.status === "active" ? "确认停用？" : "确认启用？"} onConfirm={() => handleToggleStatus(record)}><Button size="small" danger={record.status === "active"}>{record.status === "active" ? "停用" : "启用"}</Button></Popconfirm>
        <Popconfirm title="确认删除权益？" onConfirm={() => handleDelete(record.id)}><Button size="small" danger>删除</Button></Popconfirm>
      </Space>
    )},
  ];

  const claimColumns: ColumnsType<BenefitClaim> = [
    { title: "消费者 ID", dataIndex: "consumer_id", key: "consumer_id", render: (v: string) => v.slice(0, 8) + "..." },
    { title: "权益", dataIndex: "benefit_id", key: "benefit_id", render: (v: string) => benefitMap[v] || v.slice(0, 8) + "..." },
    { title: "领取状态", dataIndex: "status", key: "status", render: (s: string) => { const info = CLAIM_STATUS_MAP[s] || { label: s, color: "default" }; return <Tag color={info.color}>{info.label}</Tag>; } },
    { title: "发放状态", dataIndex: "delivery_status", key: "delivery_status", render: (s: string) => { const info = DELIVERY_STATUS_MAP[s] || { label: s || "-", color: "default" }; return <Tag color={info.color}>{info.label}</Tag>; } },
    { title: "领取时间", dataIndex: "claimed_at", key: "claimed_at" },
    { title: "操作", key: "actions", render: (_: unknown, record) => (
      record.delivery_status === "pending" || record.delivery_status === "failed" ? <Button size="small" icon={<SendOutlined />} onClick={() => router.push("/connectors?tab=deliveries")}>发放详情</Button> : null
    )},
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">权益管理</Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建权益</Button>
      </div>
      <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
        { key: "benefits", label: "权益列表", children: <Table columns={benefitColumns} dataSource={benefits} rowKey="id" loading={benefitsLoading} pagination={{ current: benefitsPage, total: benefitsTotal, pageSize: 20, onChange: setBenefitsPage, showTotal: (t) => `共 ${t} 条` }} /> },
        { key: "claims", label: "领取记录", children: <Table columns={claimColumns} dataSource={claims} rowKey="id" loading={claimsLoading} pagination={{ current: claimsPage, total: claimsTotal, pageSize: 20, onChange: setClaimsPage, showTotal: (t) => `共 ${t} 条` }} /> },
      ]} />
      <Modal title={editItem ? "编辑权益" : "新建权益"} open={modalOpen} onCancel={() => setModalOpen(false)} onOk={() => form.submit()} width={640} destroyOnClose>
        {benefitType === "cash_red_packet" && <Alert message="微信现金红包" description="单笔转账上限 200 元，金额以元为单位输入，系统自动转换为分存储。请确保已配置微信支付转账连接器。" type="info" showIcon className="mb-4" />}
        <Form form={form} layout="vertical" onFinish={handleSubmit} onValuesChange={(changed) => { if (changed.benefit_type) setBenefitType(changed.benefit_type as string); }}>
          <Form.Item name="name" label="权益名称" rules={[{ required: true, message: "请输入权益名称" }]}><Input data-testid="benefit-name-input" /></Form.Item>
          <Form.Item name="benefit_type" label="权益类型" rules={[{ required: true, message: "请选择权益类型" }]}>
            <Select options={Object.entries(BENEFIT_TYPE_MAP).map(([value, { label }]) => ({ value, label }))} data-testid="benefit-type-select" />
          </Form.Item>
          <Form.Item name="campaign_id" label="关联活动" rules={[{ required: true, message: "请选择关联活动" }]}>
            <Select placeholder="选择活动" options={campaigns.map((c) => ({ value: c.id, label: c.name }))} showSearch optionFilterProp="label" data-testid="benefit-campaign-select" />
          </Form.Item>
          <div className="grid grid-cols-2 gap-4">
            <Form.Item name="stock_total" label="总库存" rules={[{ required: true, message: "请输入总库存" }]}><InputNumber min={1} className="w-full" data-testid="benefit-stock-input" /></Form.Item>
            <Form.Item name="per_person_limit" label="每人限领" rules={[{ required: true, message: "请输入每人限领数量" }]}><InputNumber min={1} className="w-full" data-testid="benefit-limit-input" /></Form.Item>
          </div>
          <BenefitConfigFields benefitType={benefitType} form={form} couponPoolConnectors={couponPoolConnectors} wechatPayConnectors={wechatPayConnectors} />
        </Form>
      </Modal>
    </div>
  );
}
