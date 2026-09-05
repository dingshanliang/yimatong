"use client";

import { useEffect, useState, useCallback } from "react";
import {
  App,
  Button,
  Descriptions,
  Divider,
  Form,
  Input,
  Space,
  Spin,
  Switch,
  Typography,
} from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  SaveOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
} from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";
import { useCategories } from "@/lib/use-categories";

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
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  // 品类管理
  const { categories: savedCategories, mutate: mutateCategories } =
    useCategories();
  const [localCategories, setLocalCategories] = useState<string[]>([]);
  const [newCategory, setNewCategory] = useState("");
  const [categoriesDirty, setCategoriesDirty] = useState(false);
  const [savingCategories, setSavingCategories] = useState(false);

  // 同步远程品类到本地编辑状态
  useEffect(() => {
    if (savedCategories.length > 0 || localCategories.length === 0) {
      setLocalCategories(savedCategories);
      setCategoriesDirty(false);
    }
  }, [savedCategories]);

  const addCategory = () => {
    const trimmed = newCategory.trim();
    if (!trimmed) return;
    if (
      localCategories.some((c) => c.toLowerCase() === trimmed.toLowerCase())
    ) {
      message.warning("该品类已存在");
      return;
    }
    if (trimmed.length > 20) {
      message.warning("品类名称不能超过 20 个字符");
      return;
    }
    setLocalCategories((prev) => [...prev, trimmed]);
    setNewCategory("");
    setCategoriesDirty(true);
  };

  const removeCategory = (index: number) => {
    setLocalCategories((prev) => prev.filter((_, i) => i !== index));
    setCategoriesDirty(true);
  };

  const moveCategory = (index: number, direction: "up" | "down") => {
    setLocalCategories((prev) => {
      const next = [...prev];
      const target = direction === "up" ? index - 1 : index + 1;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    setCategoriesDirty(true);
  };

  const saveCategories = async () => {
    setSavingCategories(true);
    try {
      await api.patch("/tenants/me", { categories: localCategories });
      mutateCategories();
      setCategoriesDirty(false);
      message.success("品类配置已保存");
    } catch (error) {
      message.error(extractErrorMessage(error, "保存品类失败"));
    } finally {
      setSavingCategories(false);
    }
  };

  const fetchTenant = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get<TenantApiResponse>("/tenants/me");
      setTenant({
        id: data.id,
        name: data.name,
        slug: data.slug,
        plan: data.plan,
        contact_email:
          (data.compliance_settings as Record<string, string>)?.contact_email ??
          "",
        enabled_features: data.enabled_features ?? {},
        created_at: data.created_at ?? new Date().toISOString(),
      });
    } catch {
      message.error("加载租户信息失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

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
    setSaving(true);
    try {
      const { data } = await api.patch<TenantApiResponse>("/tenants/me", {
        name: values.name,
        contact_email: values.contact_email,
      });
      setTenant({
        id: data.id,
        name: data.name,
        slug: data.slug,
        plan: data.plan,
        contact_email:
          (data.compliance_settings as Record<string, string>)?.contact_email ??
          "",
        enabled_features: data.enabled_features ?? {},
        created_at: data.created_at ?? new Date().toISOString(),
      });
      message.success("租户信息已更新");
      setEditing(false);
    } catch (error) {
      message.error(extractErrorMessage(error, "更新失败"));
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
          <Button type="primary" icon={<EditOutlined />} onClick={startEditing}>
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
          <Descriptions.Item label="租户名称">{tenant.name}</Descriptions.Item>
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
          高级功能由平台根据套餐统一开通
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
                  checkedChildren="开"
                  unCheckedChildren="关"
                  disabled
                />
              </div>
            );
          })}
        </div>
      </div>

      <Divider />

      <div className="max-w-lg">
        <Title level={5} className="!mb-2">
          品类管理
        </Title>
        <Text type="secondary" className="block mb-4">
          管理产品品类选项，用于产品录入和 AI 助手中的品类下拉
        </Text>

        <div className="mb-3 flex gap-2">
          <Input
            placeholder="输入品类名称"
            value={newCategory}
            onChange={(e) => setNewCategory(e.target.value)}
            onPressEnter={addCategory}
            maxLength={20}
            className="flex-1"
          />
          <Button
            icon={<PlusOutlined />}
            onClick={addCategory}
            disabled={!newCategory.trim()}
          >
            添加
          </Button>
        </div>

        {localCategories.length === 0 ? (
          <div className="py-4 text-center text-text-muted">
            暂无品类，请添加
          </div>
        ) : (
          <div className="space-y-1">
            {localCategories.map((cat, idx) => (
              <div
                key={`${cat}-${idx}`}
                className="flex items-center justify-between rounded border border-border-subtle px-3 py-2"
              >
                <span className="flex-1">
                  <Text type="secondary" className="mr-2 text-xs">
                    {idx + 1}.
                  </Text>
                  {cat}
                </span>
                <Space size={4}>
                  <Button
                    type="text"
                    size="small"
                    icon={<ArrowUpOutlined />}
                    disabled={idx === 0}
                    onClick={() => moveCategory(idx, "up")}
                  />
                  <Button
                    type="text"
                    size="small"
                    icon={<ArrowDownOutlined />}
                    disabled={idx === localCategories.length - 1}
                    onClick={() => moveCategory(idx, "down")}
                  />
                  <Button
                    type="text"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => removeCategory(idx)}
                  />
                </Space>
              </div>
            ))}
          </div>
        )}

        {categoriesDirty && (
          <div className="mt-3">
            <Button
              type="primary"
              onClick={saveCategories}
              loading={savingCategories}
            >
              保存品类
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
