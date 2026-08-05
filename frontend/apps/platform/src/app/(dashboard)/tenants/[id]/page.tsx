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
import { STATUS_COLORS } from "@/lib/status-colors";
import { canRetryInitialAdminActivation } from "../activation";
import { planExpiryLabel, toChinaBusinessDate } from "../plan-date";

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
  initial_admin_state: string | null;
  activation_retryable: boolean;
}

interface PlanDefinition {
  id: string;
  name: TenantDetail["plan"];
  display_name: string;
  is_active: boolean;
}

export default function TenantDetailPage() {
  const router = useRouter();
  const params = useParams();
  const { modal, message } = App.useApp();
  const tenantId = params.id as string;

  const [basicEditOpen, setBasicEditOpen] = useState(false);
  const [planEditOpen, setPlanEditOpen] = useState(false);
  const [savingBasic, setSavingBasic] = useState(false);
  const [savingPlan, setSavingPlan] = useState(false);

  const { data, mutate, isLoading } = useSWR<TenantDetail>(
    `/platform/tenants/${tenantId}`
  );
  const { data: planDefinitions } = useSWR<PlanDefinition[]>("/platform/plans");
  const [basicForm] = Form.useForm();
  const [planForm] = Form.useForm();

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!data) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Text type="secondary">租户不存在</Text>
      </div>
    );
  }

  const handleStatusChange = (newStatus: string) => {
    const labels: Record<string, string> = {
      suspended: "暂停",
      active: "恢复",
      terminated: "终止",
    };
    modal.confirm({
      title: `确认${labels[newStatus]}租户`,
      content: `确定要${labels[newStatus]}租户「${data.name}」吗？`,
      onOk: async () => {
        try {
          await api.patch(`/platform/tenants/${tenantId}/status`, {
            status: newStatus,
          });
          message.success("操作成功");
          mutate();
        } catch (err) {
          message.error(extractErrorMessage(err, "操作失败"));
        }
      },
    });
  };

  const handleBasicEdit = async (values: {
    name: string;
    industry?: string;
    notes?: string;
  }) => {
    setSavingBasic(true);
    try {
      await api.patch(`/platform/tenants/${tenantId}`, {
        name: values.name,
        industry: values.industry?.trim() || null,
        notes: values.notes?.trim() || null,
      });
      message.success("基本信息已保存");
      setBasicEditOpen(false);
      mutate();
    } catch (err) {
      message.error(extractErrorMessage(err, "基本信息保存失败"));
    } finally {
      setSavingBasic(false);
    }
  };

  const handlePlanEdit = async (values: {
    plan: TenantDetail["plan"];
    plan_expires_at?: dayjs.Dayjs;
  }) => {
    setSavingPlan(true);
    try {
      const selectedPlan = planDefinitions?.find(
        (plan) => plan.name === values.plan
      );
      if (!selectedPlan) {
        throw new Error("套餐配置尚未加载，请稍后重试");
      }
      await api.post(`/platform/tenants/${tenantId}/assign-plan`, {
        plan_id: selectedPlan.id,
        expires_on: values.plan_expires_at
          ? values.plan_expires_at.format("YYYY-MM-DD")
          : null,
      });
      message.success("套餐与权益已保存");
      setPlanEditOpen(false);
      mutate();
    } catch (err) {
      message.error(extractErrorMessage(err, "套餐保存失败"));
    } finally {
      setSavingPlan(false);
    }
  };

  const handleActivationLink = async () => {
    try {
      const { data: activation } = await api.post<{ activation_url: string }>(
        `/platform/tenants/${tenantId}/initial-admin-activation`
      );
      modal.success({
        title: "管理员激活链接已生成",
        width: 560,
        content: (
          <Typography.Paragraph copyable={{ text: activation.activation_url }}>
            {activation.activation_url}
          </Typography.Paragraph>
        ),
      });
    } catch (error) {
      message.error(extractErrorMessage(error, "激活链接生成失败"));
    }
  };

  const statusInfo = STATUS_MAP[data.status] ?? {
    color: STATUS_COLORS.neutral,
    label: data.status,
  };
  const planInfo = PLAN_MAP[data.plan] ?? {
    color: STATUS_COLORS.neutral,
    label: data.plan,
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
        <Space>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => router.push("/tenants")}
          />
          <Title level={4} style={{ margin: 0 }}>
            {data.name}
          </Title>
          <Tag color={statusInfo.color}>{statusInfo.label}</Tag>
          <Tag color={planInfo.color}>{planInfo.label}</Tag>
        </Space>
        <Space>
          {data.status === "active" && (
            <Button
              icon={<PauseCircleOutlined />}
              onClick={() => handleStatusChange("suspended")}
            >
              暂停
            </Button>
          )}
          {data.status === "suspended" && (
            <Button
              icon={<PlayCircleOutlined />}
              type="primary"
              onClick={() => handleStatusChange("active")}
            >
              恢复
            </Button>
          )}
          {canRetryInitialAdminActivation(data) && (
            <Button
              icon={<PlayCircleOutlined />}
              onClick={handleActivationLink}
            >
              恢复管理员激活
            </Button>
          )}
          <Button
            icon={<EditOutlined />}
            onClick={() => {
              basicForm.setFieldsValue({
                name: data.name,
                industry: data.industry,
                notes: data.notes,
              });
              setBasicEditOpen(true);
            }}
          >
            编辑基本信息
          </Button>
          <Button
            onClick={() => {
              planForm.setFieldsValue({
                plan: data.plan,
                plan_expires_at: data.plan_expires_at
                  ? dayjs(toChinaBusinessDate(data.plan_expires_at))
                  : undefined,
              });
              setPlanEditOpen(true);
            }}
          >
            调整套餐
          </Button>
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
                  <Descriptions.Item label="租户 ID">
                    {data.id}
                  </Descriptions.Item>
                  <Descriptions.Item label="Slug">
                    {data.slug}
                  </Descriptions.Item>
                  <Descriptions.Item label="套餐">
                    {planInfo.label}
                  </Descriptions.Item>
                  <Descriptions.Item label="状态">
                    {statusInfo.label}
                  </Descriptions.Item>
                  <Descriptions.Item label="行业">
                    {data.industry ?? "-"}
                  </Descriptions.Item>
                  <Descriptions.Item label="过期时间">
                    {planExpiryLabel(data.plan_expires_at)}
                  </Descriptions.Item>
                  <Descriptions.Item label="创建时间">
                    {dayjs(data.created_at).format("YYYY-MM-DD HH:mm")}
                  </Descriptions.Item>
                  <Descriptions.Item label="备注">
                    {data.notes ?? "-"}
                  </Descriptions.Item>
                  <Descriptions.Item label="账号数">
                    {data.account_count}
                  </Descriptions.Item>
                  <Descriptions.Item label="组织数">
                    {data.organization_count}
                  </Descriptions.Item>
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
                      <Descriptions.Item key={key} label={key}>
                        {value}
                      </Descriptions.Item>
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
                {data.enabled_features &&
                Object.keys(data.enabled_features).length > 0 ? (
                  <Descriptions bordered column={2}>
                    {Object.entries(data.enabled_features).map(
                      ([key, value]) => (
                        <Descriptions.Item key={key} label={key}>
                          <Tag
                            color={
                              value
                                ? STATUS_COLORS.success
                                : STATUS_COLORS.neutral
                            }
                          >
                            {value ? "已启用" : "已禁用"}
                          </Tag>
                        </Descriptions.Item>
                      )
                    )}
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
        title="编辑基本信息"
        open={basicEditOpen}
        onCancel={() => setBasicEditOpen(false)}
        onOk={() => basicForm.submit()}
        confirmLoading={savingBasic}
        width={520}
      >
        <Form form={basicForm} layout="vertical" onFinish={handleBasicEdit}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="industry" label="行业">
            <Input />
          </Form.Item>
          <Form.Item name="notes" label="备注">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="调整套餐与权益"
        open={planEditOpen}
        onCancel={() => setPlanEditOpen(false)}
        onOk={() => planForm.submit()}
        confirmLoading={savingPlan}
        width={520}
      >
        <Form form={planForm} layout="vertical" onFinish={handlePlanEdit}>
          <Form.Item name="plan" label="套餐" rules={[{ required: true }]}>
            <Select
              loading={!planDefinitions}
              options={planDefinitions
                ?.filter(
                  (plan) =>
                    plan.name in PLAN_MAP &&
                    (plan.is_active || plan.name === data.plan)
                )
                .map((plan) => ({
                  value: plan.name,
                  label: plan.display_name,
                }))}
            />
          </Form.Item>
          <Form.Item name="plan_expires_at" label="有效期至（北京时间）">
            <DatePicker style={{ width: "100%" }} />
          </Form.Item>
          <Typography.Paragraph type="secondary">
            所选日期整日有效，到北京时间当天 23:59:59
            后到期。保存后会原子更新套餐、额度和功能权益。
          </Typography.Paragraph>
        </Form>
      </Modal>
    </div>
  );
}
