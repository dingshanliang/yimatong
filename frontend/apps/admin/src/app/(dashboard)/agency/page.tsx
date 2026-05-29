"use client";

import { useEffect, useState } from "react";
import {
  Card,
  Col,
  Row,
  Statistic,
  Table,
  Button,
  Modal,
  Steps,
  Form,
  Input,
  Select,
  Typography,
  Tag,
  message,
  Checkbox,
} from "antd";
import {
  TeamOutlined,
  UserOutlined,
  FileTextOutlined,
  RiseOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

interface Client {
  id: string;
  name: string;
  status: string;
  plan: string;
  brands: number;
  products: number;
  created_at: string;
}

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "活跃", color: "green" },
  suspended: { label: "已暂停", color: "default" },
  onboarding: { label: "配置中", color: "blue" },
};

export default function AgencyPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  const fetchClients = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/tenants", { params: { page: 1, page_size: 100 } });
      setClients((data.items || []).map((t: Record<string, unknown>) => ({
        id: String(t.id),
        name: String(t.name || ""),
        status: String(t.status || "active"),
        plan: String(t.plan || "free"),
        brands: Number(t.brands) || 0,
        products: Number(t.products) || 0,
        created_at: String(t.created_at || ""),
      })));
    } catch {
      setClients([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchClients(); }, []);

  const handleNext = async () => {
    if (currentStep < 3) {
      try {
        const fields = getStepFields(currentStep);
        if (fields.length > 0) await form.validateFields(fields);
        setCurrentStep(currentStep + 1);
      } catch { /* validation */ }
    }
  };

  const handlePrev = () => { if (currentStep > 0) setCurrentStep(currentStep - 1); };

  const handleFinish = async () => {
    setSaving(true);
    try {
      const values = form.getFieldsValue(true);
      await api.post("/tenants", {
        name: values.client_name,
        slug: values.client_name?.toLowerCase().replace(/\s+/g, "-").slice(0, 50),
        plan: values.plan || "free",
        admin_email: values.contact_email || `${values.client_name?.replace(/\s+/g, "").toLowerCase()}@example.com`,
        admin_name: values.contact_name || "管理员",
        admin_password: "TempPass123!",
      });
      message.success("客户初始化成功");
      setModalOpen(false);
      setCurrentStep(0);
      form.resetFields();
      fetchClients();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "初始化失败");
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => { setModalOpen(false); setCurrentStep(0); form.resetFields(); };

  const activeClients = clients.filter((c) => c.status === "active").length;
  const onboardingClients = clients.filter((c) => c.status === "onboarding").length;
  const totalClients = clients.length;

  const stepContent = [
    <div key="step1">
      <Form form={form} layout="vertical">
        <Form.Item name="client_name" label="客户名称" rules={[{ required: true, message: "请输入客户名称" }]}>
          <Input placeholder="客户公司名称" />
        </Form.Item>
        <Form.Item name="contact_name" label="联系人" rules={[{ required: true, message: "请输入联系人" }]}>
          <Input placeholder="联系人姓名" />
        </Form.Item>
        <Form.Item name="contact_phone" label="联系电话" rules={[{ required: true, message: "请输入联系电话" }]}>
          <Input placeholder="联系电话" />
        </Form.Item>
        <Form.Item name="contact_email" label="联系邮箱">
          <Input placeholder="联系邮箱" />
        </Form.Item>
        <Form.Item name="plan" label="套餐">
          <Select placeholder="选择套餐" options={[
            { value: "free", label: "免费版" },
            { value: "starter", label: "入门版" },
            { value: "pro", label: "专业版" },
            { value: "enterprise", label: "企业版" },
          ]} />
        </Form.Item>
      </Form>
    </div>,
    <div key="step2">
      <Form form={form} layout="vertical">
        <Form.Item name="brand_name" label="品牌名称" rules={[{ required: true, message: "请输入品牌名称" }]}>
          <Input placeholder="主品牌名称" />
        </Form.Item>
        <Form.Item name="industry" label="所属行业">
          <Select placeholder="选择行业" options={[
            { value: "food", label: "食品" },
            { value: "agriculture", label: "农产品" },
            { value: "beverage", label: "饮料" },
            { value: "daily", label: "日用品" },
            { value: "other", label: "其他" },
          ]} />
        </Form.Item>
      </Form>
    </div>,
    <div key="step3">
      <Form form={form} layout="vertical">
        <Form.Item name="page_template" label="扫码页模板">
          <Select placeholder="选择默认模板" options={[
            { value: "standard", label: "标准模板" },
            { value: "premium", label: "高级模板" },
          ]} />
        </Form.Item>
        <Form.Item name="notes" label="备注">
          <Input.TextArea rows={3} placeholder="特殊需求或备注" />
        </Form.Item>
      </Form>
    </div>,
    <div key="step4" className="py-6 text-center">
      <div className="mb-2 text-lg font-medium">配置确认</div>
      <div className="text-gray-400">请确认以上配置信息无误，点击完成开始初始化</div>
    </div>,
  ];

  const clientColumns: ColumnsType<Client> = [
    { title: "客户名称", dataIndex: "name", key: "name" },
    { title: "套餐", dataIndex: "plan", key: "plan", render: (p: string) => <Tag>{p}</Tag> },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    { title: "创建时间", dataIndex: "created_at", key: "created_at", render: (v: string) => v?.split("T")[0] || "—" },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">代运营工作台</Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          初始化新客户
        </Button>
      </div>

      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} sm={12} lg={6}>
          <Card><Statistic title="客户总数" value={totalClients} prefix={<TeamOutlined />} /></Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card><Statistic title="活跃客户" value={activeClients} prefix={<UserOutlined />} valueStyle={{ color: "#52c41a" }} /></Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card><Statistic title="配置中" value={onboardingClients} prefix={<FileTextOutlined />} valueStyle={{ color: "#faad14" }} /></Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card><Statistic title="本月新增" value={totalClients} prefix={<RiseOutlined />} valueStyle={{ color: "#1890ff" }} /></Card>
        </Col>
      </Row>

      <Card title="客户列表" size="small">
        <Table columns={clientColumns} dataSource={clients} rowKey="id" loading={loading} pagination={false} size="small" />
      </Card>

      <Modal
        title="初始化客户配置"
        open={modalOpen}
        onCancel={handleCancel}
        width={600}
        footer={
          currentStep < 3
            ? [
                <Button key="cancel" onClick={handleCancel}>取消</Button>,
                currentStep > 0 && <Button key="prev" onClick={handlePrev}>上一步</Button>,
                <Button key="next" type="primary" onClick={handleNext}>下一步</Button>,
              ]
            : [
                <Button key="prev" onClick={handlePrev}>上一步</Button>,
                <Button key="finish" type="primary" onClick={handleFinish} loading={saving}>完成</Button>,
              ]
        }
      >
        <Steps
          current={currentStep}
          items={[{ title: "基础信息" }, { title: "产品配置" }, { title: "页面配置" }, { title: "完成" }]}
          className="mb-6"
          size="small"
        />
        {stepContent[currentStep]}
      </Modal>
    </div>
  );
}

function getStepFields(step: number): string[] {
  switch (step) {
    case 0: return ["client_name", "contact_name", "contact_phone"];
    case 1: return ["brand_name"];
    default: return [];
  }
}
