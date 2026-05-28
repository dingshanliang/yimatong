"use client";

import { useEffect, useState } from "react";
import {
  Table,
  Button,
  Space,
  Modal,
  Form,
  Input,
  Select,
  Tag,
  Typography,
  message,
  Popconfirm,
} from "antd";
import { PlusOutlined, SearchOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

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
  const [campaigns, setCampaigns] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();

  const fetchCampaigns = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/campaigns", { params: { page, page_size: 20 } });
      setCampaigns(data.items || []);
      setTotal(data.total || 0);
    } catch {
      message.error("加载活动列表失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCampaigns();
  }, [page]);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      await api.post("/campaigns", {
        ...values,
        rules_json: {
          participation_conditions: values.participation_conditions || "",
          claim_limits: values.claim_limits || "每人限领1次",
          validity_period: values.validity_period || "领取后7天有效",
          disclaimer: values.disclaimer || "最终解释权归品牌方所有",
          minor_notice: values.minor_notice || "未成年人请在监护人陪同下参与",
          customer_service_contact: values.customer_service_contact || "",
        },
      });
      message.success("活动创建成功");
      setCreateOpen(false);
      form.resetFields();
      setPage(1);
      fetchCampaigns();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "创建失败");
    }
  };

  const activateCampaign = async (id: string) => {
    try {
      await api.post(`/campaigns/${id}/status`, { status: "active" });
      message.success("活动已上线");
      fetchCampaigns();
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
                fetchCampaigns();
              }}
            >
              <Button size="small">暂停</Button>
            </Popconfirm>
          )}
          {record.status === "draft" && (
            <Popconfirm
              title="确认删除活动？"
              onConfirm={async () => {
                await api.delete(`/campaigns/${record.id as string}`);
                message.success("已删除");
                fetchCampaigns();
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
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
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
        title="新建活动"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        width={600}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="活动名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="campaign_type" label="活动类型" rules={[{ required: true }]}>
            <Select options={TYPE_OPTIONS} />
          </Form.Item>
          <Space className="w-full" direction="vertical">
            <div className="grid grid-cols-2 gap-4">
              <Form.Item name="start_at" label="开始时间" rules={[{ required: true }]}>
                <Input placeholder="2026-06-01T00:00:00" />
              </Form.Item>
              <Form.Item name="end_at" label="结束时间" rules={[{ required: true }]}>
                <Input placeholder="2026-06-30T23:59:59" />
              </Form.Item>
            </div>
          </Space>
          <Form.Item name="participation_conditions" label="参与条件">
            <TextArea rows={2} />
          </Form.Item>
          <Form.Item name="disclaimer" label="免责声明">
            <TextArea rows={2} />
          </Form.Item>
          <Form.Item name="customer_service_contact" label="客服联系方式">
            <Input placeholder="400-123-4567" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
