"use client";

import { useState } from "react";
import { App, Button, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { useCrud } from "@/lib/hooks";

const { Title } = Typography;
const { TextArea } = Input;

const TYPE_OPTIONS = [
  { value: "coupon", label: "优惠券" },
  { value: "lottery", label: "抽奖" },
  { value: "points", label: "积分" },
];

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  draft: { label: "草稿", color: "default" },
  active: { label: "进行中", color: "blue" },
  paused: { label: "已暂停", color: "orange" },
  ended: { label: "已结束", color: "gray" },
};

export default function CampaignsPage() {
  const { message } = App.useApp();
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<Record<string, unknown> | null>(null);
  const [form] = Form.useForm();

  const { items: campaigns, total, page, loading, setPage, mutate, create, update, remove } = useCrud<Record<string, unknown> & { id: string }>("/campaigns");

  const openCreate = () => {
    setEditItem(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (record: Record<string, unknown>) => {
    setEditItem(record);
    const rules = (record.rules_json as Record<string, string>) || {};
    form.setFieldsValue({
      name: record.name,
      campaign_type: record.campaign_type,
      start_at: record.start_at,
      end_at: record.end_at,
      description: record.description,
      participation_conditions: rules.participation_conditions || "",
      claim_limits: rules.claim_limits || "",
      validity_period: rules.validity_period || "",
      disclaimer: rules.disclaimer || "",
      minor_notice: rules.minor_notice || "",
      customer_service_contact: rules.customer_service_contact || "",
    });
    setModalOpen(true);
  };

  const handleSubmit = async (values: Record<string, unknown>) => {
    try {
      const payload = {
        ...values,
        rules_json: {
          participation_conditions: values.participation_conditions || "",
          claim_limits: values.claim_limits || "每人限领1次",
          validity_period: values.validity_period || "领取后7天有效",
          disclaimer: values.disclaimer || "最终解释权归品牌方所有",
          minor_notice: values.minor_notice || "未成年人请在监护人陪同下参与",
          customer_service_contact: values.customer_service_contact || "",
        },
      };
      if (editItem) {
        await update(editItem.id as string, payload);
        message.success("活动更新成功");
      } else {
        await create(payload);
        message.success("活动创建成功");
      }
      setModalOpen(false);
      form.resetFields();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || (editItem ? "更新失败" : "创建失败"));
    }
  };

  const activateCampaign = async (id: string) => {
    try {
      await api.post(`/campaigns/${id}/status`, { status: "active" });
      message.success("活动已上线");
      mutate();
    } catch {
      message.error("操作失败");
    }
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "活动名称", dataIndex: "name", key: "name" },
    {
      title: "类型",
      dataIndex: "campaign_type",
      key: "campaign_type",
      render: (t: string) => TYPE_OPTIONS.find((o) => o.value === t)?.label || t,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    { title: "开始时间", dataIndex: "start_at", key: "start_at" },
    { title: "结束时间", dataIndex: "end_at", key: "end_at" },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Record<string, unknown>) => (
        <Space>
          <Button size="small" onClick={() => openEdit(record)}>编辑</Button>
          {record.status === "draft" && (
            <Popconfirm
              title="确认上线活动？"
              onConfirm={() => activateCampaign(record.id as string)}
            >
              <Button size="small" type="primary">上线</Button>
            </Popconfirm>
          )}
          {record.status === "active" && (
            <Popconfirm
              title="确认暂停活动？"
              onConfirm={async () => {
                await api.post(`/campaigns/${record.id as string}/status`, { status: "paused" });
                message.success("已暂停");
                mutate();
              }}
            >
              <Button size="small">暂停</Button>
            </Popconfirm>
          )}
          {record.status === "draft" && (
            <Popconfirm
              title="确认删除活动？"
              onConfirm={async () => {
                await remove(record.id as string);
                message.success("已删除");
              }}
            >
              <Button size="small" danger>删除</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">活动管理</Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建活动
        </Button>
      </div>

      <Table
        columns={columns}
        dataSource={campaigns}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page,
          total,
          pageSize: 20,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
      />

      <Modal
        title={editItem ? "编辑活动" : "新建活动"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        width={600}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <Form.Item name="name" label="活动名称" rules={[{ required: true }]}>
            <Input data-testid="campaign-name-input" />
          </Form.Item>
          <Form.Item name="campaign_type" label="活动类型" rules={[{ required: true }]}>
            <Select options={TYPE_OPTIONS} data-testid="campaign-type-select" />
          </Form.Item>
          <Space className="w-full" orientation="vertical">
            <div className="grid grid-cols-2 gap-4">
              <Form.Item name="start_at" label="开始时间" rules={[{ required: true }]}>
                <Input placeholder="2026-06-01T00:00:00" data-testid="campaign-start-input" />
              </Form.Item>
              <Form.Item name="end_at" label="结束时间" rules={[{ required: true }]}>
                <Input placeholder="2026-06-30T23:59:59" data-testid="campaign-end-input" />
              </Form.Item>
            </div>
          </Space>
          <Form.Item name="description" label="活动描述">
            <TextArea rows={2} data-testid="campaign-description-input" />
          </Form.Item>
          <Form.Item name="participation_conditions" label="参与条件">
            <TextArea rows={2} data-testid="campaign-conditions-input" />
          </Form.Item>
          <Form.Item name="claim_limits" label="领取限制">
            <Input placeholder="每人限领1次" data-testid="campaign-claim-limits-input" />
          </Form.Item>
          <Form.Item name="validity_period" label="有效期">
            <Input placeholder="领取后7天有效" data-testid="campaign-validity-input" />
          </Form.Item>
          <Form.Item name="disclaimer" label="免责声明">
            <TextArea rows={2} data-testid="campaign-disclaimer-input" />
          </Form.Item>
          <Form.Item name="customer_service_contact" label="客服联系方式">
            <Input placeholder="400-123-4567" data-testid="campaign-contact-input" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
