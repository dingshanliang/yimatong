"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Table,
  Button,
  Space,
  Modal,
  Form,
  Input,
  InputNumber,
  Select,
  Tag,
  Typography,
  Tabs,
  message,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

interface Benefit {
  id: string;
  name: string;
  benefit_type: string;
  total_quota: number;
  remaining_quota: number;
  campaign_id: string;
  status: string;
  created_at: string;
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
  coupon: { label: "优惠券", color: "blue" },
  points: { label: "积分", color: "green" },
  gift: { label: "实物礼品", color: "orange" },
  lottery: { label: "抽奖", color: "purple" },
};

const CLAIM_STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待领取", color: "default" },
  claimed: { label: "已领取", color: "blue" },
  used: { label: "已使用", color: "green" },
  expired: { label: "已过期", color: "gray" },
  cancelled: { label: "已取消", color: "red" },
};

export default function BenefitsPage() {
  const [benefits, setBenefits] = useState<Benefit[]>([]);
  const [benefitsTotal, setBenefitsTotal] = useState(0);
  const [benefitsPage, setBenefitsPage] = useState(1);
  const [benefitsLoading, setBenefitsLoading] = useState(false);

  const [claims, setClaims] = useState<BenefitClaim[]>([]);
  const [claimsTotal, setClaimsTotal] = useState(0);
  const [claimsPage, setClaimsPage] = useState(1);
  const [claimsLoading, setClaimsLoading] = useState(false);

  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();
  const [activeTab, setActiveTab] = useState("benefits");

  const fetchBenefits = useCallback(async () => {
    setBenefitsLoading(true);
    try {
      const { data } = await api.get("/benefits", {
        params: { page: benefitsPage, page_size: 20 },
      });
      setBenefits(data.items || []);
      setBenefitsTotal(data.total || 0);
    } catch {
      message.error("加载权益列表失败");
    } finally {
      setBenefitsLoading(false);
    }
  }, [benefitsPage]);

  const fetchClaims = useCallback(async () => {
    setClaimsLoading(true);
    try {
      const { data } = await api.get("/benefit-claims", {
        params: { page: claimsPage, page_size: 20 },
      });
      setClaims(data.items || []);
      setClaimsTotal(data.total || 0);
    } catch {
      message.error("加载领取记录失败");
    } finally {
      setClaimsLoading(false);
    }
  }, [claimsPage]);

  const fetchCampaigns = useCallback(async () => {
    try {
      const { data } = await api.get("/campaigns", { params: { page_size: 100 } });
      setCampaigns(data.items || []);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    fetchBenefits();
  }, [fetchBenefits]);

  useEffect(() => {
    if (activeTab === "claims") {
      fetchClaims();
    }
  }, [fetchClaims, activeTab]);

  useEffect(() => {
    fetchCampaigns();
  }, [fetchCampaigns]);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      await api.post("/benefits", values);
      message.success("权益创建成功");
      setCreateOpen(false);
      form.resetFields();
      setBenefitsPage(1);
      fetchBenefits();
    } catch {
      message.error("创建权益失败");
    }
  };

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
    { title: "总配额", dataIndex: "total_quota", key: "total_quota" },
    { title: "剩余配额", dataIndex: "remaining_quota", key: "remaining_quota" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => <Tag>{s}</Tag>,
    },
    { title: "创建时间", dataIndex: "created_at", key: "created_at" },
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
          onClick={() => setCreateOpen(true)}
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
        title="新建权益"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        width={500}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item
            name="name"
            label="权益名称"
            rules={[{ required: true, message: "请输入权益名称" }]}
          >
            <Input />
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
            />
          </Form.Item>
          <Form.Item
            name="total_quota"
            label="总配额"
            rules={[{ required: true, message: "请输入总配额" }]}
          >
            <InputNumber min={1} className="w-full" />
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
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
