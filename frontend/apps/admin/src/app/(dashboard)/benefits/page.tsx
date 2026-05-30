"use client";

import { useEffect, useState, useCallback } from "react";
import { App, Button, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Table, Tabs, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { usePaginatedList } from "@/lib/hooks";

const { Title } = Typography;

interface Benefit {
  id: string;
  name: string;
  benefit_type: string;
  stock_total: number;
  stock_used: number;
  per_person_limit: number;
  campaign_id: string;
  status: string;
  created_at: string;
  config_json: Record<string, unknown>;
}

interface BenefitClaim {
  id: string;
  consumer_id: string;
  benefit_id: string;
  status: string;
  claimed_at: string;
}

interface Campaign {
  id: string;
  name: string;
}

const BENEFIT_TYPE_MAP: Record<string, { label: string; color: string }> = {
  platform_coupon: { label: "平台券", color: "blue" },
  external_link: { label: "外部链接", color: "green" },
  private_domain: { label: "私域", color: "orange" },
  form_benefit: { label: "表单", color: "purple" },
};

const CLAIM_STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待领取", color: "default" },
  claimed: { label: "已领取", color: "blue" },
  used: { label: "已使用", color: "green" },
  expired: { label: "已过期", color: "gray" },
  cancelled: { label: "已取消", color: "red" },
};

function BenefitConfigFields({ benefitType }: { benefitType: string }) {
  if (benefitType === "platform_coupon") {
    return (
      <>
        <Form.Item name={["config_json", "amount"]} label="券面额（元）">
          <InputNumber min={0} className="w-full" />
        </Form.Item>
        <Form.Item name={["config_json", "min_order"]} label="最低订单金额（元）">
          <InputNumber min={0} className="w-full" />
        </Form.Item>
        <Form.Item name={["config_json", "coupon_code"]} label="券码">
          <Input placeholder="可选，留空则系统自动生成" />
        </Form.Item>
      </>
    );
  }
  if (benefitType === "external_link") {
    return (
      <>
        <Form.Item name={["config_json", "url"]} label="跳转链接">
          <Input placeholder="https://example.com" />
        </Form.Item>
        <Form.Item name={["config_json", "link_text"]} label="链接文案">
          <Input placeholder="点击领取" />
        </Form.Item>
      </>
    );
  }
  if (benefitType === "private_domain") {
    return (
      <>
        <Form.Item name={["config_json", "qr_image_url"]} label="微信群二维码图片 URL">
          <Input placeholder="https://..." />
        </Form.Item>
        <Form.Item name={["config_json", "group_name"]} label="群名称">
          <Input />
        </Form.Item>
      </>
    );
  }
  if (benefitType === "form_benefit") {
    return (
      <>
        <Form.Item name={["config_json", "form_url"]} label="表单链接">
          <Input placeholder="https://..." />
        </Form.Item>
        <Form.Item name={["config_json", "require_phone"]} label="需要手机号">
          <Select
            options={[
              { value: true, label: "是" },
              { value: false, label: "否" },
            ]}
          />
        </Form.Item>
      </>
    );
  }
  return null;
}

