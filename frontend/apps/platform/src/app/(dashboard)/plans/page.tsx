"use client";

import { useState } from "react";
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Switch,
  Typography,
  message,
} from "antd";
import { PlusOutlined, EditOutlined, CrownOutlined } from "@ant-design/icons";
import useSWR from "swr";
import api from "@/lib/api";
import { extractErrorMessage } from "@/lib/api";

const { Title, Text } = Typography;

interface PlanDef {
  id: string;
  name: string;
  display_name: string;
  description: string | null;
  price_yearly: number;
  quota_defaults: Record<string, number> | null;
  feature_flags: Record<string, boolean> | null;
  is_active: boolean;
  sort_order: number;
}

const QUOTA_FIELDS = [
  { key: "max_codes", label: "最大码量" },
  { key: "max_scans", label: "最大扫码量" },
  { key: "max_campaigns", label: "最大活动数" },
  { key: "max_accounts", label: "最大账号数" },
];

const FEATURE_FIELDS = [
  { key: "ai_assistant", label: "AI 助手" },
  { key: "risk_module", label: "风控模块" },
  { key: "channel_portal", label: "渠道门户" },
  { key: "white_label", label: "白标" },
];

const PLAN_COLORS: Record<string, string> = {
  free: "var(--ymt-color-text-tertiary)",
  starter: "var(--ymt-color-feedback-info)",
  pro: "var(--ymt-color-brand-primary)",
  enterprise: "var(--ymt-color-feedback-warning)",
};

