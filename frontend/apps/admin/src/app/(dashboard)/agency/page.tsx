"use client";

import { useState } from "react";
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
} from "antd";
import {
  TeamOutlined,
  UserOutlined,
  FileTextOutlined,
  RiseOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

const { Title } = Typography;

interface Client {
  id: string;
  name: string;
  contact: string;
  status: string;
  brands: number;
  products: number;
  created_at: string;
}

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "活跃", color: "green" },
  inactive: { label: "非活跃", color: "default" },
  onboarding: { label: "配置中", color: "blue" },
};

// Mock data - backend API not yet available
const MOCK_CLIENTS: Client[] = [
  {
    id: "1",
    name: "示例品牌 A",
    contact: "张经理",
    status: "active",
    brands: 2,
    products: 15,
    created_at: "2026-05-20",
  },
  {
    id: "2",
    name: "示例品牌 B",
    contact: "李经理",
    status: "onboarding",
    brands: 1,
    products: 5,
    created_at: "2026-05-25",
  },
  {
    id: "3",
    name: "示例品牌 C",
    contact: "王经理",
    status: "inactive",
    brands: 1,
    products: 3,
    created_at: "2026-04-10",
  },
];

export default function AgencyPage() {
  const [modalOpen, setModalOpen] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  const handleNext = async () => {
    if (currentStep < 3) {
      // Validate current step fields
      try {
        const fieldsToValidate = getStepFields(currentStep);
        if (fieldsToValidate.length > 0) {
          await form.validateFields(fieldsToValidate);
        }
        setCurrentStep(currentStep + 1);
      } catch {
        // validation failed
      }
    }
  };

  const handlePrev = () => {
    if (currentStep > 0) {
      setCurrentStep(currentStep - 1);
    }
  };

  const handleFinish = async () => {
    setSaving(true);
    try {
      // TODO: call API to initialize client
      await new Promise((resolve) => setTimeout(resolve, 800));
      message.success("客户配置初始化完成");
      setModalOpen(false);
      setCurrentStep(0);
      form.resetFields();
    } catch {
      message.error("初始化失败");
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    setModalOpen(false);
    setCurrentStep(0);
    form.resetFields();
  };

  const stepContent = [
    // Step 1: 基础信息
    <div key="step1">
      <Form form={form} layout="vertical">
        <Form.Item
          name="client_name"
          label="客户名称"
          rules={[{ required: true, message: "请输入客户名称" }]}
        >
          <Input placeholder="客户公司名称" />
        </Form.Item>
        <Form.Item
          name="contact_name"
          label="联系人"
          rules={[{ required: true, message: "请输入联系人" }]}
        >
          <Input placeholder="联系人姓名" />
        </Form.Item>
        <Form.Item
          name="contact_phone"
          label="联系电话"
          rules={[{ required: true, message: "请输入联系电话" }]}
        >
          <Input placeholder="联系电话" />
        </Form.Item>
        <Form.Item name="contact_email" label="联系邮箱">
          <Input placeholder="联系邮箱" />
        </Form.Item>
      </Form>
    </div>,

    // Step 2: 产品配置
    <div key="step2">
      <Form form={form} layout="vertical">
        <Form.Item
          name="brand_name"
          label="品牌名称"
          rules={[{ required: true, message: "请输入品牌名称" }]}
        >
          <Input placeholder="主品牌名称" />
        </Form.Item>
        <Form.Item name="product_count" label="预计产品数量">
          <Select
            placeholder="选择数量范围"
            options={[
              { value: "1-10", label: "1-10 个" },
              { value: "11-50", label: "11-50 个" },
              { value: "51-200", label: "51-200 个" },
              { value: "200+", label: "200+ 个" },
            ]}
          />
        </Form.Item>
        <Form.Item name="industry" label="所属行业">
          <Select
            placeholder="选择行业"
            options={[
              { value: "food", label: "食品" },
              { value: "agriculture", label: "农产品" },
              { value: "beverage", label: "饮料" },
              { value: "daily", label: "日用品" },
              { value: "other", label: "其他" },
            ]}
          />
        </Form.Item>
      </Form>
    </div>,

    // Step 3: 页面配置
    <div key="step3">
      <Form form={form} layout="vertical">
        <Form.Item name="page_template" label="扫码页模板">
          <Select
            placeholder="选择默认模板"
            options={[
              { value: "standard", label: "标准模板" },
              { value: "premium", label: "高级模板" },
              { value: "custom", label: "自定义模板" },
            ]}
          />
        </Form.Item>
        <Form.Item name="has_campaign" label="是否配置活动" valuePropName="checked">
          <Select
            placeholder="选择活动配置"
            options={[
              { value: "yes", label: "是" },
              { value: "no", label: "否" },
            ]}
          />
        </Form.Item>
        <Form.Item name="notes" label="备注">
          <Input.TextArea rows={3} placeholder="特殊需求或备注" />
        </Form.Item>
      </Form>
    </div>,

    // Step 4: 完成
    <div key="step4" className="text-center py-6">
      <div className="text-lg font-medium mb-2">配置确认</div>
      <div className="text-gray-400">
        请确认以上配置信息无误，点击完成开始初始化
      </div>
    </div>,
  ];

  const clientColumns: ColumnsType<Client> = [
    { title: "客户名称", dataIndex: "name", key: "name" },
    { title: "联系人", dataIndex: "contact", key: "contact" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    { title: "品牌数", dataIndex: "brands", key: "brands" },
    { title: "产品数", dataIndex: "products", key: "products" },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          代运营工作台
        </Title>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setModalOpen(true)}
        >
          初始化新客户
        </Button>
      </div>

      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="客户总数"
              value={3}
              prefix={<TeamOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="活跃客户"
              value={1}
              prefix={<UserOutlined />}
              valueStyle={{ color: "#52c41a" }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="待办任务"
              value={5}
              prefix={<FileTextOutlined />}
              valueStyle={{ color: "#faad14" }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="本月新增"
              value={2}
              prefix={<RiseOutlined />}
              valueStyle={{ color: "#1890ff" }}
            />
          </Card>
        </Col>
      </Row>

      <Card title="客户列表" size="small">
        <div className="mb-4 text-gray-400 text-sm">
          注：代运营管理功能待后端 API 实现，当前为模拟数据
        </div>
        <Table
          columns={clientColumns}
          dataSource={MOCK_CLIENTS}
          rowKey="id"
          pagination={false}
          size="small"
        />
      </Card>

      <Modal
        title="初始化客户配置"
        open={modalOpen}
        onCancel={handleCancel}
        width={600}
        footer={
          currentStep < 3
            ? [
                <Button key="cancel" onClick={handleCancel}>
                  取消
                </Button>,
                currentStep > 0 && (
                  <Button key="prev" onClick={handlePrev}>
                    上一步
                  </Button>
                ),
                <Button key="next" type="primary" onClick={handleNext}>
                  下一步
                </Button>,
              ]
            : [
                <Button key="prev" onClick={handlePrev}>
                  上一步
                </Button>,
                <Button
                  key="finish"
                  type="primary"
                  onClick={handleFinish}
                  loading={saving}
                >
                  完成
                </Button>,
              ]
        }
      >
        <Steps
          current={currentStep}
          items={[
            { title: "基础信息" },
            { title: "产品配置" },
            { title: "页面配置" },
            { title: "完成" },
          ]}
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
    case 0:
      return ["client_name", "contact_name", "contact_phone"];
    case 1:
      return ["brand_name"];
    case 2:
      return [];
    default:
      return [];
  }
}
