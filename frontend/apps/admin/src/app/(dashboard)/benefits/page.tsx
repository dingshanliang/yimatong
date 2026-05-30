"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  App, Button, Form, Input, InputNumber, Modal, Popconfirm, Progress,
  Select, Space, Table, Tabs, Tag, Typography, Radio, Divider, Alert,
} from "antd";
import { PlusOutlined, SendOutlined } from "@ant-design/icons";
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
  connector_id: string | null;
  status: string;
  created_at: string;
  config_json: Record<string, unknown>;
}

interface BenefitClaim {
  id: string;
  consumer_id: string;
  benefit_id: string;
  campaign_id: string;
  status: string;
  delivery_status: string;
  claimed_at: string;
}

interface Campaign {
  id: string;
  name: string;
}

interface Connector {
  id: string;
  name: string;
  connector_type: string;
  enabled: boolean;
  config: Record<string, unknown>;
}

function yuanToFen(yuan: number): number {
  return Math.round(yuan * 100);
}

function fenToYuan(fen: number): number {
  return fen / 100;
}

const BENEFIT_TYPE_MAP: Record<string, { label: string; color: string }> = {
  platform_coupon: { label: "平台券", color: "blue" },
  external_link: { label: "外部链接", color: "green" },
  private_domain: { label: "私域", color: "orange" },
  form_benefit: { label: "表单", color: "purple" },
  cash_red_packet: { label: "现金红包", color: "red" },
};

const BENEFIT_STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "已启用", color: "green" },
  inactive: { label: "已停用", color: "default" },
};

const CLAIM_STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待领取", color: "default" },
  claimed: { label: "已领取", color: "blue" },
  used: { label: "已使用", color: "green" },
  expired: { label: "已过期", color: "gray" },
  cancelled: { label: "已取消", color: "red" },
};

const DELIVERY_STATUS_MAP: Record<string, { label: string; color: string }> = {
  not_required: { label: "无需发放", color: "default" },
  pending: { label: "发放中", color: "blue" },
  delivered: { label: "已发放", color: "green" },
  failed: { label: "发放失败", color: "red" },
};

const AMOUNT_TYPE_OPTIONS = [
  { value: "fixed", label: "固定金额" },
  { value: "random", label: "随机金额" },
  { value: "lucky", label: "拼手气" },
];

function YuanInput({
  value = 0,
  onChange,
  ...rest
}: {
  value?: number;
  onChange?: (val: number) => void;
  min?: number;
  max?: number;
  placeholder?: string;
  className?: string;
  "data-testid"?: string;
}) {
  const [display, setDisplay] = useState<number | null>(fenToYuan(value));

  const handleChange = (val: number | null) => {
    setDisplay(val);
    if (onChange && val !== null && val !== undefined) {
      onChange(yuanToFen(val));
    }
  };

  return (
    <InputNumber
      value={display}
      onChange={handleChange}
      min={0.01}
      step={0.01}
      precision={2}
      addonAfter="元"
      {...rest}
    />
  );
}

function CashRedPacketConfigFields({
  form,
  connectors,
}: {
  form: ReturnType<typeof Form.useForm>[0];
  connectors: Connector[];
}) {
  const amountType = Form.useWatch(["config_json", "amount_type"], form) ?? "";

  return (
    <>
      <Divider titlePlacement="left" plain>
        红包金额设置
      </Divider>

      <Form.Item
        name={["config_json", "amount_type"]}
        label="金额类型"
        rules={[{ required: true, message: "请选择金额类型" }]}
      >
        <Radio.Group options={AMOUNT_TYPE_OPTIONS} optionType="button" buttonStyle="solid" />
      </Form.Item>

      {amountType === "fixed" && (
        <Form.Item
          name={["config_json", "fixed_amount"]}
          label="固定金额"
          rules={[{ required: true, message: "请输入固定金额" }]}
          extra="微信现金营销单笔上限 200 元"
        >
          <YuanInput max={200} />
        </Form.Item>
      )}

      {amountType === "random" && (
        <>
          <Form.Item
            name={["config_json", "min_amount"]}
            label="最小金额"
            rules={[{ required: true, message: "请输入最小金额" }]}
          >
            <YuanInput max={200} />
          </Form.Item>
          <Form.Item
            name={["config_json", "max_amount"]}
            label="最大金额"
            rules={[{ required: true, message: "请输入最大金额" }]}
          >
            <YuanInput max={200} />
          </Form.Item>
        </>
      )}

      {amountType === "lucky" && (
        <>
          <Form.Item
            name={["config_json", "lucky_total_count"]}
            label="拼手气总份数"
            rules={[{ required: true, message: "请输入拼手气总份数" }]}
          >
            <InputNumber min={2} max={100} className="w-full" addonAfter="份" />
          </Form.Item>
          <Form.Item
            name={["config_json", "lucky_min_per"]}
            label="每人最小金额"
            rules={[{ required: true, message: "请输入每人最小金额" }]}
          >
            <YuanInput max={200} />
          </Form.Item>
        </>
      )}

      <Divider titlePlacement="left" plain>
        预算与限制
      </Divider>

      <Form.Item
        name={["config_json", "budget"]}
        label="总预算"
        rules={[{ required: true, message: "请输入总预算" }]}
        extra="红包发放的总预算金额"
      >
        <YuanInput />
      </Form.Item>

      <div className="grid grid-cols-2 gap-4">
        <Form.Item
          name={["config_json", "daily_limit_per_user"]}
          label="每用户每日限领"
          initialValue={3}
        >
          <InputNumber min={1} max={100} className="w-full" addonAfter="次" />
        </Form.Item>
        <Form.Item
          name={["config_json", "total_limit_per_user"]}
          label="每用户总限领"
          initialValue={10}
        >
          <InputNumber min={1} max={1000} className="w-full" addonAfter="次" />
        </Form.Item>
      </div>

      <Form.Item
        name={["config_json", "transfer_remark"]}
        label="转账备注"
        extra="微信转账到零钱时显示的备注（选填）"
      >
        <Input placeholder="扫码领红包" maxLength={32} />
      </Form.Item>

      <Divider titlePlacement="left" plain>
        微信支付连接器
      </Divider>

      <Form.Item
        name="connector_id"
        label="微信支付转账连接器"
        rules={[{ required: true, message: "请选择微信支付转账连接器" }]}
        extra="用于调用微信支付商家转账到零钱 API"
      >
        <Select
          placeholder="选择已配置的微信支付转账连接器"
          showSearch
          optionFilterProp="label"
          notFoundContent={
            connectors.length === 0
              ? "暂无微信支付转账连接器，请先在连接器管理中创建"
              : undefined
          }
          options={connectors.map((c) => ({
            value: c.id,
            label: `${c.name}${c.enabled ? "" : "（已禁用）"}`,
          }))}
        />
      </Form.Item>
    </>
  );
}