export default function PlansPage() {
  const [createOpen, setCreateOpen] = useState(false);
  const [editPlan, setEditPlan] = useState<PlanDef | null>(null);
  const [saving, setSaving] = useState(false);
  const { data, mutate } = useSWR<PlanDef[]>("/platform/plans");
  const [form] = Form.useForm();

  const openEdit = (plan: PlanDef) => {
    setEditPlan(plan);
    form.setFieldsValue({
      display_name: plan.display_name,
      description: plan.description,
      price_yearly: plan.price_yearly / 100, // cents → yuan
      max_codes: plan.quota_defaults?.max_codes,
      max_scans: plan.quota_defaults?.max_scans,
      max_campaigns: plan.quota_defaults?.max_campaigns,
      max_accounts: plan.quota_defaults?.max_accounts,
      ai_assistant: plan.feature_flags?.ai_assistant ?? false,
      risk_module: plan.feature_flags?.risk_module ?? false,
      channel_portal: plan.feature_flags?.channel_portal ?? false,
      white_label: plan.feature_flags?.white_label ?? false,
    });
  };

  const handleSubmit = async (values: Record<string, unknown>) => {
    setSaving(true);
    try {
      const quota_defaults: Record<string, number> = {};
      QUOTA_FIELDS.forEach(({ key }) => {
        const v = values[key];
        if (v !== undefined && v !== null) quota_defaults[key] = Number(v);
      });

      const feature_flags: Record<string, boolean> = {};
      FEATURE_FIELDS.forEach(({ key }) => {
        feature_flags[key] = !!values[key];
      });

      const payload = {
        display_name: values.display_name,
        description: values.description || null,
        price_yearly: Math.round((Number(values.price_yearly) || 0) * 100), // yuan → cents
        quota_defaults,
        feature_flags,
      };

      if (editPlan) {
        await api.patch(`/platform/plans/${editPlan.id}`, payload);
        message.success("更新成功");
      } else {
        await api.post("/platform/plans", {
          ...payload,
          name: values.name,
          sort_order: values.sort_order || 0,
        });
        message.success("创建成功");
      }

      mutate();
      setCreateOpen(false);
      setEditPlan(null);
      form.resetFields();
    } catch (err) {
      message.error(extractErrorMessage(err, "操作失败"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <Title level={4} style={{ margin: 0 }}>
          套餐管理
        </Title>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setEditPlan(null);
            form.resetFields();
            setCreateOpen(true);
          }}
        >
          创建套餐
        </Button>
      </div>

      <Row gutter={[16, 16]}>
        {data?.map((plan) => (
          <Col xs={24} sm={12} lg={6} key={plan.id}>
            <Card
              style={{
                borderTop: `3px solid ${PLAN_COLORS[plan.name] || "var(--ymt-color-brand-primary)"}`,
              }}
              actions={[
                <Button
                  key="edit"
                  type="link"
                  icon={<EditOutlined />}
                  onClick={() => {
                    openEdit(plan);
                    setCreateOpen(true);
                  }}
                >
                  编辑
                </Button>,
              ]}
            >
              <Card.Meta
                avatar={
                  <CrownOutlined
                    style={{
                      fontSize: "var(--ymt-font-size-xl)",
                      color:
                        PLAN_COLORS[plan.name] ||
                        "var(--ymt-color-brand-primary)",
                    }}
                  />
                }
                title={plan.display_name}
                description={plan.description || ""}
              />
              <div style={{ marginTop: 16 }}>
                <Text
                  strong
                  style={{
                    fontSize: "var(--ymt-font-size-lg)",
                    color: PLAN_COLORS[plan.name],
                  }}
                >
                  ¥
                  {plan.price_yearly
                    ? (plan.price_yearly / 100).toLocaleString()
                    : "免费"}
                </Text>
                <Text type="secondary"> / 年</Text>
              </div>
              <div style={{ marginTop: 12 }}>
                {plan.quota_defaults &&
                  Object.entries(plan.quota_defaults).map(([key, value]) => (
                    <div
                      key={key}
                      style={{
                        fontSize: "var(--ymt-font-size-xs)",
                        color: "var(--ymt-color-text-secondary)",
                        marginBottom: 2,
                      }}
                    >
                      {key === "max_codes"
                        ? "码量"
                        : key === "max_scans"
                          ? "扫码量"
                          : key === "max_campaigns"
                            ? "活动数"
                            : "账号数"}
                      ：{value === -1 ? "无限制" : value?.toLocaleString()}
                    </div>
                  ))}
              </div>
              {!plan.is_active && (
                <Text
                  type="danger"
                  style={{
                    fontSize: "var(--ymt-font-size-xs)",
                    marginTop: 8,
                    display: "block",
                  }}
                >
                  已停用
                </Text>
              )}
            </Card>
          </Col>
        ))}
      </Row>

      <Modal
        title={editPlan ? "编辑套餐" : "创建套餐"}
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false);
          setEditPlan(null);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        confirmLoading={saving}
        width={560}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          {!editPlan && (
            <>
              <Form.Item
                name="name"
                label="套餐标识"
                rules={[{ required: true }]}
              >
                <Input placeholder="例：starter" disabled={!!editPlan} />
              </Form.Item>
              <Form.Item name="sort_order" label="排序">
                <InputNumber min={0} style={{ width: "100%" }} />
              </Form.Item>
            </>
          )}
          <Form.Item
            name="display_name"
            label="显示名称"
            rules={[{ required: true }]}
          >
            <Input placeholder="例：入门版" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="price_yearly" label="年费（元）" initialValue={0}>
            <InputNumber min={0} style={{ width: "100%" }} suffix="元/年" />
          </Form.Item>

          <Typography.Text
            strong
            style={{ display: "block", marginBottom: 12 }}
          >
            额度配置
          </Typography.Text>
          <Row gutter={12}>
            {QUOTA_FIELDS.map(({ key, label }) => (
              <Col span={12} key={key}>
                <Form.Item name={key} label={label}>
                  <InputNumber
                    min={-1}
                    style={{ width: "100%" }}
                    placeholder="-1 表示无限制"
                  />
                </Form.Item>
              </Col>
            ))}
          </Row>

          <Typography.Text
            strong
            style={{ display: "block", marginBottom: 12 }}
          >
            功能开关
          </Typography.Text>
          <Row gutter={12}>
            {FEATURE_FIELDS.map(({ key, label }) => (
              <Col span={12} key={key}>
                <Form.Item
                  name={key}
                  label={label}
                  valuePropName="checked"
                  initialValue={false}
                >
                  <Switch />
                </Form.Item>
              </Col>
            ))}
          </Row>
        </Form>
      </Modal>
    </div>
  );
}
