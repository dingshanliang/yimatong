"use client";

import { useEffect, useState } from "react";
import { App, Button, Checkbox, DatePicker, Form, Input, List, Modal, Progress, Select, Space } from "antd";
import api, { extractErrorMessage } from "@/lib/api";
import type { Client, ChecklistResult } from "./types";

interface CreateTaskModalProps {
  open: boolean;
  onClose: () => void;
  clients: Client[];
  initialTenantId?: string;
  initialTitle?: string;
  onSuccess: () => void;
}

export function CreateTaskModal({ open, onClose, clients, initialTenantId, initialTitle, onSuccess }: CreateTaskModalProps) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      tenant_id: initialTenantId,
      title: initialTitle,
      priority: "medium",
    });
  }, [form, initialTenantId, initialTitle, open]);

  const handleCreate = async () => {
    setSaving(true);
    try {
      const values = await form.validateFields();
      await api.post("/ops/tasks", {
        tenant_id: values.tenant_id, title: values.title, priority: values.priority || "medium",
        ...(values.due_date ? { due_date: values.due_date.toISOString() } : {}),
      });
      message.success("任务创建成功");
      form.resetFields();
      onClose();
      onSuccess();
    } catch (e: unknown) {
      if (!(e as { errorFields?: unknown }).errorFields) message.error(extractErrorMessage(e, "创建失败"));
    } finally { setSaving(false); }
  };

  return (
    <Modal
      title="新建待办任务"
      open={open}
      onCancel={() => { form.resetFields(); onClose(); }}
      footer={null}
    >
      <Form form={form} layout="vertical">
        <Form.Item name="title" label="任务标题" rules={[{ required: true, message: "请输入任务标题" }]}><Input placeholder="任务标题" /></Form.Item>
        <Form.Item name="tenant_id" label="关联客户" rules={[{ required: true, message: "请选择客户" }]}>
          <Select placeholder="选择客户" options={clients.map((c) => ({ value: c.id, label: c.name }))} />
        </Form.Item>
        <Form.Item name="priority" label="优先级">
          <Select placeholder="优先级" options={[{ value: "low", label: "低" }, { value: "medium", label: "中" }, { value: "high", label: "高" }]} />
        </Form.Item>
        <Form.Item name="due_date" label="截止日期"><DatePicker className="w-full" /></Form.Item>
      </Form>
      <div className="mt-6 text-right">
        <Space>
          <Button onClick={() => { form.resetFields(); onClose(); }}>取消</Button>
          <Button data-testid="agency-task-create-submit" type="primary" loading={saving} onClick={handleCreate}>创建</Button>
        </Space>
      </div>
    </Modal>
  );
}

interface ChecklistModalProps {
  open: boolean;
  clientName: string;
  onClose: () => void;
  data: ChecklistResult | null;
  loading: boolean;
}

export function ChecklistModal({ open, clientName, onClose, data, loading }: ChecklistModalProps) {
  return (
    <Modal title={`上线检查清单 — ${clientName}`} open={open} onCancel={onClose} footer={null} width={600}>
      {loading ? <div className="py-8 text-center text-gray-400">加载中...</div>
        : data ? (
          <>
            <div className="mb-4">
              <Progress percent={data.total_count ? Math.round((data.passed_count / data.total_count) * 100) : 0} status={data.ready ? "success" : "active"} />
              <div className="mt-1 text-sm text-gray-400">{data.passed_count} / {data.total_count} 项通过</div>
            </div>
            <List dataSource={data.checks} renderItem={(item) => (
              <List.Item><Checkbox checked={item.passed}><span>{item.name}</span><span className="ml-2 text-sm text-gray-400">{item.detail}</span></Checkbox></List.Item>
            )} />
          </>
        ) : <div className="py-8 text-center text-gray-400">无法加载检查清单</div>
      }
    </Modal>
  );
}
