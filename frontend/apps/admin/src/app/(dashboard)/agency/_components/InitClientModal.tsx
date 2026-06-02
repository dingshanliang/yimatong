"use client";

import { useState } from "react";
import { Alert, App, Button, Form, Input, Modal, Select, Steps } from "antd";
import api, { extractErrorMessage } from "@/lib/api";

interface InitClientModalProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

const PLAN_OPTIONS = [
  { value: "free", label: "免费版" }, { value: "starter", label: "入门版" },
  { value: "pro", label: "专业版" }, { value: "enterprise", label: "企业版" },
];

const INDUSTRY_OPTIONS = [
  { value: "food", label: "食品" }, { value: "agriculture", label: "农产品" },
  { value: "beverage", label: "饮料" }, { value: "daily", label: "日用品" }, { value: "other", label: "其他" },
];

const TEMPLATE_OPTIONS = [
  { value: "standard", label: "标准模板" }, { value: "premium", label: "高级模板" },
];

function getStepFields(step: number): string[] {
  switch (step) {
    case 0: return ["client_name", "contact_name", "contact_phone"];
    case 1: return ["brand_name"];
    default: return [];
  }
}

export function InitClientModal({ open, onClose, onSuccess }: InitClientModalProps) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [currentStep, setCurrentStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const [credentials, setCredentials] = useState<{ email: string; password: string } | null>(null);

  const generatePassword = () => `Ymt-${Math.random().toString(36).slice(2, 8)}${Date.now().toString().slice(-6)}`;

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
      const initialPassword = generatePassword();
      const { data } = await api.post("/tenants", {
        name: values.client_name,
        slug: `client-${Date.now()}`,
        plan: values.plan || "free",
        admin_email: values.contact_email || `${values.client_name?.replace(/\s+/g, "").toLowerCase()}@example.com`,
        admin_name: values.contact_name || "管理员",
        admin_password: initialPassword,
      });
      setCredentials({
        email: data?.admin_email || values.contact_email || `${values.client_name?.replace(/\s+/g, "").toLowerCase()}@example.com`,
        password: data?.initial_password || initialPassword,
      });
      message.success("客户初始化成功");
      onSuccess();
    } catch (e: unknown) { message.error(extractErrorMessage(e, "初始化失败")); }
    finally { setSaving(false); }
  };

  const handleCancel = () => { setCurrentStep(0); setCredentials(null); form.resetFields(); onClose(); };

  const stepContent = [
    <div key="step1">
      <Form form={form} layout="vertical">
        <Form.Item name="client_name" label="客户名称" rules={[{ required: true, message: "请输入客户名称" }]}><Input placeholder="客户公司名称" /></Form.Item>
        <Form.Item name="contact_name" label="联系人" rules={[{ required: true, message: "请输入联系人" }]}><Input placeholder="联系人姓名" /></Form.Item>
        <Form.Item name="contact_phone" label="联系电话" rules={[{ required: true, message: "请输入联系电话" }]}><Input placeholder="联系电话" /></Form.Item>
        <Form.Item name="contact_email" label="联系邮箱"><Input placeholder="联系邮箱" /></Form.Item>
        <Form.Item name="plan" label="套餐"><Select placeholder="选择套餐" options={PLAN_OPTIONS} /></Form.Item>
      </Form>
    </div>,
    <div key="step2">
      <Form form={form} layout="vertical">
        <Form.Item name="brand_name" label="品牌名称" rules={[{ required: true, message: "请输入品牌名称" }]}><Input placeholder="主品牌名称" /></Form.Item>
        <Form.Item name="industry" label="所属行业"><Select placeholder="选择行业" options={INDUSTRY_OPTIONS} /></Form.Item>
      </Form>
    </div>,
    <div key="step3">
      <Form form={form} layout="vertical">
        <Form.Item name="page_template" label="扫码页模板"><Select placeholder="选择默认模板" options={TEMPLATE_OPTIONS} /></Form.Item>
        <Form.Item name="notes" label="备注"><Input.TextArea rows={3} placeholder="特殊需求或备注" /></Form.Item>
      </Form>
    </div>,
    <div key="step4" className="py-6 text-center">
      <div className="mb-2 text-lg font-medium">配置确认</div>
      <div className="text-gray-400">请确认以上配置信息无误，点击完成开始初始化</div>
      {credentials && (
        <Alert
          className="mt-4 text-left"
          data-testid="agency-init-credentials"
          type="success"
          showIcon
          message="客户管理员账号已生成"
          description={`登录邮箱：${credentials.email}，临时密码：${credentials.password}`}
        />
      )}
    </div>,
  ];

  return (
    <Modal title="初始化客户配置" open={open} onCancel={handleCancel} width={600}
      footer={currentStep < 3
        ? [<Button key="cancel" onClick={handleCancel}>取消</Button>,
           currentStep > 0 && <Button key="prev" onClick={handlePrev}>上一步</Button>,
           <Button key="next" type="primary" onClick={handleNext}>下一步</Button>]
        : [<Button key="prev" onClick={handlePrev}>上一步</Button>,
           <Button key="finish" type="primary" onClick={handleFinish} loading={saving}>完成初始化</Button>]
      }>
      <Steps current={currentStep} items={[{ title: "基础信息" }, { title: "产品配置" }, { title: "页面配置" }, { title: "完成" }]} className="mb-6" size="small" />
      {stepContent[currentStep]}
    </Modal>
  );
}