function BenefitConfigFields({
  benefitType,
  form,
  couponPoolConnectors,
  wechatPayConnectors,
}: {
  benefitType: string;
  form: ReturnType<typeof Form.useForm>[0];
  couponPoolConnectors: Connector[];
  wechatPayConnectors: Connector[];
}) {
  if (benefitType === "cash_red_packet") {
    return <CashRedPacketConfigFields form={form} connectors={wechatPayConnectors} />;
  }
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
        <Divider titlePlacement="left" plain>
          券码池连接器
        </Divider>
        <Form.Item
          name="connector_id"
          label="券码池连接器"
          extra="关联券码池后，消费者领取时自动分配券码"
        >
          <Select
            placeholder="选择券码池连接器（可选）"
            showSearch
            optionFilterProp="label"
            allowClear
            notFoundContent={
              couponPoolConnectors.length === 0
                ? "暂无券码池连接器，请先在连接器管理中创建"
                : undefined
            }
            options={couponPoolConnectors.map((c) => ({
              value: c.id,
              label: `${c.name}${c.enabled ? "" : "（已禁用）"}`,
            }))}
          />
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
  const router = useRouter();
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
    setPage: setClaimsPage,
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
  const [allConnectors, setAllConnectors] = useState<Connector[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<Benefit | null>(null);
  const [form] = Form.useForm();
  const [activeTab, setActiveTab] = useState("benefits");
  const [benefitType, setBenefitType] = useState<string>("");

  const wechatPayConnectors = allConnectors.filter(
    (c) => c.connector_type === "wechat_pay_transfer"
  );
  const couponPoolConnectors = allConnectors.filter(
    (c) => c.connector_type === "coupon_pool"
  );

  const fetchCampaigns = useCallback(async () => {
    try {
      const { data } = await api.get("/campaigns", { params: { page_size: 100 } });
      setCampaigns(data.items || []);
    } catch {
      /* ignore */
    }
  }, []);

  const fetchConnectors = useCallback(async () => {
    try {
      const { data } = await api.get("/connectors/connectors");
      setAllConnectors(Array.isArray(data) ? data : []);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    fetchCampaigns();
    fetchConnectors();
  }, [fetchCampaigns, fetchConnectors]);

  const openCreate = () => {
    setEditItem(null);
    form.resetFields();
    setBenefitType("");
    setModalOpen(true);
  };

  const openEdit = (record: Benefit) => {
    setEditItem(record);
    setBenefitType(record.benefit_type);

    const configJson = { ...record.config_json } as Record<string, unknown>;
    if (record.benefit_type === "cash_red_packet") {
      if (typeof configJson.fixed_amount === "number") {
        configJson.fixed_amount = fenToYuan(configJson.fixed_amount as number);
      }
      if (typeof configJson.min_amount === "number") {
        configJson.min_amount = fenToYuan(configJson.min_amount as number);
      }
      if (typeof configJson.max_amount === "number") {
        configJson.max_amount = fenToYuan(configJson.max_amount as number);
      }
      if (typeof configJson.lucky_min_per === "number") {
        configJson.lucky_min_per = fenToYuan(configJson.lucky_min_per as number);
      }
      if (typeof configJson.budget === "number") {
        configJson.budget = fenToYuan(configJson.budget as number);
      }
    }

    form.setFieldsValue({
      name: record.name,
      benefit_type: record.benefit_type,
      stock_total: record.stock_total,
      per_person_limit: record.per_person_limit,
      campaign_id: record.campaign_id,
      status: record.status,
      connector_id: record.connector_id,
      config_json: configJson,
    });
    setModalOpen(true);
  };

  const transformValuesForSubmit = (values: Record<string, unknown>) => {
    if (values.benefit_type !== "cash_red_packet") {
      return values;
    }

    const configJson = { ...(values.config_json as Record<string, number>) };
    const fenFields = ["fixed_amount", "min_amount", "max_amount", "lucky_min_per", "budget"];

    for (const field of fenFields) {
      if (typeof configJson[field] === "number") {
        configJson[field] = yuanToFen(configJson[field]);
      }
    }

    return {
      ...values,
      config_json: configJson,
    };
  };

  const handleSubmit = async (values: Record<string, unknown>) => {
    try {
      const payload = transformValuesForSubmit(values) as Record<string, unknown>;

      const benefitPayload: Record<string, unknown> = {
        name: payload.name,
        benefit_type: payload.benefit_type,
        stock_total: payload.stock_total,
        per_person_limit: payload.per_person_limit,
        config_json: payload.config_json || {},
        connector_id: payload.connector_id || null,
      };

      if (editItem) {
        await api.patch(`/benefits/${editItem.id}`, benefitPayload);
        message.success("权益更新成功");
      } else {
        await api.post(`/campaigns/${payload.campaign_id}/benefits`, benefitPayload);
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

  const handleToggleStatus = async (record: Benefit) => {
    const newStatus = record.status === "active" ? "inactive" : "active";
    try {
      await api.patch(`/benefits/${record.id}`, { status: newStatus });
      message.success(newStatus === "active" ? "已启用" : "已停用");
      refreshBenefits();
    } catch {
      message.error("操作失败");
    }
  };

  const campaignMap = campaigns.reduce<Record<string, string>>((acc, c) => {
    acc[c.id] = c.name;
    return acc;
  }, {});

  const benefitMap = benefits.reduce<Record<string, string>>((acc, b) => {
    acc[b.id] = b.name;
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
    {
      title: "库存",
      key: "stock",
      render: (_: unknown, record: Benefit) => {
        const percent = record.stock_total > 0
          ? Math.round((record.stock_used / record.stock_total) * 100)
          : 0;
        const remaining = record.stock_total - record.stock_used;
        return (
          <div className="min-w-[120px]">
            <div className="mb-1 text-xs text-gray-500">
              已用 {record.stock_used} / {record.stock_total}（剩余 {remaining}）
            </div>
            <Progress
              percent={percent}
              size="small"
              status={percent >= 90 ? "exception" : percent >= 70 ? "active" : undefined}
            />
          </div>
        );
      },
    },
    { title: "每人限领", dataIndex: "per_person_limit", key: "per_person_limit" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = BENEFIT_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    { title: "创建时间", dataIndex: "created_at", key: "created_at" },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Benefit) => (
        <Space>
          <Button size="small" onClick={() => openEdit(record)}>编辑</Button>
          <Popconfirm
            title={record.status === "active" ? "确认停用？" : "确认启用？"}
            onConfirm={() => handleToggleStatus(record)}
          >
            <Button size="small" danger={record.status === "active"}>
              {record.status === "active" ? "停用" : "启用"}
            </Button>
          </Popconfirm>
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
    { title: "消费者 ID", dataIndex: "consumer_id", key: "consumer_id", render: (v: string) => v.slice(0, 8) + "..." },
    {
      title: "权益",
      dataIndex: "benefit_id",
      key: "benefit_id",
      render: (v: string) => benefitMap[v] || v.slice(0, 8) + "...",
    },
    {
      title: "领取状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = CLAIM_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "发放状态",
      dataIndex: "delivery_status",
      key: "delivery_status",
      render: (s: string) => {
        const info = DELIVERY_STATUS_MAP[s] || { label: s || "-", color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    { title: "领取时间", dataIndex: "claimed_at", key: "claimed_at" },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: BenefitClaim) => (
        record.delivery_status === "pending" || record.delivery_status === "failed" ? (
          <Button
            size="small"
            icon={<SendOutlined />}
            onClick={() => router.push("/connectors?tab=deliveries")}
          >
            发放详情
          </Button>
        ) : null
      ),
    },
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
        width={640}
        destroyOnClose
      >
        {benefitType === "cash_red_packet" && (
          <Alert
            message="微信现金红包"
            description="单笔转账上限 200 元，金额以元为单位输入，系统自动转换为分存储。请确保已配置微信支付转账连接器。"
            type="info"
            showIcon
            className="mb-4"
          />
        )}
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
          <BenefitConfigFields
            benefitType={benefitType}
            form={form}
            couponPoolConnectors={couponPoolConnectors}
            wechatPayConnectors={wechatPayConnectors}
          />
        </Form>
      </Modal>
    </div>
  );
}
