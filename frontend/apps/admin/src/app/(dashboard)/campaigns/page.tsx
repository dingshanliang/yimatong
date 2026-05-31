"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Alert,
  App,
  Button,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import { BarChartOutlined, CopyOutlined, GiftOutlined, PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import api from "@/lib/api";
import { useCrud } from "@/lib/hooks";

const { RangePicker } = DatePicker;
const { Text, Title } = Typography;
const { TextArea } = Input;

type CampaignStatus = "draft" | "active" | "paused" | "ended";
type ComputedCampaignStatus = CampaignStatus | "pending";

interface Campaign {
  id: string;
  name: string;
  campaign_type: string;
  status: CampaignStatus;
  computed_status: ComputedCampaignStatus;
  product_id?: string | null;
  product_name?: string | null;
  start_at: string;
  end_at: string;
  description?: string | null;
  rules_json?: Record<string, unknown>;
  benefit_count: number;
  stock_total: number;
  stock_used: number;
  claim_count: number;
}

interface ProductOption {
  id: string;
  name: string;
  category?: string | null;
}

interface CampaignFormValues {
  name: string;
  campaign_type: string;
  product_id?: string;
  active_range: [Dayjs, Dayjs];
  description?: string;
  participation_conditions?: string;
  claim_limits?: string;
  validity_period?: string;
  disclaimer?: string;
  minor_notice?: string;
  customer_service_contact?: string;
}

const TYPE_OPTIONS = [
  { value: "coupon", label: "优惠券" },
  { value: "lottery", label: "抽奖" },
  { value: "points", label: "积分" },
];

const STATUS_FILTER_OPTIONS = [
  { value: "draft", label: "草稿" },
  { value: "pending", label: "待开始" },
  { value: "active", label: "进行中" },
  { value: "paused", label: "已暂停" },
  { value: "ended", label: "已结束" },
];

const STATUS_MAP: Record<ComputedCampaignStatus, { label: string; color: string }> = {
  draft: { label: "草稿", color: "default" },
  pending: { label: "待开始", color: "geekblue" },
  active: { label: "进行中", color: "green" },
  paused: { label: "已暂停", color: "orange" },
  ended: { label: "已结束", color: "gray" },
};

function formatDateTime(value?: string | null) {
  if (!value) return "未设置";
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format("YYYY-MM-DD HH:mm") : value;
}

function formatCampaignTime(record: Campaign) {
  const start = formatDateTime(record.start_at);
  const end = formatDateTime(record.end_at);
  const status = getDisplayStatus(record);
  if (status === "active") {
    const endAt = dayjs(record.end_at);
    if (endAt.isValid()) {
      const days = Math.max(endAt.endOf("day").diff(dayjs(), "day"), 0);
      return { range: `${start} 至 ${end}`, hint: `剩余 ${days} 天` };
    }
  }
  if (status === "pending") return { range: `${start} 至 ${end}`, hint: "未开始" };
  if (status === "ended") return { range: `${start} 至 ${end}`, hint: "已结束" };
  return { range: `${start} 至 ${end}`, hint: "" };
}

function getDisplayStatus(record: Campaign): ComputedCampaignStatus {
  return record.computed_status || record.status || "draft";
}

function getBenefitCount(record: Campaign) {
  return record.benefit_count ?? 0;
}

function getStockTotal(record: Campaign) {
  return record.stock_total ?? 0;
}

function getStockUsed(record: Campaign) {
  return record.stock_used ?? 0;
}

function getClaimCount(record: Campaign) {
  return record.claim_count ?? 0;
}

function toDateRange(startAt?: string, endAt?: string): [Dayjs, Dayjs] | undefined {
  const start = dayjs(startAt);
  const end = dayjs(endAt);
  if (!start.isValid() || !end.isValid()) return undefined;
  return [start, end];
}

function rulesFromCampaign(record?: Campaign | null) {
  return (record?.rules_json || {}) as Record<string, string>;
}

export default function CampaignsPage() {
  const router = useRouter();
  const { message, modal } = App.useApp();
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<Campaign | null>(null);
  const [detailItem, setDetailItem] = useState<Campaign | null>(null);
  const [products, setProducts] = useState<ProductOption[]>([]);
  const [form] = Form.useForm<CampaignFormValues>();

  const {
    items: campaigns,
    total,
    page,
    loading,
    filters,
    setPage,
    setFilter,
    resetFilters,
    mutate,
    create,
    update,
    remove,
  } = useCrud<Campaign>("/campaigns");

  const productOptions = useMemo(
    () => products.map((p) => ({ value: p.id, label: p.category ? `${p.name} · ${p.category}` : p.name })),
    [products],
  );

  const fetchProducts = useCallback(async () => {
    try {
      const { data } = await api.get("/products", { params: { page_size: 100 } });
      setProducts(data.items || []);
    } catch {
      setProducts([]);
    }
  }, []);

  useEffect(() => {
    fetchProducts();
  }, [fetchProducts]);

  const openCreate = () => {
    setEditItem(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (record: Campaign) => {
    const rules = rulesFromCampaign(record);
    setEditItem(record);
    form.setFieldsValue({
      name: record.name,
      campaign_type: record.campaign_type,
      product_id: record.product_id || undefined,
      active_range: toDateRange(record.start_at, record.end_at),
      description: record.description || undefined,
      participation_conditions: rules.participation_conditions || "",
      claim_limits: rules.claim_limits || "",
      validity_period: rules.validity_period || "",
      disclaimer: rules.disclaimer || "",
      minor_notice: rules.minor_notice || "",
      customer_service_contact: rules.customer_service_contact || "",
    });
    setModalOpen(true);
  };

  const buildPayload = (values: CampaignFormValues) => {
    const [startAt, endAt] = values.active_range;
    return {
      name: values.name,
      campaign_type: values.campaign_type,
      product_id: values.product_id || null,
      start_at: startAt.format("YYYY-MM-DDTHH:mm:ss"),
      end_at: endAt.format("YYYY-MM-DDTHH:mm:ss"),
      description: values.description || null,
      rules_json: {
        participation_conditions: values.participation_conditions || "",
        claim_limits: values.claim_limits || "每人限领1次",
        validity_period: values.validity_period || "领取后7天有效",
        disclaimer: values.disclaimer || "最终解释权归品牌方所有",
        minor_notice: values.minor_notice || "未成年人请在监护人陪同下参与",
        customer_service_contact: values.customer_service_contact || "",
      },
    };
  };

  const handleSubmit = async (values: CampaignFormValues) => {
    try {
      const payload = buildPayload(values);
      if (editItem) {
        await update(editItem.id, payload);
        message.success("活动已更新");
      } else {
        await create(payload);
        message.success("活动草稿已创建，请继续配置权益后上线");
      }
      setModalOpen(false);
      form.resetFields();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || (editItem ? "更新活动失败" : "创建活动失败"));
    }
  };

  const changeStatus = async (record: Campaign, status: CampaignStatus, successText: string) => {
    try {
      await api.post(`/campaigns/${record.id}/status`, { status });
      message.success(successText);
      mutate();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "操作失败");
    }
  };

  const confirmActivate = (record: Campaign) => {
    modal.confirm({
      title: "确认上线活动？",
      okText: "上线活动",
      cancelText: "取消",
      content: (
        <div className="space-y-3">
          <Alert
            type={getBenefitCount(record) > 0 ? "info" : "warning"}
            showIcon
            title={getBenefitCount(record) > 0 ? "上线后消费者可在活动期内参与。" : "当前还没有配置权益，上线后消费者可能无法领取奖励。"}
          />
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="活动">{record.name}</Descriptions.Item>
            <Descriptions.Item label="关联产品">{record.product_name || "未关联产品"}</Descriptions.Item>
            <Descriptions.Item label="活动时间">{formatCampaignTime(record).range}</Descriptions.Item>
            <Descriptions.Item label="权益配置">{getBenefitCount(record) > 0 ? `${getBenefitCount(record)} 个权益` : "未配置权益"}</Descriptions.Item>
          </Descriptions>
        </div>
      ),
      onOk: () => changeStatus(record, "active", "活动已上线"),
    });
  };

  const confirmPause = (record: Campaign) => {
    modal.confirm({
      title: "确认暂停活动？",
      okText: "暂停活动",
      cancelText: "取消",
      content: "暂停后消费者暂时无法参与活动，已领取权益不会删除，活动数据会保留。",
      onOk: () => changeStatus(record, "paused", "活动已暂停"),
    });
  };

  const copyCampaign = async (record: Campaign) => {
    try {
      await api.post("/campaigns", {
        name: `${record.name} 副本`,
        campaign_type: record.campaign_type,
        product_id: record.product_id || null,
        start_at: record.start_at,
        end_at: record.end_at,
        description: record.description,
        rules_json: record.rules_json || {},
      });
      message.success("活动副本已创建为草稿");
      mutate();
    } catch {
      message.error("复制活动失败");
    }
  };

  const configureBenefits = (record: Campaign) => {
    router.push(`/benefits?campaign_id=${record.id}`);
  };

  const updateFilters = (next: Record<string, string | number | undefined>) => {
    const clean = Object.fromEntries(Object.entries({ ...filters, ...next }).filter(([, v]) => v !== undefined && v !== ""));
    setFilter(clean as Record<string, string | number>);
  };

  const columns: ColumnsType<Campaign> = [
    {
      title: "活动名称",
      dataIndex: "name",
      key: "name",
      width: 220,
      render: (name: string, record) => (
        <Space orientation="vertical" size={2}>
          <Button type="link" className="h-auto !p-0 text-left" onClick={() => setDetailItem(record)}>
            {name}
          </Button>
          {record.description && <Text type="secondary" className="line-clamp-1 max-w-[260px] text-xs">{record.description}</Text>}
        </Space>
      ),
    },
    {
      title: "活动类型",
      dataIndex: "campaign_type",
      key: "campaign_type",
      width: 100,
      render: (t: string) => TYPE_OPTIONS.find((o) => o.value === t)?.label || t,
    },
    {
      title: "关联产品",
      key: "product",
      width: 180,
      render: (_, record) => (
        record.product_name ? <Text>{record.product_name}</Text> : <Tag color="warning">未关联产品</Tag>
      ),
    },
    {
      title: "活动状态",
      dataIndex: "computed_status",
      key: "computed_status",
      width: 110,
      render: (_, record) => {
        const status = getDisplayStatus(record);
        const info = STATUS_MAP[status] || { label: status, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "活动时间",
      key: "time",
      width: 250,
      render: (_, record) => {
        const time = formatCampaignTime(record);
        return (
          <Space orientation="vertical" size={2}>
            <Text>{time.range}</Text>
            {time.hint && <Text type="secondary" className="text-xs">{time.hint}</Text>}
          </Space>
        );
      },
    },
    {
      title: "权益配置",
      key: "benefits",
      width: 170,
      render: (_, record) => {
        const stockTotal = getStockTotal(record);
        const stockUsed = getStockUsed(record);
        const benefitCount = getBenefitCount(record);
        const percent = stockTotal > 0 ? Math.round((stockUsed / stockTotal) * 100) : 0;
        return (
          <Space orientation="vertical" size={2} className="min-w-[140px]">
            <Text>{benefitCount > 0 ? `${benefitCount} 个权益` : "未配置权益"}</Text>
            {stockTotal > 0 && <Progress percent={percent} size="small" showInfo={false} />}
            <Text type="secondary" className="text-xs">库存 {stockUsed}/{stockTotal}</Text>
          </Space>
        );
      },
    },
    {
      title: "参与/领取数据",
      key: "claims",
      width: 130,
      render: (_, record) => (
        <Space orientation="vertical" size={2}>
          <Text>{getClaimCount(record)} 次领取</Text>
          <Button size="small" type="link" className="h-auto !p-0" onClick={() => setDetailItem(record)}>
            查看数据
          </Button>
        </Space>
      ),
    },
    {
      title: "操作",
      key: "actions",
      fixed: "right",
      width: 220,
      render: (_, record) => (
        <Space wrap>
          {getDisplayStatus(record) === "draft" && (
            <>
              <Button size="small" onClick={() => openEdit(record)}>编辑草稿</Button>
              <Button size="small" icon={<GiftOutlined />} onClick={() => configureBenefits(record)}>配置权益</Button>
              <Button size="small" type="primary" onClick={() => confirmActivate(record)}>上线活动</Button>
              <Popconfirm title="确认删除草稿活动？" onConfirm={async () => { await remove(record.id); message.success("活动已删除"); }}>
                <Button size="small" danger>删除</Button>
              </Popconfirm>
            </>
          )}
          {(getDisplayStatus(record) === "active" || getDisplayStatus(record) === "pending") && (
            <>
              <Button size="small" icon={<BarChartOutlined />} onClick={() => setDetailItem(record)}>查看数据</Button>
              <Button size="small" onClick={() => openEdit(record)}>编辑</Button>
              <Button size="small" onClick={() => confirmPause(record)}>暂停活动</Button>
            </>
          )}
          {getDisplayStatus(record) === "paused" && (
            <>
              <Button size="small" icon={<BarChartOutlined />} onClick={() => setDetailItem(record)}>查看数据</Button>
              <Button size="small" type="primary" onClick={() => changeStatus(record, "active", "活动已恢复")}>恢复活动</Button>
            </>
          )}
          {getDisplayStatus(record) === "ended" && (
            <>
              <Button size="small" icon={<BarChartOutlined />} onClick={() => setDetailItem(record)}>查看数据</Button>
              <Button size="small" icon={<CopyOutlined />} onClick={() => copyCampaign(record)}>复制活动</Button>
            </>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <Title level={4} className="!mb-1">活动管理</Title>
          <Text type="secondary">管理扫码后的营销活动、权益配置和领取效果。</Text>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建活动
        </Button>
      </div>

      <div className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-4">
        <Input.Search
          allowClear
          placeholder="搜索活动名称"
          onSearch={(q) => updateFilters({ q })}
          onChange={(e) => { if (!e.target.value) updateFilters({ q: undefined }); }}
        />
        <Select
          allowClear
          placeholder="按状态筛选"
          options={STATUS_FILTER_OPTIONS}
          value={filters.computed_status as string | undefined}
          onChange={(computed_status) => updateFilters({ computed_status })}
        />
        <Select
          allowClear
          placeholder="按类型筛选"
          options={TYPE_OPTIONS}
          value={filters.campaign_type as string | undefined}
          onChange={(campaign_type) => updateFilters({ campaign_type })}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="按产品筛选"
          options={productOptions}
          value={filters.product_id as string | undefined}
          onChange={(product_id) => updateFilters({ product_id })}
        />
      </div>

      {Object.keys(filters).length > 0 && (
        <div className="mb-3">
          <Button size="small" onClick={resetFilters}>清空筛选</Button>
        </div>
      )}

      <Table
        columns={columns}
        dataSource={campaigns}
        rowKey="id"
        loading={loading}
        scroll={{ x: 1380 }}
        locale={{
          emptyText: (
            <Empty description="先创建活动，再配置权益并上线投放">
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建活动</Button>
            </Empty>
          ),
        }}
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
        okText={editItem ? "保存活动" : "创建草稿"}
        cancelText="取消"
        width={720}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Form.Item name="name" label="活动名称" rules={[{ required: true, message: "请输入活动名称" }]}>
              <Input data-testid="campaign-name-input" />
            </Form.Item>
            <Form.Item name="campaign_type" label="活动类型" rules={[{ required: true, message: "请选择活动类型" }]}>
              <Select options={TYPE_OPTIONS} data-testid="campaign-type-select" />
            </Form.Item>
          </div>
          <Form.Item name="product_id" label="关联产品">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="选择活动关联产品"
              options={productOptions}
            />
          </Form.Item>
          <Form.Item
            name="active_range"
            label="活动时间"
            rules={[
              { required: true, message: "请选择活动时间" },
              {
                validator: async (_, value?: [Dayjs, Dayjs]) => {
                  if (!value?.[0] || !value?.[1]) return;
                  if (value[1].isBefore(value[0])) throw new Error("结束时间必须晚于开始时间");
                },
              },
            ]}
          >
            <RangePicker showTime className="w-full" format="YYYY-MM-DD HH:mm" />
          </Form.Item>
          <Form.Item name="description" label="活动说明">
            <TextArea rows={2} data-testid="campaign-description-input" />
          </Form.Item>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Form.Item name="participation_conditions" label="参与条件">
              <TextArea rows={2} placeholder="例如：消费者扫码后即可参与" data-testid="campaign-conditions-input" />
            </Form.Item>
            <Form.Item name="claim_limits" label="领取限制">
              <Input placeholder="每人限领1次" data-testid="campaign-claim-limits-input" />
            </Form.Item>
            <Form.Item name="validity_period" label="有效期">
              <Input placeholder="领取后7天有效" data-testid="campaign-validity-input" />
            </Form.Item>
            <Form.Item name="customer_service_contact" label="客服方式">
              <Input placeholder="400-123-4567 或企业微信客服" data-testid="campaign-contact-input" />
            </Form.Item>
          </div>
          <Form.Item name="disclaimer" label="活动说明/免责声明">
            <TextArea rows={2} data-testid="campaign-disclaimer-input" />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title="活动数据"
        open={!!detailItem}
        onClose={() => setDetailItem(null)}
        size="large"
      >
        {detailItem && (
          <Space orientation="vertical" size="large" className="w-full">
            <Descriptions column={1} bordered size="small">
              <Descriptions.Item label="活动名称">{detailItem.name}</Descriptions.Item>
              <Descriptions.Item label="关联产品">{detailItem.product_name || "未关联产品"}</Descriptions.Item>
              <Descriptions.Item label="活动状态">{STATUS_MAP[getDisplayStatus(detailItem)]?.label}</Descriptions.Item>
              <Descriptions.Item label="活动时间">{formatCampaignTime(detailItem).range}</Descriptions.Item>
            </Descriptions>
            <div className="grid grid-cols-2 gap-4">
              <Statistic title="权益数" value={getBenefitCount(detailItem)} />
              <Statistic title="领取数" value={getClaimCount(detailItem)} />
              <Statistic title="总库存" value={getStockTotal(detailItem)} />
              <Statistic title="已消耗库存" value={getStockUsed(detailItem)} />
            </div>
            <Alert
              type="info"
              showIcon
              title="本轮先统计权益和领取记录；扫码到活动的精确归因会在后续接入。"
            />
            <Space>
              <Button icon={<GiftOutlined />} onClick={() => configureBenefits(detailItem)}>配置权益</Button>
              <Button onClick={() => openEdit(detailItem)}>编辑活动</Button>
            </Space>
          </Space>
        )}
      </Drawer>
    </div>
  );
}
