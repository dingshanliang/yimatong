"use client";

import { useState } from "react";
import {
  App,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  DatePicker,
  Modal,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Typography,
} from "antd";
import {
  ArrowLeftOutlined,
  EditOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
} from "@ant-design/icons";
import { useRouter, useParams } from "next/navigation";
import dayjs from "dayjs";
import useSWR from "swr";
import api from "@/lib/api";
import { extractErrorMessage } from "@/lib/api";
import { STATUS_MAP, PLAN_MAP } from "@/lib/constants";

const { Title, Text } = Typography;

interface TenantDetail {
  id: string;
  name: string;
  slug: string;
  status: "active" | "suspended" | "terminated";
  plan: "free" | "starter" | "pro" | "enterprise";
  plan_expires_at: string | null;
  industry: string | null;
  notes: string | null;
  quota: Record<string, number> | null;
  enabled_features: Record<string, boolean> | null;
  created_at: string;
  account_count: number;
  organization_count: number;
}

export default function TenantDetailPage() {
  const router = useRouter();
  const params = useParams();
  const { modal, message } = App.useApp();
  const tenantId = params.id as string;

  const [editOpen, setEditOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const { data, mutate, isLoading } = useSWR<TenantDetail>(`/platform/tenants/${tenantId}`);
  const [form] = Form.useForm();

  if (isLoading) {
    return <div style={{ textAlign: "center", padding: 80 }}><Spin size="large" /></div>;
  }

  if (!data) {
    return <div style={{ textAlign: "center", padding: 80 }}><Text type="secondary">租户不存在</Text></div>;
  }

  const handleStatusChange = (newStatus: string) => {
    const labels: Record<string, string> = { suspended: "暂停", active: "恢复", terminated: "终止" };
    modal.confirm({
      title: `确认${labels[newStatus]}租户`,
      content: `确定要${labels[newStatus]}租户「${data.name}」吗？`,
      onOk: async () => {
        try {
          await api.patch(`/platform/tenants/${tenantId}/status`, { status: newStatus });
          message.success("操作成功");
          mutate();
        } catch (err) {
          message.error(extractErrorMessage(err, "操作失败"));
        }
      },
    });
  };

  const handleEdit = async (values: Record<string, unknown>) => {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {};
      if (values.name) payload.name = values.name;
      if (values.plan) payload.plan = values.plan;
      if (values.industry) payload.industry = values.industry;
      if (values.notes) payload.notes = values.notes;
      if (values.plan_expires_at) payload.plan_expires_at = (values.plan_expires_at as dayjs.Dayjs).toISOString();

      await api.patch(`/platform/tenants/${tenantId}`, payload);
      message.success("更新成功");
      setEditOpen(false);
      mutate();
    } catch (err) {
      message.error(extractErrorMessage(err, "更新失败"));
    } finally {
      setSaving(false);
    }
  };

  const statusInfo = STATUS_MAP[data.status] ?? { color: "default", label: data.status };
  const planInfo = PLAN_MAP[data.plan] ?? { color: "default", label: data.plan };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => router.push("/tenants")} />
          <Title level={4} style={{ margin: 0 }}>{data.name}</Title>
          <Tag color={statusInfo.color}>{statusInfo.label}</Tag>
          <Tag color={planInfo.color}>{planInfo.label}</Tag>
        </Space>
        <Space>
          {data.status === "active" && (
            <Button icon={<PauseCircleOutlined />} onClick={() => handleStatusChange("suspended")}>暂停</Button>
          )}
          {data.status === "suspended" && (
            <Button icon={<PlayCircleOutlined />} type="primary" onClick={() => handleStatusChange("active")}>恢复</Button>
          )}
          <Button icon={<EditOutlined />} onClick={() => { form.setFieldsValue({ ...data, plan_expires_at: data.plan_expires_at ? dayjs(data.plan_expires_at) : undefined }); setEditOpen(true); }}>编辑</Button>
        </Space>
      </div>

      <Tabs
        defaultActiveKey="info"
        items={[
          {
            key: "info",
            label: "基本信息",
            children: (
              <Card>
                <Descriptions bordered column={2}>
                  <Descriptions.Item label="租户 ID">{data.id}</Descriptions.Item>
                  <Descriptions.Item label="Slug">{data.slug}</Descriptions.Item>
                  <Descriptions.Item label="套餐">{planInfo.label}</Descriptions.Item>
                  <Descriptions.Item label="状态">{statusInfo.label}</Descriptions.Item>
                  <Descriptions.Item label="行业">{data.industry ?? "-"}</Descriptions.Item>
                  <Descriptions.Item label="过期时间">{data.plan_expires_at ? dayjs(data.plan_expires_at).format("YYYY-MM-DD") : "永久"}</Descriptions.Item>
                  <Descriptions.Item label="创建时间">{dayjs(data.created_at).format("YYYY-MM-DD HH:mm")}</Descriptions.Item>
                  <Descriptions.Item label="备注">{data.notes ?? "-"}</Descriptions.Item>
                  <Descriptions.Item label="账号数">{data.account_count}</Descriptions.Item>
                  <Descriptions.Item label="组织数">{data.organization_count}</Descriptions.Item>
                </Descriptions>
              </Card>
            ),
          },
          {
            key: "quota",
            label: "额度配置",
            children: (
              <Card>
                {data.quota && Object.keys(data.quota).length > 0 ? (
                  <Descriptions bordered column={2}>
                    {Object.entries(data.quota).map(([key, value]) => (
                      <Descriptions.Item key={key} label={key}>{value}</Descriptions.Item>
                    ))}
                  </Descriptions>
                ) : (
                  <Text type="secondary">暂无额度配置</Text>
                )}
              </Card>
            ),
          },
          {
            key: "features",
            label: "功能开关",
            children: (
              <Card>
                {data.enabled_features && Object.keys(data.enabled_features).length > 0 ? (
                  <Descriptions bordered column={2}>
                    {Object.entries(data.enabled_features).map(([key, value]) => (
                      <Descriptions.Item key={key} label={key}>
                        <Tag color={value ? "green" : "default"}>{value ? "已启用" : "已禁用"}</Tag>
                      </Descriptions.Item>
                    ))}
                  </Descriptions>
                ) : (
                  <Text type="secondary">暂无功能开关配置</Text>
                )}
              </Card>
            ),
          },
        ]}
      />

      <Modal
        title="编辑租户"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={saving}
        width={520}
      >
        <Form form={form} layout="vertical" onFinish={handleEdit}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="plan" label="套餐">
            <Select options={Object.entries(PLAN_MAP).map(([k, v]) => ({ value: k, label: v.label }))} />
          </Form.Item>
          <Form.Item name="industry" label="行业">
            <Input />
          </Form.Item>
          <Form.Item name="plan_expires_at" label="过期时间">
            <DatePicker style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="notes" label="备注">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
