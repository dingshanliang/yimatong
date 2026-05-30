"use client";

import { useEffect, useState, useCallback } from "react";
import { App, Button, Descriptions, Form, Input, Space, Spin, Typography } from "antd";
import { EditOutlined, SaveOutlined } from "@ant-design/icons";
import { useAuthStore } from "@/lib/auth";
import api from "@/lib/api";

const { Title } = Typography;

interface Tenant {
  id: string;
  name: string;
  slug: string;
  plan: string;
  contact_email: string;
  created_at: string;
}

const PLAN_MAP: Record<string, string> = {
  free: "免费版",
  starter: "入门版",
  professional: "专业版",
  enterprise: "企业版",
};

export default function TenantSettingsPage() {
  const { message } = App.useApp();
  const { user } = useAuthStore();
  const tenantId = user?.tenant_id;

  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const fetchTenant = useCallback(async () => {
    if (!tenantId) return;
    setLoading(true);
    try {
      const { data } = await api.get(`/tenants/${tenantId}`);
      setTenant(data);
    } catch {
      message.error("加载租户信息失败");
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

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
      const { data } = await api.patch(`/tenants/${tenantId}`, values);
      setTenant(data);
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
        <div className="text-gray-400">无法加载租户信息</div>
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
            <span className="text-gray-500">{tenant.slug}</span>
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
    </div>
  );
}
