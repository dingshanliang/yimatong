"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, App, Button, Descriptions, Form, Input, Modal, Select, Space, Steps, Typography } from "antd";
import { CopyOutlined } from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";
import type { IndustryTemplate } from "./types";

interface InitClientModalProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

const INDUSTRY_OPTIONS = [
  { value: "food", label: "食品" },
  { value: "agriculture", label: "农产品" },
  { value: "beverage", label: "饮料" },
  { value: "daily", label: "日用品" },
  { value: "other", label: "其他" },
];

const PLAN_OPTIONS = [
  { value: "free", label: "免费版" },
  { value: "starter", label: "入门版" },
  { value: "pro", label: "专业版" },
  { value: "enterprise", label: "企业版" },
];

function getStepFields(step: number): string[] {
  switch (step) {
    case 0: return ["client_name", "contact_name", "contact_phone"];
    case 1: return ["brand_name"];
    case 2: return [];
    default: return [];
  }
}

export function InitClientModal({ open, onClose, onSuccess }: InitClientModalProps) {
  const { message, modal } = App.useApp();
  const [form] = Form.useForm();
  const [currentStep, setCurrentStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const [credentials, setCredentials] = useState<{ email: string; password: string } | null>(null);
  const [templates, setTemplates] = useState<IndustryTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [passwordCopied, setPasswordCopied] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTemplatesLoading(true);
    api.get("/industry-templates")
      .then(({ data }) => setTemplates(Array.isArray(data) ? data : []))
      .catch(() => setTemplates([]))
      .finally(() => setTemplatesLoading(false));
  }, [open]);

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

  const handleCopyPassword = useCallback(async () => {
    if (!credentials) return;
    try {
      await navigator.clipboard.writeText(credentials.password);
      setPasswordCopied(true);
      message.success("密码已复制到剪贴板");
    } catch {
      message.error("复制失败，请手动选中复制");
    }
  }, [credentials, message]);

  const handleFinish = async () => {
    setSaving(true);
    try {
      const values = form.getFieldsValue(true);
      const initialPassword = generatePassword();
      const { data } = await api.post("/tenants", {
        name: values.client_name,
        plan: values.plan || "free",
        admin_email: values.contact_email || `${values.client_name?.replace(/\s+/g, "").toLowerCase()}@example.com`,
        admin_name: values.contact_name || "管理员",
        admin_password: initialPassword,
        industry: values.industry,
        notes: values.notes,
        template_id: values.template_id ?? null,
      });
      setCredentials({
        email: data?.admin_email || values.contact_email || `${values.client_name?.replace(/\s+/g, "").toLowerCase()}@example.com`,
        password: data?.initial_password || initialPassword,
      });
      message.success("客户初始化成功");
      onSuccess();
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, "初始化失败"));
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    if (credentials && !passwordCopied) {
      modal.confirm({
        title: "确认关闭？",
        content: "管理员密码尚未复制，关闭后将无法再次查看。请确认已妥善保存密码。",
        okText: "确认关闭",
        okButtonProps: { danger: true },
        onOk: () => resetAndClose(),
      });
      return;
    }
    resetAndClose();
  };

  const resetAndClose = () => {
    setCurrentStep(0);
    setCredentials(null);
    setPasswordCopied(false);
    form.resetFields();
    onClose();
  };

  const values = form.getFieldsValue(true);

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
        <Form.Item name="industry" label="所属行业">
          <Select placeholder="选择行业" options={INDUSTRY_OPTIONS} />
        </Form.Item>
        <Form.Item name="plan" label="套餐">
          <Select placeholder="选择套餐" options={PLAN_OPTIONS} />
        </Form.Item>
      </Form>
    </div>,
    <div key="step2">
      <Form form={form} layout="vertical">
        <Form.Item name="brand_name" label="品牌名称" rules={[{ required: true, message: "请输入品牌名称" }]}>
          <Input placeholder="主品牌名称" />
        </Form.Item>
        <Form.Item name="template_id" label="扫码页模板">
          <Select
            placeholder="选择行业模板（可选）"
            loading={templatesLoading}
            allowClear
            options={templates.map((t) => ({ value: t.id, label: `${t.name} — ${t.description}` }))}
          />
        </Form.Item>
      </Form>
    </div>,
    <div key="step3">
      <Form form={form} layout="vertical">
        <Form.Item name="notes" label="备注">
          <Input.TextArea rows={4} placeholder="记录客户特殊需求、上线时间要求等" />
        </Form.Item>
      </Form>
    </div>,
    <div key="step4" className="py-4">
      {credentials ? (
        <div>
          <Alert
            className="mb-4"
            data-testid="agency-init-credentials"
            type="success"
            showIcon
            message="客户管理员账号已生成"
            description={
              <Space direction="vertical" size={4}>
                <span>登录邮箱：{credentials.email}</span>
                <span>
                  临时密码：{credentials.password}
                  <Button size="small" type="link" icon={<CopyOutlined />} onClick={handleCopyPassword}>
                    {passwordCopied ? "已复制" : "复制密码"}
                  </Button>
                </span>
              </Space>
            }
          />
          <Typography.Text type="secondary">请将登录信息发送给客户，客户首次登录后建议修改密码。</Typography.Text>
        </div>
      ) : (
        <Descriptions column={1} size="small" bordered title="配置确认">
          <Descriptions.Item label="客户名称">{values.client_name || "—"}</Descriptions.Item>
          <Descriptions.Item label="联系人">{values.contact_name || "—"}</Descriptions.Item>
          <Descriptions.Item label="联系电话">{values.contact_phone || "—"}</Descriptions.Item>
          <Descriptions.Item label="联系邮箱">{values.contact_email || "—"}</Descriptions.Item>
          <Descriptions.Item label="行业">{INDUSTRY_OPTIONS.find((o) => o.value === values.industry)?.label || "未选择"}</Descriptions.Item>
          <Descriptions.Item label="套餐">{PLAN_OPTIONS.find((o) => o.value === values.plan)?.label || "免费版"}</Descriptions.Item>
          <Descriptions.Item label="品牌名称">{values.brand_name || "—"}</Descriptions.Item>
          <Descriptions.Item label="扫码页模板">
            {values.template_id != null ? templates.find((t) => t.id === values.template_id)?.name || "未选择" : "未选择"}
          </Descriptions.Item>
          {values.notes && <Descriptions.Item label="备注">{values.notes}</Descriptions.Item>}
        </Descriptions>
      )}
    </div>,
  ];

  return (
    <Modal
      title="初始化客户配置"
      open={open}
      onCancel={handleCancel}
      width={640}
      footer={
        currentStep < 3
          ? [
              <Button key="cancel" onClick={handleCancel}>取消</Button>,
              currentStep > 0 && <Button key="prev" onClick={handlePrev}>上一步</Button>,
              <Button key="next" type="primary" onClick={handleNext}>下一步</Button>,
            ]
          : credentials
            ? [<Button key="close" type="primary" onClick={handleCancel}>完成</Button>]
            : [
                <Button key="prev" onClick={handlePrev}>上一步</Button>,
                <Button key="finish" type="primary" onClick={handleFinish} loading={saving}>完成初始化</Button>,
              ]
      }
    >
      <Steps
        current={currentStep}
        items={[{ title: "基础信息" }, { title: "产品配置" }, { title: "备注" }, { title: "确认" }]}
        className="mb-6"
        size="small"
      />
      {stepContent[currentStep]}
    </Modal>
  );
}
