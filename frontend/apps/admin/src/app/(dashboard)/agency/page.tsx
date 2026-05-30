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
  DatePicker,
  Space,
  List,
  Progress,
} from "antd";
import {
  TeamOutlined,
  UserOutlined,
  FileTextOutlined,
  CheckSquareOutlined,
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
  plan_expires_at: string | null;
  created_at: string;
}

interface Task {
  id: string;
  tenant_id: string;
  title: string;
  status: string;
  priority: string;
  due_date: string | null;
}

interface ChecklistResult {
  tenant_id: string;
  ready: boolean;
  passed_count: number;
  total_count: number;
  checks: { name: string; passed: boolean; detail: string }[];
}

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "活跃", color: "green" },
  suspended: { label: "已暂停", color: "default" },
  onboarding: { label: "配置中", color: "blue" },
};

const PRIORITY_MAP: Record<string, { label: string; color: string }> = {
  low: { label: "低", color: "default" },
  medium: { label: "中", color: "blue" },
  high: { label: "高", color: "red" },
};

const TASK_STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待处理", color: "default" },
  in_progress: { label: "进行中", color: "processing" },
  completed: { label: "已完成", color: "success" },
  cancelled: { label: "已取消", color: "default" },
};

export default function AgencyPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  const [taskModalOpen, setTaskModalOpen] = useState(false);
  const [taskForm] = Form.useForm();
  const [taskSaving, setTaskSaving] = useState(false);

  const [checklistModalOpen, setChecklistModalOpen] = useState(false);
  const [checklistData, setChecklistData] = useState<ChecklistResult | null>(null);
  const [checklistClientName, setChecklistClientName] = useState("");
  const [checklistLoading, setChecklistLoading] = useState(false);

  const fetchClients = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/tenants", { params: { page: 1, page_size: 100 } });
      setClients(
        (data.items || []).map((t: Record<string, unknown>) => ({
          id: String(t.id),
          name: String(t.name || ""),
          status: String(t.status || "active"),
          plan: String(t.plan || "free"),
          plan_expires_at: t.plan_expires_at ? String(t.plan_expires_at) : null,
          created_at: String(t.created_at || ""),
        }))
      );
    } catch {
      setClients([]);
    } finally {
      setLoading(false);
    }
  };

  const fetchTasks = async () => {
    try {
      const { data } = await api.get("/ops/tasks", { params: { page: 1, page_size: 100 } });
      setTasks(
        (data.items || []).map((t: Record<string, unknown>) => ({
          id: String(t.id),
          tenant_id: String(t.tenant_id || ""),
          title: String(t.title || ""),
          status: String(t.status || "pending"),
          priority: String(t.priority || "medium"),
          due_date: t.due_date ? String(t.due_date) : null,
        }))
      );
    } catch {
      setTasks([]);
    }
  };

  useEffect(() => {
    fetchClients();
    fetchTasks();
  }, []);

  const handleNext = async () => {
    if (currentStep < 3) {
      try {
        const fields = getStepFields(currentStep);
        if (fields.length > 0) await form.validateFields(fields);
        setCurrentStep(currentStep + 1);
      } catch {
        /* validation */
      }
    }
  };

  const handlePrev = () => {
    if (currentStep > 0) setCurrentStep(currentStep - 1);
  };

  const handleFinish = async () => {
    setSaving(true);
    try {
      const values = form.getFieldsValue(true);
      await api.post("/tenants", {
        name: values.client_name,
        slug: values.client_name?.toLowerCase().replace(/\s+/g, "-").slice(0, 50),
        plan: values.plan || "free",
        admin_email:
          values.contact_email ||
          `${values.client_name?.replace(/\s+/g, "").toLowerCase()}@example.com`,
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

  const handleCancel = () => {
    setModalOpen(false);
    setCurrentStep(0);
    form.resetFields();
  };

  const handleCreateTask = async () => {
    setTaskSaving(true);
    try {
      const values = await taskForm.validateFields();
      await api.post("/ops/tasks", {
        tenant_id: values.tenant_id,
        title: values.title,
        priority: values.priority || "medium",
        due_date: values.due_date?.toISOString(),
      });
      message.success("任务创建成功");
      setTaskModalOpen(false);
      taskForm.resetFields();
      fetchTasks();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      if (err.response?.data?.detail) {
        message.error(err.response.data.detail);
      }
    } finally {
      setTaskSaving(false);
    }
  };

  const handleOpenChecklist = async (clientId: string, clientName: string) => {
    setChecklistLoading(true);
    setChecklistClientName(clientName);
    setChecklistModalOpen(true);
    try {
      const { data } = await api.get(`/ops/clients/${clientId}/launch-checklist`);
      setChecklistData(data);
    } catch {
      setChecklistData(null);
    } finally {
      setChecklistLoading(false);
    }
  };

  const activeClients = clients.filter((c) => c.status === "active").length;
  const onboardingClients = clients.filter((c) => c.status === "onboarding").length;
  const totalClients = clients.length;
  const pendingTasks = tasks.filter((t) => t.status === "pending").length;

  const stepContent = [
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
        <Form.Item name="plan" label="套餐">
          <Select
            placeholder="选择套餐"
            options={[
              { value: "free", label: "免费版" },
              { value: "starter", label: "入门版" },
              { value: "pro", label: "专业版" },
              { value: "enterprise", label: "企业版" },
            ]}
          />
        </Form.Item>
      </Form>
    </div>,
    <div key="step2">
      <Form form={form} layout="vertical">
        <Form.Item
          name="brand_name"
          label="品牌名称"
          rules={[{ required: true, message: "请输入品牌名称" }]}
        >
          <Input placeholder="主品牌名称" />
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
    <div key="step3">
      <Form form={form} layout="vertical">
        <Form.Item name="page_template" label="扫码页模板">
          <Select
            placeholder="选择默认模板"
            options={[
              { value: "standard", label: "标准模板" },
              { value: "premium", label: "高级模板" },
            ]}
          />
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
    {
      title: "套餐",
      dataIndex: "plan",
      key: "plan",
      render: (p: string) => <Tag>{p}</Tag>,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "到期日",
      dataIndex: "plan_expires_at",
      key: "plan_expires_at",
      render: (v: string | null) => {
        if (!v) return "—";
        const date = v.split("T")[0];
        const daysLeft = Math.ceil(
          (new Date(v).getTime() - Date.now()) / (1000 * 60 * 60 * 24)
        );
        return daysLeft < 30 ? (
          <Tag color="red">{date}（剩余{daysLeft}天）</Tag>
        ) : (
          date
        );
      },
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v: string) => v?.split("T")[0] || "—",
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Client) => (
        <Button
          size="small"
          onClick={() => handleOpenChecklist(record.id, record.name)}
        >
          检查清单
        </Button>
      ),
    },
  ];

  const taskColumns: ColumnsType<Task> = [
    { title: "任务", dataIndex: "title", key: "title" },
    {
      title: "优先级",
      dataIndex: "priority",
      key: "priority",
      render: (p: string) => {
        const info = PRIORITY_MAP[p] || { label: p, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = TASK_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "截止日",
      dataIndex: "due_date",
      key: "due_date",
      render: (v: string | null) => v?.split("T")[0] || "—",
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          代运营工作台
        </Title>
        <Space>
          <Button
            type="default"
            icon={<PlusOutlined />}
            onClick={() => setTaskModalOpen(true)}
          >
            新建任务
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setModalOpen(true)}
          >
            初始化新客户
          </Button>
        </Space>
      </div>

      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="客户总数" value={totalClients} prefix={<TeamOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="活跃客户"
              value={activeClients}
              prefix={<UserOutlined />}
              valueStyle={{ color: "#52c41a" }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="配置中客户"
              value={onboardingClients}
              prefix={<FileTextOutlined />}
              valueStyle={{ color: "#faad14" }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="待办任务"
              value={pendingTasks}
              prefix={<CheckSquareOutlined />}
              valueStyle={{ color: "#1890ff" }}
            />
          </Card>
        </Col>
      </Row>

      <Card title="客户列表" size="small" className="mb-6">
        <Table
          columns={clientColumns}
          dataSource={clients}
          rowKey="id"
          loading={loading}
          pagination={false}
          size="small"
        />
      </Card>

      <Card title="任务列表" size="small">
        <Table
          columns={taskColumns}
          dataSource={tasks}
          rowKey="id"
          pagination={false}
          size="small"
        />
      </Card>

      {/* 初始化客户 Modal */}
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

      {/* 新建任务 Modal */}
      <Modal
        title="新建待办任务"
        open={taskModalOpen}
        onCancel={() => {
          setTaskModalOpen(false);
          taskForm.resetFields();
        }}
        onOk={handleCreateTask}
        confirmLoading={taskSaving}
        okText="创建"
      >
        <Form form={taskForm} layout="vertical">
          <Form.Item
            name="title"
            label="任务标题"
            rules={[{ required: true, message: "请输入任务标题" }]}
          >
            <Input placeholder="任务标题" />
          </Form.Item>
          <Form.Item
            name="tenant_id"
            label="关联客户"
            rules={[{ required: true, message: "请选择客户" }]}
          >
            <Select
              placeholder="选择客户"
              options={clients.map((c) => ({ value: c.id, label: c.name }))}
            />
          </Form.Item>
          <Form.Item name="priority" label="优先级">
            <Select
              placeholder="优先级"
              options={[
                { value: "low", label: "低" },
                { value: "medium", label: "中" },
                { value: "high", label: "高" },
              ]}
            />
          </Form.Item>
          <Form.Item name="due_date" label="截止日期">
            <DatePicker className="w-full" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 上线检查清单 Modal */}
      <Modal
        title={`上线检查清单 — ${checklistClientName}`}
        open={checklistModalOpen}
        onCancel={() => {
          setChecklistModalOpen(false);
          setChecklistData(null);
        }}
        footer={null}
        width={600}
      >
        {checklistLoading ? (
          <div className="py-8 text-center text-gray-400">加载中...</div>
        ) : checklistData ? (
          <>
            <div className="mb-4">
              <Progress
                percent={
                  checklistData.total_count
                    ? Math.round(
                        (checklistData.passed_count / checklistData.total_count) * 100
                      )
                    : 0
                }
                status={checklistData.ready ? "success" : "active"}
              />
              <div className="mt-1 text-sm text-gray-400">
                {checklistData.passed_count} / {checklistData.total_count} 项通过
              </div>
            </div>
            <List
              dataSource={checklistData.checks}
              renderItem={(item) => (
                <List.Item>
                  <Checkbox checked={item.passed}>
                    <span>{item.name}</span>
                    <span className="ml-2 text-sm text-gray-400">{item.detail}</span>
                  </Checkbox>
                </List.Item>
              )}
            />
          </>
        ) : (
          <div className="py-8 text-center text-gray-400">无法加载检查清单</div>
        )}
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
    default:
      return [];
  }
}