export default function BenefitsPage() {
  const { message } = App.useApp();
  const {
    items: benefits, total: benefitsTotal, page: benefitsPage, loading: benefitsLoading,
    setPage: setBenefitsPage, refresh: refreshBenefits,
  } = usePaginatedList<Benefit>(
    async ({ page, page_size }) => {
      try {
        const { data } = await api.get("/benefits", { params: { page, page_size } });
        return { items: data.items || [], total: data.total || 0 };
      } catch {
        message.error("加载权益列表失败");
        return { items: [], total: 0 };
      }
    }
  );

  const {
    items: claims, total: claimsTotal, page: claimsPage, loading: claimsLoading,
    setPage: setClaimsPage, refresh: refreshClaims,
  } = usePaginatedList<BenefitClaim>(
    async ({ page, page_size }) => {
      try {
        const { data } = await api.get("/benefits/admin/claims", { params: { page, page_size } });
        return { items: data.items || [], total: data.total || 0 };
      } catch {
        message.error("加载领取记录失败");
        return { items: [], total: 0 };
      }
    }
  );

  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<Benefit | null>(null);
  const [form] = Form.useForm();
  const [activeTab, setActiveTab] = useState("benefits");
  const [benefitType, setBenefitType] = useState<string>("");

  const fetchCampaigns = useCallback(async () => {
    try {
      const { data } = await api.get("/campaigns", { params: { page_size: 100 } });
      setCampaigns(data.items || []);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    fetchCampaigns();
  }, [fetchCampaigns]);

  const openCreate = () => {
    setEditItem(null);
    form.resetFields();
    setBenefitType("");
    setModalOpen(true);
  };

  const openEdit = (record: Benefit) => {
    setEditItem(record);
    setBenefitType(record.benefit_type);
    form.setFieldsValue({
      name: record.name,
      benefit_type: record.benefit_type,
      stock_total: record.stock_total,
      per_person_limit: record.per_person_limit,
      campaign_id: record.campaign_id,
      status: record.status,
      config_json: record.config_json,
    });
    setModalOpen(true);
  };

  const handleSubmit = async (values: Record<string, unknown>) => {
    try {
      const payload: Record<string, unknown> = {
        name: values.name,
        benefit_type: values.benefit_type,
        stock_total: values.stock_total,
        per_person_limit: values.per_person_limit,
        campaign_id: values.campaign_id,
        config_json: values.config_json || {},
      };
      if (editItem) {
        await api.patch(`/benefits/${editItem.id}`, payload);
        message.success("权益更新成功");
      } else {
        await api.post(`/campaigns/${values.campaign_id}/benefits`, payload);
        message.success("权益创建成功");
      }
      setModalOpen(false);
      form.resetFields();
      setBenefitsPage(1);
      refreshBenefits();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || (editItem ? "更新权益失败" : "创建权益失败"));
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/benefits/${id}`);
      message.success("权益已删除");
      refreshBenefits();
    } catch {
      message.error("删除失败");
    }
  };

  const campaignMap = campaigns.reduce<Record<string, string>>((acc, c) => {
    acc[c.id] = c.name;
    return acc;
  }, {});

  const benefitColumns: ColumnsType<Benefit> = [
    { title: "权益名称", dataIndex: "name", key: "name" },
    {
      title: "类型",
      dataIndex: "benefit_type",
      key: "benefit_type",
      render: (t: string) => {
        const info = BENEFIT_TYPE_MAP[t] || { label: t, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "关联活动",
      dataIndex: "campaign_id",
      key: "campaign_id",
      render: (v: string) => campaignMap[v] || v,
    },
    { title: "总库存", dataIndex: "stock_total", key: "stock_total" },
    {
      title: "剩余库存",
      key: "remaining",
      render: (_: unknown, record: Benefit) => record.stock_total - record.stock_used,
    },
    { title: "每人限领", dataIndex: "per_person_limit", key: "per_person_limit" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => <Tag>{s}</Tag>,
    },
    { title: "创建时间", dataIndex: "created_at", key: "created_at" },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Benefit) => (
        <Space>
          <Button size="small" onClick={() => openEdit(record)}>编辑</Button>
          <Popconfirm
            title="确认删除权益？"
            onConfirm={() => handleDelete(record.id)}
          >
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const claimColumns: ColumnsType<BenefitClaim> = [
    { title: "消费者 ID", dataIndex: "consumer_id", key: "consumer_id" },
    { title: "权益 ID", dataIndex: "benefit_id", key: "benefit_id" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = CLAIM_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    { title: "领取时间", dataIndex: "claimed_at", key: "claimed_at" },
  ];

  const tabItems = [
    {
      key: "benefits",
      label: "权益列表",
      children: (
        <Table
          columns={benefitColumns}
          dataSource={benefits}
          rowKey="id"
          loading={benefitsLoading}
          pagination={{
            current: benefitsPage,
            total: benefitsTotal,
            pageSize: 20,
            onChange: setBenefitsPage,
            showTotal: (t) => `共 ${t} 条`,
          }}
        />
      ),
    },
    {
      key: "claims",
      label: "领取记录",
      children: (
        <Table
          columns={claimColumns}
          dataSource={claims}
          rowKey="id"
          loading={claimsLoading}
          pagination={{
            current: claimsPage,
            total: claimsTotal,
            pageSize: 20,
            onChange: setClaimsPage,
            showTotal: (t) => `共 ${t} 条`,
          }}
        />
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          权益管理
        </Title>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={openCreate}
        >
          新建权益
        </Button>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={tabItems}
      />

      <Modal
        title={editItem ? "编辑权益" : "新建权益"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        width={560}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          onValuesChange={(changed) => {
            if (changed.benefit_type) {
              setBenefitType(changed.benefit_type as string);
            }
          }}
        >
          <Form.Item
            name="name"
            label="权益名称"
            rules={[{ required: true, message: "请输入权益名称" }]}
          >
            <Input data-testid="benefit-name-input" />
          </Form.Item>
          <Form.Item
            name="benefit_type"
            label="权益类型"
            rules={[{ required: true, message: "请选择权益类型" }]}
          >
            <Select
              options={Object.entries(BENEFIT_TYPE_MAP).map(([value, { label }]) => ({
                value,
                label,
              }))}
              data-testid="benefit-type-select"
            />
          </Form.Item>
          <Form.Item
            name="campaign_id"
            label="关联活动"
            rules={[{ required: true, message: "请选择关联活动" }]}
          >
            <Select
              placeholder="选择活动"
              options={campaigns.map((c) => ({ value: c.id, label: c.name }))}
              showSearch
              optionFilterProp="label"
              data-testid="benefit-campaign-select"
            />
          </Form.Item>
          <div className="grid grid-cols-2 gap-4">
            <Form.Item
              name="stock_total"
              label="总库存"
              rules={[{ required: true, message: "请输入总库存" }]}
            >
              <InputNumber min={1} className="w-full" data-testid="benefit-stock-input" />
            </Form.Item>
            <Form.Item
              name="per_person_limit"
              label="每人限领"
              rules={[{ required: true, message: "请输入每人限领数量" }]}
            >
              <InputNumber min={1} className="w-full" data-testid="benefit-limit-input" />
            </Form.Item>
          </div>
          <BenefitConfigFields benefitType={benefitType} />
        </Form>
      </Modal>
    </div>
  );
}
