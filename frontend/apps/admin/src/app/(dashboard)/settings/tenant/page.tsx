"use client";

import { useEffect, useState, useCallback } from "react";
import { App, Button, Descriptions, Divider, Form, Input, Space, Spin, Switch, Typography } from "antd";
import { EditOutlined, SaveOutlined } from "@ant-design/icons";
import { useAuthStore } from "@/lib/auth";
import api from "@/lib/api";

const { Title, Text } = Typography;

interface Tenant {
  id: string;
  name: string;
  slug: string;
  plan: string;
  contact_email: string;
  enabled_features: Record<string, boolean> | null;
  created_at: string;
}

interface TenantApiResponse {
  id: string;
  name: string;
  slug: string;
  status: string;
  plan: string;
  plan_expires_at: string | null;
  quota: Record<string, unknown> | null;
  compliance_settings: Record<string, unknown> | null;
  onboarding_progress: Record<string, unknown> | null;
  enabled_features: Record<string, boolean> | null;
  created_at: string | null;
}

const PLAN_MAP: Record<string, string> = {
  free: "免费版",
  starter: "入门版",
  pro: "专业版",
  enterprise: "企业版",
};

/** 功能开关配置 */
const FEATURE_FLAGS = [
  {
    key: "cash_red_packet",
    label: "现金红包",
    description: "允许创建和管理现金红包权益（微信支付商家转账到零钱）",
  },
] as const;

export default function TenantSettingsPage() {
  const { message } = App.useApp();
  const { user } = useAuthStore();
  const tenantId = user?.tenant_id;

  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [togglingFeature, setTogglingFeature] = useState<string | null>(null);
  const [form] = Form.useForm();

  const fetchTenant = useCallback(async () => {
    if (!tenantId) return;
    setLoading(true);
    try {
      const { data } = await api.get<TenantApiResponse>(`/tenants/${tenantId}`);
      setTenant({
        id: data.id,
        name: data.name,
        slug: data.slug,
        plan: data.plan,
        contact_email: (data.compliance_settings as Record<string, string>)?.contact_email ?? "",
        enabled_features: data.enabled_features ?? {},
        created_at: data.created_at ?? new Date().toISOString(),
      });
    } catch {
      message.error("加载租户信息失败");
    } finally {
      setLoading(false);
    }
  }, [tenantId, message]);

  useEffect(() => {
    fetchTenant();
  }, [fetchTenant]);

  const startEditing = () => {
    if (!tenant) return;
    form.setFieldsValue({
      name: tenant.name,
      contact_email: tenant.contact_email,
    });
    setEditing(true);
  };

  const handleSave = async (values: {
    name: string;
    contact_email: string;
  }) => {
    if (!tenantId) return;
    setSaving(true);
    try {
      const { data } = await api.patch<TenantApiResponse>(`/tenants/${tenantId}`, {
        name: values.name,
        compliance_settings: { contact_email: values.contact_email },
      });
      setTenant({
        id: data.id,
        name: data.name,
        slug: data.slug,
        plan: data.plan,
        contact_email: (data.compliance_settings as Record<string, string>)?.contact_email ?? "",
        enabled_features: data.enabled_features ?? {},
        created_at: data.created_at ?? new Date().toISOString(),
      });
      message.success("租户信息已更新");
      setEditing(false);
    } catch {
      message.error("更新失败");
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    setEditing(false);
    form.resetFields();
  };

  /** 切换功能开关 */
  const handleFeatureToggle = async (featureKey: string, enabled: boolean) => {
    if (!tenantId || !tenant) return;
    setTogglingFeature(featureKey);
    const currentFeatures = tenant.enabled_features ?? {};
    const newFeatures = { ...currentFeatures, [featureKey]: enabled };

    try {
      const { data } = await api.patch<TenantApiResponse>(`/tenants/${tenantId}`, {
        enabled_features: newFeatures,
      });
      setTenant({
        ...tenant,
        enabled_features: data.enabled_features ?? {},
      });
      message.success(`${enabled ? "已启用" : "已关闭"} ${FEATURE_FLAGS.find((f) => f.key === featureKey)?.label ?? featureKey}`);
    } catch {
      message.error("更新功能开关失败");
    } finally {
      setTogglingFeature(null);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Spin size="large" />
      </div>
    );
  }

  if (!tenant) {
    return (
      <div>
        <Title level={4}>租户设置</Title>
        <div className="text-text-muted">无法加载租户信息</div>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          租户设置
        </Title>
        {!editing && (
          <Button
            type="primary"
            icon={<EditOutlined />}
            onClick={startEditing}
          >
            编辑
          </Button>
        )}
      </div>

      {editing ? (
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSave}
          className="max-w-lg"
        >
          <Form.Item
            name="name"
            label="租户名称"
            rules={[{ required: true, message: "请输入租户名称" }]}
          >
            <Input placeholder="租户名称" />
          </Form.Item>
          <Form.Item
            name="contact_email"
            label="联系邮箱"
            rules={[
              { required: true, message: "请输入联系邮箱" },
              { type: "email", message: "请输入有效的邮箱地址" },
            ]}
          >
            <Input placeholder="联系邮箱" />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button
                type="primary"
                htmlType="submit"
                icon={<SaveOutlined />}
                loading={saving}
              >
                保存
              </Button>
              <Button onClick={handleCancel}>取消</Button>
            </Space>
          </Form.Item>
        </Form>
      ) : (
        <Descriptions bordered column={1} className="max-w-lg">
          <Descriptions.Item label="租户名称">
            {tenant.name}
          </Descriptions.Item>
          <Descriptions.Item label="Slug">
            <span className="text-text-muted">{tenant.slug}</span>
          </Descriptions.Item>
          <Descriptions.Item label="套餐">
            {PLAN_MAP[tenant.plan] || tenant.plan}
          </Descriptions.Item>
          <Descriptions.Item label="联系邮箱">
            {tenant.contact_email}
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {new Date(tenant.created_at).toLocaleString("zh-CN")}
          </Descriptions.Item>
        </Descriptions>
      )}

      <Divider />

      <div className="max-w-lg">
        <Title level={5} className="!mb-2">
          功能开关
        </Title>
        <Text type="secondary" className="block mb-4">
          管理租户可使用的高级功能
        </Text>

        <div className="space-y-4">
          {FEATURE_FLAGS.map((feature) => {
            const enabled = tenant.enabled_features?.[feature.key] ?? false;
            return (
              <div
                key={feature.key}
                className="flex items-start justify-between rounded-lg border border-border-subtle p-4"
              >
                <div>
                  <div className="font-medium">{feature.label}</div>
                  <div className="mt-1 text-sm text-text-muted">
                    {feature.description}
                  </div>
                </div>
                <Switch
                  checked={enabled}
                  onChange={(checked) => handleFeatureToggle(feature.key, checked)}
                  checkedChildren="开"
                  unCheckedChildren="关"
                  loading={togglingFeature === feature.key}
                />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
