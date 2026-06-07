"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Divider,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Progress,
  Radio,
  Row,
  Col,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { EyeOutlined, LinkOutlined, PlusOutlined, ReloadOutlined, SendOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { useCrud } from "@/lib/hooks";
import { BenefitConfigFields } from "./_components/BenefitConfigFields";
import { BENEFIT_TYPE_MAP, BENEFIT_STATUS_MAP, CLAIM_STATUS_MAP, DELIVERY_STATUS_MAP } from "./_components/constants";
import { yuanToFen, fenToYuan } from "./_components/utils";
import type { Benefit, BenefitClaim, BenefitDelivery, BenefitSummary, Campaign, Connector } from "./_components/types";

const { Text, Title } = Typography;

const DEFAULT_SUMMARY: BenefitSummary = {
  total: 0,
  active: 0,
  unused: 0,
  stock_total: 0,
  stock_used: 0,
  stock_remaining: 0,
  claim_count: 0,
  failed_delivery_count: 0,
};

function summarizeVisibleBenefits(benefits: Benefit[], total: number, claimCount: number): BenefitSummary {
  const stockTotal = benefits.reduce((sum, benefit) => sum + (benefit.stock_total || 0), 0);
  const stockUsed = benefits.reduce((sum, benefit) => sum + (benefit.stock_used || 0), 0);
  return {
    ...DEFAULT_SUMMARY,
    total,
    active: benefits.filter((benefit) => benefit.status === "active").length,
    unused: benefits.filter((benefit) => !benefit.campaign_id).length,
    stock_total: stockTotal,
    stock_used: stockUsed,
    stock_remaining: Math.max(stockTotal - stockUsed, 0),
    claim_count: claimCount,
  };
}

const VALIDITY_OPTIONS = [
  { value: "campaign_period", label: "随活动期有效" },
  { value: "after_claim_days", label: "领取后 N 天有效" },
  { value: "fixed_range", label: "固定日期范围" },
];

function formatDate(value?: string | null) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function formatClaimDate(value?: string | null) {
  if (!value) return <Text type="secondary">时间未知</Text>;
  return formatDate(value);
}

function shortText(value?: string | null, length = 16) {
  if (!value) return "-";
  return value.length > length ? `${value.slice(0, length)}...` : value;
}

function renderCopyableText(value?: string | null, length = 16) {
  if (!value) return "-";
  return (
    <Tooltip title={value}>
      <Text copyable={{ text: value }}>{shortText(value, length)}</Text>
    </Tooltip>
  );
}

function benefitNameSuggestion(benefitType: string, campaignName?: string) {
  const prefix = campaignName ? `${campaignName} ` : "";
  const labels: Record<string, string> = {
    platform_coupon: "复购券",
    external_link: "专属入口",
    private_domain: "专属服务",
    form_benefit: "报名表单",
    cash_red_packet: "现金红包",
  };
  return `${prefix}${labels[benefitType] || "权益"}`.trim();
}

export default function BenefitsPage() {
  const searchParams = useSearchParams();
  const { message } = App.useApp();
  const {
    items: benefits,
    total: benefitsTotal,
    page: benefitsPage,
    loading: benefitsLoading,
    setPage: setBenefitsPage,
    setFilter: setBenefitFilter,
    resetFilters,
    update: updateBenefit,
    remove: removeBenefit,
    mutate: mutateBenefits,
  } = useCrud<Benefit>("/benefits");

  const {
    items: claims,
    total: claimsTotal,
    page: claimsPage,
    loading: claimsLoading,
    setPage: setClaimsPage,
    setFilter: setClaimFilter,
    resetFilters: resetClaimFilters,
    mutate: mutateClaims,
  } = useCrud<BenefitClaim>("/benefits/admin/claims");

  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [allConnectors, setAllConnectors] = useState<Connector[]>([]);
  const [summary, setSummary] = useState<BenefitSummary>(DEFAULT_SUMMARY);
  const [summaryUnavailable, setSummaryUnavailable] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [editItem, setEditItem] = useState<Benefit | null>(null);
  const [activeTab, setActiveTab] = useState("benefits");
  const [benefitType, setBenefitType] = useState<string>("");
  const [contextCampaignId, setContextCampaignId] = useState<string | null>(null);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [claimFilters, setClaimFilters] = useState<Record<string, string>>({});
  const [claimDetailOpen, setClaimDetailOpen] = useState(false);
  const [selectedClaim, setSelectedClaim] = useState<BenefitClaim | null>(null);
  const [deliveryDetail, setDeliveryDetail] = useState<BenefitDelivery | null>(null);
  const [deliveryLoading, setDeliveryLoading] = useState(false);
  const [retryingDelivery, setRetryingDelivery] = useState(false);
  const [nameManuallyEdited, setNameManuallyEdited] = useState(false);
  const [form] = Form.useForm();
  const validityType = Form.useWatch(["config_json", "validity_type"], form);

  const selectedCampaign = contextCampaignId ? campaigns.find((campaign) => campaign.id === contextCampaignId) : undefined;
  const campaignMap = useMemo(() => campaigns.reduce<Record<string, string>>((acc, c) => { acc[c.id] = c.name; return acc; }, {}), [campaigns]);
  const benefitMap = useMemo(() => benefits.reduce<Record<string, string>>((acc, b) => { acc[b.id] = b.name; return acc; }, {}), [benefits]);
  const wechatPayConnectors = allConnectors.filter((c) => c.connector_type === "wechat_pay_transfer");
  const couponPoolConnectors = allConnectors.filter((c) => c.connector_type === "coupon_pool");
  const displaySummary = useMemo(
    () => summaryUnavailable ? summarizeVisibleBenefits(benefits, benefitsTotal, claimsTotal) : summary,
    [benefits, benefitsTotal, claimsTotal, summary, summaryUnavailable],
  );

  const fetchSummary = useCallback(async () => {
    try {
      const { data } = await api.get("/benefits/summary");
      setSummary({ ...DEFAULT_SUMMARY, ...(data || {}) });
      setSummaryUnavailable(false);
    } catch {
      setSummaryUnavailable(true);
    }
  }, []);

  const refreshAll = useCallback(() => {
    mutateBenefits();
    mutateClaims();
    fetchSummary();
  }, [fetchSummary, mutateBenefits, mutateClaims]);

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

  useEffect(() => { fetchCampaigns(); fetchConnectors(); fetchSummary(); }, [fetchCampaigns, fetchConnectors, fetchSummary]);

  useEffect(() => {
    const campaignId = searchParams.get("campaign_id");
    setContextCampaignId(campaignId);
    if (campaignId) {
      setActiveTab("benefits");
      const next = { ...filters, campaign_id: campaignId };
      setFilters(next);
      setBenefitFilter(next);
    }
  }, [searchParams]); // eslint-disable-line react-hooks/exhaustive-deps

  const applyFilters = (next: Record<string, string>) => {
    const compact = Object.fromEntries(Object.entries(next).filter(([, value]) => value)) as Record<string, string>;
    setFilters(compact);
    setBenefitFilter(compact);
  };

  const clearFilters = () => {
    setFilters({});
    resetFilters();
  };

  const applyClaimFilters = (next: Record<string, string>) => {
    const compact = Object.fromEntries(Object.entries(next).filter(([, value]) => value)) as Record<string, string>;
    setClaimFilters(compact);
    setClaimFilter(compact);
  };

  const clearClaimFilters = () => {
    setClaimFilters({});
    resetClaimFilters();
  };

  const fetchDeliveryDetail = useCallback(async (deliveryId: string) => {
    setDeliveryLoading(true);
    try {
      const { data } = await api.get(`/connectors/deliveries/${deliveryId}`);
      setDeliveryDetail(data);
    } catch {
      setDeliveryDetail(null);
      message.error("加载发放详情失败");
    } finally {
      setDeliveryLoading(false);
    }
  }, [message]);

  const openClaimDetail = (record: BenefitClaim) => {
    setSelectedClaim(record);
    setDeliveryDetail(null);
    setClaimDetailOpen(true);
    if (record.latest_delivery_id) {
      fetchDeliveryDetail(record.latest_delivery_id);
    }
  };

  const retryDelivery = async () => {
    if (!selectedClaim?.latest_delivery_id) return;
    setRetryingDelivery(true);
    try {
      await api.post(`/connectors/deliveries/${selectedClaim.latest_delivery_id}/retry`);
      message.success("已触发重试发放");
      await fetchDeliveryDetail(selectedClaim.latest_delivery_id);
      mutateClaims();
      fetchSummary();
    } catch {
      message.error("重试发放失败");
    } finally {
      setRetryingDelivery(false);
    }
  };

  const openCreate = () => {
    setEditItem(null);
    setBenefitType("");
    setNameManuallyEdited(false);
    form.resetFields();
    form.setFieldsValue({
      campaign_id: contextCampaignId || null,
      stock_total: 100,
      per_person_limit: 1,
      config_json: { validity_type: contextCampaignId ? "campaign_period" : undefined },
    });
    setModalOpen(true);
  };

  const openEdit = (record: Benefit) => {
    setEditItem(record);
    setBenefitType(record.benefit_type);
    setNameManuallyEdited(true);
    const configJson = { ...record.config_json } as Record<string, unknown>;
    if (record.benefit_type === "cash_red_packet") {
      for (const field of ["fixed_amount", "min_amount", "max_amount", "lucky_min_per", "budget"] as const) {
        if (typeof configJson[field] === "number") configJson[field] = fenToYuan(configJson[field] as number);
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

  const handleSubmit = async (values: Record<string, unknown>) => {
    setSubmitting(true);
    try {
      const configJson = { ...((values.config_json as Record<string, number | string | boolean>) || {}) };
      if (values.benefit_type === "cash_red_packet") {
        for (const field of ["fixed_amount", "min_amount", "max_amount", "lucky_min_per", "budget"]) {
          if (typeof configJson[field] === "number") configJson[field] = yuanToFen(configJson[field] as number);
        }
      }
      const payload = {
        name: values.name,
        benefit_type: values.benefit_type,
        stock_total: values.stock_total,
        per_person_limit: values.per_person_limit,
        config_json: configJson,
        connector_id: values.connector_id || null,
      };
      if (editItem) {
        await updateBenefit(editItem.id, payload);
        message.success("权益已保存");
      } else if (contextCampaignId) {
        await api.post(`/campaigns/${contextCampaignId}/benefits`, payload);
        message.success("权益已创建并用于当前活动");
      } else {
        await api.post("/benefits", payload);
        message.success("权益已创建，可在活动中选择使用");
      }
      setModalOpen(false);
      form.resetFields();
      refreshAll();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || (editItem ? "保存权益失败" : "创建权益失败"));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (record: Benefit) => {
    try {
      await removeBenefit(record.id);
      message.success("权益已删除");
      refreshAll();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "删除失败");
    }
  };

  const handleToggleStatus = async (record: Benefit) => {
    const newStatus = record.status === "active" ? "inactive" : "active";
    try {
      await updateBenefit(record.id, { status: newStatus });
      message.success(newStatus === "active" ? "权益已启用" : "权益已停用");
      refreshAll();
    } catch {
      message.error("操作失败");
    }
  };

  const handleUseInCampaign = async (record: Benefit) => {
    if (!contextCampaignId) return;
    try {
      await api.post(`/campaigns/${contextCampaignId}/benefits/${record.id}/attach`);
      message.success("已用于当前活动");
      refreshAll();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "使用权益失败");
    }
  };

  const handleDetachFromCampaign = async (record: Benefit) => {
    if (!contextCampaignId) return;
    try {
      await api.delete(`/campaigns/${contextCampaignId}/benefits/${record.id}/attach`);
      message.success("已取消用于当前活动");
      refreshAll();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "取消使用失败");
    }
  };

  const handleValuesChange = (changed: Record<string, unknown>) => {
    if (Object.prototype.hasOwnProperty.call(changed, "name")) setNameManuallyEdited(true);
    if (changed.benefit_type) {
      const nextType = changed.benefit_type as string;
      setBenefitType(nextType);
      if (!nameManuallyEdited && !editItem) {
        form.setFieldsValue({ name: benefitNameSuggestion(nextType, selectedCampaign?.name) });
      }
    }
  };

  const getBenefitValidityText = (record: Benefit) => {
    const validity = record.config_json?.validity_period;
    if (typeof validity === "string" && validity.trim()) return validity;
    if (record.config_json?.validity_type === "campaign_period") return "随活动期有效";
    if (typeof record.config_json?.validity_days === "number") return `领取后 ${record.config_json.validity_days} 天内有效`;
    return "未设置";
  };

  const benefitColumns: ColumnsType<Benefit> = [
    {
      title: "权益",
      dataIndex: "name",
      key: "name",
      render: (_: unknown, record) => {
        const info = BENEFIT_TYPE_MAP[record.benefit_type] || { label: record.benefit_type, color: "default", description: "" };
        return (
          <div>
            <div className="font-medium text-text-heading">{record.name}</div>
            <Space size={6} className="mt-1">
              <Tag color={info.color}>{info.label}</Tag>
              <Text type="secondary" className="text-xs">{getBenefitValidityText(record)}</Text>
            </Space>
          </div>
        );
      },
    },
    {
      title: contextCampaignId ? "使用状态" : "使用活动",
      dataIndex: "campaign_id",
      key: "campaign_id",
      render: (v: string | null) => {
        if (!v) return <Tag>未使用</Tag>;
        const campaignName = contextCampaignId && v === contextCampaignId ? "当前活动" : campaignMap[v] || "未找到活动";
        return <Text ellipsis={{ tooltip: campaignName }} className="block max-w-[180px]">{campaignName}</Text>;
      },
    },
    {
      title: "库存",
      key: "stock",
      render: (_: unknown, record) => {
        const remaining = Math.max(record.stock_total - record.stock_used, 0);
        const percent = record.stock_total > 0 ? Math.round((record.stock_used / record.stock_total) * 100) : 0;
        return (
          <div className="min-w-[150px]">
            <div className="mb-1 text-xs text-text-muted">已领 {record.stock_used} / {record.stock_total}，剩余 {remaining}</div>
            <Progress percent={percent} size="small" status={remaining <= 0 ? "exception" : percent >= 80 ? "active" : undefined} />
          </div>
        );
      },
    },
    { title: "每位限领", dataIndex: "per_person_limit", key: "per_person_limit", width: 96 },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 96,
      render: (s: string, record: Benefit) => (
        <Switch
          checked={s === "active"}
          checkedChildren="启用"
          unCheckedChildren="停用"
          onChange={async (checked) => {
            try {
              await updateBenefit(record.id, { status: checked ? "active" : "inactive" });
              message.success(checked ? "权益已启用" : "权益已停用");
              refreshAll();
            } catch {
              message.error("操作失败");
            }
          }}
        />
      ),
    },
    { title: "创建时间", dataIndex: "created_at", key: "created_at", width: 180, render: formatDate },
    {
      title: "操作",
      key: "actions",
      width: 260,
      render: (_: unknown, record) => (
        <Space wrap>
          {contextCampaignId && !record.campaign_id && <Button size="small" type="primary" onClick={() => handleUseInCampaign(record)}>用于此活动</Button>}
          {contextCampaignId && record.campaign_id === contextCampaignId && (
            <Popconfirm title="确认取消当前活动使用此权益？" onConfirm={() => handleDetachFromCampaign(record)}>
              <Button size="small">取消使用</Button>
            </Popconfirm>
          )}
          <Button size="small" onClick={() => openEdit(record)}>编辑</Button>
          <Popconfirm title="仅未使用且无领取记录的权益可删除，确认删除？" onConfirm={() => handleDelete(record)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const claimColumns: ColumnsType<BenefitClaim> = [
    { title: "消费者", dataIndex: "consumer_id", key: "consumer_id", render: (v: string) => renderCopyableText(v, 16) },
    { title: "权益", dataIndex: "benefit_name", key: "benefit_name", render: (_: unknown, record) => renderCopyableText(record.benefit_name || benefitMap[record.benefit_id] || record.benefit_id, 18) },
    { title: "活动", dataIndex: "campaign_name", key: "campaign_name", render: (_: unknown, record) => renderCopyableText(record.campaign_name || (record.campaign_id ? campaignMap[record.campaign_id] || record.campaign_id : "独立权益"), 20) },
    { title: "领取状态", dataIndex: "status", key: "status", render: (s: string) => { const info = CLAIM_STATUS_MAP[s] || { label: s, color: "default" }; return <Tag color={info.color}>{info.label}</Tag>; } },
    { title: "发放状态", dataIndex: "delivery_status", key: "delivery_status", render: (s: string) => { const info = DELIVERY_STATUS_MAP[s] || { label: s || "-", color: "default" }; return <Tag color={info.color}>{info.label}</Tag>; } },
    { title: "领取时间", dataIndex: "claimed_at", key: "claimed_at", render: formatClaimDate },
    { title: "操作", key: "actions", render: (_: unknown, record) => (
      <Button size="small" icon={<EyeOutlined />} onClick={() => openClaimDetail(record)}>查看详情</Button>
    ) },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <Title level={4} className="!mb-1">权益管理</Title>
          <Text type="secondary">管理可复用权益、活动使用关系、库存与消费者领取记录。</Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={refreshAll}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            {contextCampaignId ? "新建并用于此活动" : "新建权益"}
          </Button>
        </Space>
      </div>

      <Row gutter={12}>
        <Col xs={12} md={6}><Card size="small"><Statistic title="权益总数" value={displaySummary.total} /></Card></Col>
        <Col xs={12} md={6}><Card size="small"><Statistic title="启用中" value={displaySummary.active} /></Card></Col>
        <Col xs={12} md={6}><Card size="small"><Statistic title="剩余库存" value={displaySummary.stock_remaining} suffix={`/ ${displaySummary.stock_total}`} /></Card></Col>
        <Col xs={12} md={6}><Card size="small"><Statistic title="领取记录" value={displaySummary.claim_count} /></Card></Col>
      </Row>

      {contextCampaignId ? (
        <Alert
          type="info"
          showIcon
          title={`当前活动：${selectedCampaign?.name || "活动"}`}
          description="可以选择未使用的权益用于当前活动，也可以直接创建活动专用权益。已有领取记录的权益不能从活动中取消使用。"
        />
      ) : null}

      <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
        {
          key: "benefits",
          label: "权益列表",
          children: (
            <Card size="small">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <Input.Search
                  allowClear
                  placeholder="搜索权益名称"
                  className="w-[240px]"
                  onSearch={(q) => applyFilters({ ...filters, q })}
                />
                <Select
                  allowClear
                  placeholder="权益类型"
                  className="w-[160px]"
                  value={filters.benefit_type}
                  onChange={(benefit_type) => applyFilters({ ...filters, benefit_type })}
                  options={Object.entries(BENEFIT_TYPE_MAP).map(([value, info]) => ({ value, label: info.label }))}
                />
                <Select
                  allowClear
                  placeholder="状态"
                  className="w-[140px]"
                  value={filters.status}
                  onChange={(status) => applyFilters({ ...filters, status })}
                  options={Object.entries(BENEFIT_STATUS_MAP).map(([value, info]) => ({ value, label: info.label }))}
                />
                <Select
                  allowClear
                  placeholder="使用状态"
                  className="w-[140px]"
                  value={filters.usage}
                  onChange={(usage) => applyFilters({ ...filters, usage })}
                  options={[{ value: "unused", label: "未使用" }, { value: "used", label: "已用于活动" }]}
                />
                <Button onClick={clearFilters}>清空筛选</Button>
              </div>
              <Table
                columns={benefitColumns}
                dataSource={benefits}
                rowKey="id"
                loading={benefitsLoading}
                pagination={{ current: benefitsPage, total: benefitsTotal, pageSize: 20, onChange: setBenefitsPage, showTotal: (t) => `共 ${t} 条` }}
              />
            </Card>
          ),
        },
        {
          key: "claims",
          label: "领取记录",
          children: (
            <Card size="small">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <Input.Search
                  allowClear
                  placeholder="搜索消费者/权益/活动"
                  className="w-[260px]"
                  onSearch={(q) => applyClaimFilters({ ...claimFilters, q })}
                />
                <Select
                  allowClear
                  showSearch
                  placeholder="权益"
                  className="w-[180px]"
                  value={claimFilters.benefit_id}
                  onChange={(benefit_id) => applyClaimFilters({ ...claimFilters, benefit_id })}
                  options={benefits.map((benefit) => ({ value: benefit.id, label: benefit.name }))}
                  optionFilterProp="label"
                />
                <Select
                  allowClear
                  showSearch
                  placeholder="活动"
                  className="w-[180px]"
                  value={claimFilters.campaign_id}
                  onChange={(campaign_id) => applyClaimFilters({ ...claimFilters, campaign_id })}
                  options={campaigns.map((campaign) => ({ value: campaign.id, label: campaign.name }))}
                  optionFilterProp="label"
                />
                <Select
                  allowClear
                  placeholder="领取状态"
                  className="w-[140px]"
                  value={claimFilters.status}
                  onChange={(status) => applyClaimFilters({ ...claimFilters, status })}
                  options={[
                    { value: "claimed", label: "已领取" },
                    { value: "pending", label: "待领取" },
                    { value: "used", label: "已使用" },
                    { value: "expired", label: "已过期" },
                    { value: "cancelled", label: "已取消" },
                  ]}
                />
                <Select
                  allowClear
                  placeholder="发放状态"
                  className="w-[140px]"
                  value={claimFilters.delivery_status}
                  onChange={(delivery_status) => applyClaimFilters({ ...claimFilters, delivery_status })}
                  options={Object.entries(DELIVERY_STATUS_MAP).map(([value, info]) => ({ value, label: info.label }))}
                />
                <Button onClick={clearClaimFilters}>清空筛选</Button>
              </div>
              <Table
                columns={claimColumns}
                dataSource={claims}
                rowKey="id"
                loading={claimsLoading}
                pagination={{ current: claimsPage, total: claimsTotal, pageSize: 20, onChange: setClaimsPage, showTotal: (t) => `共 ${t} 条` }}
              />
            </Card>
          ),
        },
      ]} />

      <Drawer
        title="领取详情"
        open={claimDetailOpen}
        onClose={() => setClaimDetailOpen(false)}
        size="large"
        extra={selectedClaim?.latest_delivery_id && (selectedClaim.delivery_status === "pending" || selectedClaim.delivery_status === "failed") ? (
          <Button type="primary" icon={<SendOutlined />} loading={retryingDelivery} onClick={retryDelivery}>重试发放</Button>
        ) : null}
      >
        {selectedClaim ? (
          <Space orientation="vertical" size="middle" className="w-full">
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="消费者">{renderCopyableText(selectedClaim.consumer_id, 28)}</Descriptions.Item>
              <Descriptions.Item label="权益">{selectedClaim.benefit_name || benefitMap[selectedClaim.benefit_id] || selectedClaim.benefit_id}</Descriptions.Item>
              <Descriptions.Item label="活动">{selectedClaim.campaign_name || (selectedClaim.campaign_id ? campaignMap[selectedClaim.campaign_id] || selectedClaim.campaign_id : "独立权益")}</Descriptions.Item>
              <Descriptions.Item label="领取状态">{CLAIM_STATUS_MAP[selectedClaim.status]?.label || selectedClaim.status}</Descriptions.Item>
              <Descriptions.Item label="发放状态">{DELIVERY_STATUS_MAP[selectedClaim.delivery_status]?.label || selectedClaim.delivery_status}</Descriptions.Item>
              <Descriptions.Item label="领取时间">{formatClaimDate(selectedClaim.claimed_at)}</Descriptions.Item>
            </Descriptions>
            {selectedClaim.latest_delivery_id ? (
              <Card size="small" title="发放详情" loading={deliveryLoading}>
                {deliveryDetail ? (
                  <Space orientation="vertical" size="small" className="w-full">
                    <Descriptions size="small" column={1}>
                      <Descriptions.Item label="发放状态">{DELIVERY_STATUS_MAP[deliveryDetail.status]?.label || deliveryDetail.status}</Descriptions.Item>
                      <Descriptions.Item label="重试次数">{deliveryDetail.retry_count} / {deliveryDetail.max_retries}</Descriptions.Item>
                      <Descriptions.Item label="下次重试">{formatDate(deliveryDetail.next_retry_at)}</Descriptions.Item>
                      <Descriptions.Item label="更新时间">{formatDate(deliveryDetail.updated_at)}</Descriptions.Item>
                    </Descriptions>
                    <pre className="max-h-56 overflow-auto rounded bg-slate-50 p-3 text-xs">
                      {JSON.stringify(deliveryDetail.external_data || {}, null, 2)}
                    </pre>
                  </Space>
                ) : (
                  <Text type="secondary">暂无发放详情</Text>
                )}
              </Card>
            ) : (
              <Text type="secondary">此记录无需外部发放。</Text>
            )}
          </Space>
        ) : null}
      </Drawer>

      <Modal
        title={editItem ? "编辑权益" : selectedCampaign ? `新建权益并用于「${selectedCampaign.name}」` : "新建权益"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        okText={editItem ? "保存权益" : "创建权益"}
        confirmLoading={submitting}
        width={760}
        forceRender
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit} onValuesChange={handleValuesChange}>
          {contextCampaignId || editItem?.campaign_id ? (
            <Alert
              className="mb-4"
              type="info"
              showIcon
              title={editItem ? `使用活动：${editItem.campaign_id ? campaignMap[editItem.campaign_id] || "活动" : "未使用"}` : `创建后用于：${selectedCampaign?.name || "当前活动"}`}
              description="权益用于活动后，会参与活动页面展示、领取校验和库存统计。"
            />
          ) : null}

          <Divider titlePlacement="start" plain>基本信息</Divider>
          <Form.Item name="name" label="权益名称" rules={[{ required: true, message: "请输入权益名称" }]} extra="建议使用消费者能理解的名称，例如“20 元复购券”。">
            <Input data-testid="benefit-name-input" placeholder="例如 20 元复购券" maxLength={200} />
          </Form.Item>

          <Form.Item name="benefit_type" label="权益类型" rules={[{ required: true, message: "请选择权益类型" }]}>
            <Radio.Group className="grid w-full grid-cols-1 gap-2 md:grid-cols-2" data-testid="benefit-type-select">
              {Object.entries(BENEFIT_TYPE_MAP).map(([value, info]) => (
                <Radio key={value} value={value} className="rounded border border-border-subtle p-3 [&>span:last-child]:w-full">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-medium">{info.label}</div>
                      <div className="mt-1 text-xs text-text-muted">{info.description}</div>
                    </div>
                    <Tag color={info.color}>{info.label}</Tag>
                  </div>
                </Radio>
              ))}
            </Radio.Group>
          </Form.Item>

          <Form.Item name="campaign_id" hidden><Input /></Form.Item>

          <Divider titlePlacement="start" plain>发放规则</Divider>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Form.Item name="stock_total" label="总库存" rules={[{ required: true, message: "请输入总库存" }]} extra={editItem ? `已领取 ${editItem.stock_used}，总库存不能低于已领取数量。` : "可发放的总数量。"}>
              <InputNumber min={Math.max(editItem?.stock_used || 1, 1)} className="w-full" data-testid="benefit-stock-input" addonAfter="份" />
            </Form.Item>
            <Form.Item name="per_person_limit" label="每位消费者限领" rules={[{ required: true, message: "请输入每位消费者限领数量" }]}>
              <InputNumber min={1} className="w-full" data-testid="benefit-limit-input" addonAfter="次" />
            </Form.Item>
          </div>

          <Form.Item name={["config_json", "validity_type"]} label="权益有效期">
            <Select allowClear placeholder="选择有效期规则" options={VALIDITY_OPTIONS} />
          </Form.Item>
          {validityType === "after_claim_days" && (
            <Form.Item name={["config_json", "validity_days"]} label="领取后有效天数" rules={[{ required: true, message: "请输入有效天数" }]}>
              <InputNumber min={1} className="w-full" addonAfter="天" />
            </Form.Item>
          )}
          {validityType === "fixed_range" && (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <Form.Item name={["config_json", "validity_start_at"]} label="有效期开始" rules={[{ required: true, message: "请输入开始时间" }]}>
                <Input placeholder="2026-06-01T00:00:00" />
              </Form.Item>
              <Form.Item name={["config_json", "validity_end_at"]} label="有效期结束" rules={[{ required: true, message: "请输入结束时间" }]}>
                <Input placeholder="2026-06-30T23:59:59" />
              </Form.Item>
            </div>
          )}

          {benefitType === "cash_red_packet" && (
            <Alert title="微信现金红包" description="金额以元为单位输入，保存时会转换为分存储。请确保已配置微信支付转账连接器。" type="info" showIcon className="mb-4" />
          )}
          <BenefitConfigFields benefitType={benefitType} form={form} couponPoolConnectors={couponPoolConnectors} wechatPayConnectors={wechatPayConnectors} />

          {benefitType && (
            <Alert
              className="mt-4"
              type="success"
              showIcon
              icon={<LinkOutlined />}
              message="领取闭环"
              description="创建后可在活动中使用，消费者扫码领取时会按库存、限领、有效期和履约配置执行。领取结果会进入领取记录。"
            />
          )}
        </Form>
      </Modal>
    </div>
  );
}
