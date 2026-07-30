"use client";

import { useEffect, useState } from "react";
import {
  App,
  Button,
  DatePicker,
  Form,
  Input,
  List,
  Modal,
  Progress,
  Select,
  Space,
} from "antd";
import {
  CheckCircleFilled,
  CloseCircleFilled,
  ReloadOutlined,
} from "@ant-design/icons";
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

export function CreateTaskModal({
  open,
  onClose,
  clients,
  initialTenantId,
  initialTitle,
  onSuccess,
}: CreateTaskModalProps) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [allClients, setAllClients] = useState<Client[]>([]);
  const [clientsLoading, setClientsLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      tenant_id: initialTenantId,
      title: initialTitle,
      priority: "medium",
    });
    setClientsLoading(true);
    api
      .get("/tenants", { params: { page: 1, page_size: 200 } })
      .then(({ data }) => {
        const items = data?.items ?? [];
        setAllClients(Array.isArray(items) ? items : []);
      })
      .catch(() => setAllClients([]))
      .finally(() => setClientsLoading(false));
  }, [form, initialTenantId, initialTitle, open]);

  const clientOptions =
    allClients.length > 0
      ? allClients.map((c) => ({ value: c.id, label: c.name }))
      : clients.map((c) => ({ value: c.id, label: c.name }));

  const handleCreate = async () => {
    setSaving(true);
    try {
      const values = await form.validateFields();
      await api.post("/ops/tasks", {
        tenant_id: values.tenant_id,
        title: values.title,
        description: values.description || null,
        priority: values.priority || "medium",
        ...(values.due_date ? { due_date: values.due_date.toISOString() } : {}),
      });
      message.success("任务创建成功");
      form.resetFields();
      onClose();
      onSuccess();
    } catch (e: unknown) {
      if (!(e as { errorFields?: unknown }).errorFields)
        message.error(extractErrorMessage(e, "创建失败"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title="新建待办任务"
      open={open}
      onCancel={() => {
        form.resetFields();
        onClose();
      }}
      footer={null}
    >
      <Form form={form} layout="vertical">
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
            loading={clientsLoading}
            showSearch
            optionFilterProp="label"
            options={clientOptions}
          />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <Input.TextArea rows={2} placeholder="任务描述（可选）" />
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
      <div className="mt-6 text-right">
        <Space>
          <Button
            onClick={() => {
              form.resetFields();
              onClose();
            }}
          >
            取消
          </Button>
          <Button
            data-testid="agency-task-create-submit"
            type="primary"
            loading={saving}
            onClick={handleCreate}
          >
            创建
          </Button>
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
  onRetry: () => void;
}

export function ChecklistModal({
  open,
  clientName,
  onClose,
  data,
  loading,
  onRetry,
}: ChecklistModalProps) {
  return (
    <Modal
      title={`上线检查清单 — ${clientName}`}
      open={open}
      onCancel={onClose}
      footer={null}
      width={600}
    >
      {loading ? (
        <div className="py-8 text-center text-text-muted">加载中...</div>
      ) : data ? (
        <>
          <div className="mb-4">
            <Progress
              percent={
                data.total_count
                  ? Math.round((data.passed_count / data.total_count) * 100)
                  : 0
              }
              status={data.ready ? "success" : "active"}
            />
            <div className="mt-1 text-sm text-text-muted">
              {data.passed_count} / {data.total_count} 项通过
            </div>
          </div>
          <List
            dataSource={data.checks}
            renderItem={(item) => (
              <List.Item>
                <div className="flex items-center gap-2 w-full">
                  {item.passed ? (
                    <CheckCircleFilled
                      className="text-lg"
                      style={{ color: "var(--ymt-color-feedback-success)" }}
                    />
                  ) : (
                    <CloseCircleFilled
                      className="text-lg"
                      style={{ color: "var(--ymt-color-feedback-danger)" }}
                    />
                  )}
                  <span>{item.name}</span>
                  <span className="ml-2 text-sm text-text-muted">
                    {item.detail}
                  </span>
                </div>
              </List.Item>
            )}
          />
        </>
      ) : (
        <div className="py-8 text-center">
          <div className="mb-4 text-text-muted">无法加载检查清单</div>
          <Button icon={<ReloadOutlined />} onClick={onRetry}>
            重试
          </Button>
        </div>
      )}
    </Modal>
  );
}
