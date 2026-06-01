"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Alert,
  App,
  Button,
  Checkbox,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Progress,
  Segmented,
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
  campaign_goal?: string;
  name: string;
  campaign_type: string;
  product_id?: string;
  active_range: [Dayjs, Dayjs];
  description?: string;
  participation_condition_type?: "first_scan" | "any_scan" | "member_only" | "wecom_required";
  claim_limit_count?: number;
  participation_conditions?: string;
  claim_limits?: string;
  validity_period?: string;
  disclaimer?: string;
  minor_notice?: string;
  customer_service_contact?: string;
  benefit_enabled?: boolean;
  benefit_name?: string;
  benefit_stock_total?: number;
  benefit_per_person_limit?: number;
  benefit_validity_mode?: "campaign_period" | "after_claim_days" | "fixed_range";
  benefit_validity_days?: number;
  benefit_validity_range?: [Dayjs, Dayjs];
  benefit_description?: string;
}

const TYPE_OPTIONS = [
  { value: "coupon", label: "优惠券" },
  { value: "lottery", label: "抽奖" },
  { value: "points", label: "积分" },
];

const CAMPAIGN_GOAL_OPTIONS = [
  { value: "first_scan_coupon", label: "首扫领券" },
  { value: "lottery", label: "抽奖活动" },
  { value: "points", label: "积分激励" },
  { value: "private_domain_repurchase", label: "加企微复购" },
  { value: "festival", label: "节日促销" },
  { value: "custom", label: "自定义活动" },
];

const BENEFIT_VALIDITY_OPTIONS = [
  { value: "campaign_period", label: "随活动期有效" },
  { value: "after_claim_days", label: "领取后 N 天有效" },
  { value: "fixed_range", label: "指定日期范围" },
];

const PARTICIPATION_OPTIONS = [
  { value: "first_scan", label: "首次扫码可参与" },
  { value: "any_scan", label: "扫码即可参与" },
  { value: "member_only", label: "仅会员可参与" },
  { value: "wecom_required", label: "添加企微后参与" },
];

const PARTICIPATION_LABELS: Record<NonNullable<CampaignFormValues["participation_condition_type"]>, string> = {
  first_scan: "消费者首次扫码后即可参与。",
  any_scan: "消费者扫码后即可参与。",
  member_only: "消费者完成会员识别后即可参与。",
  wecom_required: "消费者按页面指引添加企业微信后即可参与。",
};

const GOAL_PRESETS: Record<string, {
  campaign_type: string;
  name: string;
  description: string;
  participation_condition_type: NonNullable<CampaignFormValues["participation_condition_type"]>;
  claim_limit_count: number;
  participation_conditions: string;
  claim_limits: string;
  validity_period: string;
  disclaimer: string;
  benefit_enabled: boolean;
  benefit_name: string;
  benefit_description: string;
}> = {
  first_scan_coupon: {
    campaign_type: "coupon",
    name: "首扫领券复购活动",
    description: "消费者首次扫码后领取优惠券，引导加购和复购。",
    participation_condition_type: "first_scan",
    claim_limit_count: 1,
    participation_conditions: "消费者首次扫码后即可参与。",
    claim_limits: "每人限领1次",
    validity_period: "领取后7天有效",
    disclaimer: "活动权益数量有限，先到先得，最终解释权归品牌方所有。",
    benefit_enabled: true,
    benefit_name: "首扫专属优惠券",
    benefit_description: "扫码后可领取的复购优惠券",
  },
  lottery: {
    campaign_type: "lottery",
    name: "扫码抽奖活动",
    description: "消费者扫码后参与抽奖，提升互动和分享意愿。",
    participation_condition_type: "any_scan",
    claim_limit_count: 1,
    participation_conditions: "消费者扫码后即可参与抽奖。",
    claim_limits: "每人限参与1次",
    validity_period: "活动期内有效",
    disclaimer: "中奖结果以系统记录为准，最终解释权归品牌方所有。",
    benefit_enabled: false,
    benefit_name: "",
    benefit_description: "",
  },
  points: {
    campaign_type: "points",
    name: "扫码积分激励活动",
    description: "消费者扫码后获得积分，沉淀会员资产。",
    participation_condition_type: "member_only",
    claim_limit_count: 1,
    participation_conditions: "消费者扫码并完成会员识别后可参与。",
    claim_limits: "每人每天限参与1次",
    validity_period: "积分长期有效，以会员规则为准",
    disclaimer: "积分发放以系统记录为准。",
    benefit_enabled: false,
    benefit_name: "",
    benefit_description: "",
  },
  private_domain_repurchase: {
    campaign_type: "coupon",
    name: "加企微复购活动",
    description: "消费者扫码领取权益，并引导添加企业微信完成复购转化。",
    participation_condition_type: "wecom_required",
    claim_limit_count: 1,
    participation_conditions: "消费者扫码并按页面指引添加企业微信后可参与。",
    claim_limits: "每人限领1次",
    validity_period: "领取后7天有效",
    disclaimer: "请通过官方客服领取权益，最终解释权归品牌方所有。",
    benefit_enabled: true,
    benefit_name: "企微复购优惠券",
    benefit_description: "添加企业微信后使用的复购优惠券",
  },
  festival: {
    campaign_type: "coupon",
    name: "节日扫码促销活动",
    description: "节日期间扫码领取福利，提升活动期转化。",
    participation_condition_type: "any_scan",
    claim_limit_count: 1,
    participation_conditions: "活动期间消费者扫码即可参与。",
    claim_limits: "每人限领1次",
    validity_period: "活动期内有效",
    disclaimer: "活动权益数量有限，最终解释权归品牌方所有。",
    benefit_enabled: true,
    benefit_name: "节日专属优惠券",
    benefit_description: "节日活动期使用的专属优惠券",
  },
  custom: {
    campaign_type: "coupon",
    name: "",
    description: "",
    participation_condition_type: "any_scan",
    claim_limit_count: 1,
    participation_conditions: "消费者扫码后即可参与。",
    claim_limits: "每人限参与1次",
    validity_period: "活动期内有效",
    disclaimer: "最终解释权归品牌方所有。",
    benefit_enabled: false,
    benefit_name: "",
    benefit_description: "",
  },
};

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

function participationTypeFromText(value?: string) {
  if (value?.includes("首次")) return "first_scan";
  if (value?.includes("会员")) return "member_only";
  if (value?.includes("企微") || value?.includes("企业微信")) return "wecom_required";
  return "any_scan";
}

function claimLimitCountFromText(value?: string) {
  const matched = value?.match(/\d+/);
  return matched ? Number(matched[0]) : 1;
}

function formatParticipationCondition(value?: CampaignFormValues["participation_condition_type"]) {
  return PARTICIPATION_LABELS[value || "any_scan"];
}

function formatClaimLimit(values: Pick<CampaignFormValues, "campaign_goal" | "claim_limit_count">) {
  const count = values.claim_limit_count || 1;
  const action = values.campaign_goal === "lottery" || values.campaign_goal === "points" ? "参与" : "领取";
  return `每人限${action}${count}次`;
}

function getProductLabel(products: ProductOption[], productId?: string) {
  const product = products.find((item) => item.id === productId);
  if (!product) return "";
  return product.category ? `${product.name} · ${product.category}` : product.name;
}

function getProductName(products: ProductOption[], productId?: string) {
  return products.find((item) => item.id === productId)?.name || "";
}

function buildBenefitDefaults(values: Partial<CampaignFormValues>, products: ProductOption[]) {
  const goal = values.campaign_goal || "custom";
  const productName = getProductName(products, values.product_id);
  const namePrefix = productName || "";
  const descriptionSubject = productName || "本活动";
  const templates: Record<string, { name: string; description: string }> = {
    first_scan_coupon: {
      name: `${namePrefix}首扫专属优惠券`,
      description: `${descriptionSubject}扫码后可领取的复购优惠券。`,
    },
    lottery: {
      name: `${namePrefix}扫码抽奖奖品`,
      description: `${descriptionSubject}扫码抽奖活动奖品，中奖后按页面规则领取。`,
    },
    points: {
      name: `${namePrefix}扫码积分奖励`,
      description: `${descriptionSubject}扫码后可获得的积分奖励。`,
    },
    private_domain_repurchase: {
      name: `${namePrefix}企微复购优惠券`,
      description: `${descriptionSubject}添加企业微信后可使用的复购优惠券。`,
    },
    festival: {
      name: `${namePrefix}节日专属优惠券`,
      description: `${descriptionSubject}节日活动期可领取的专属优惠券。`,
    },
    custom: {
      name: `${namePrefix || "活动"}权益`,
      description: `${descriptionSubject}可发放的活动权益。`,
    },
  };
  return templates[goal] || templates.custom;
}

function buildBenefitValidityDefaults(validityPeriod?: string) {
  if (validityPeriod?.includes("领取后7天")) {
    return { benefit_validity_mode: "after_claim_days" as const, benefit_validity_days: 7 };
  }
  return { benefit_validity_mode: "campaign_period" as const, benefit_validity_days: undefined };
}

function formatBenefitValidity(values: CampaignFormValues) {
  if (values.benefit_validity_mode === "after_claim_days") {
    const days = values.benefit_validity_days || 1;
    return {
      label: `领取后${days}天有效`,
      config: { validity_type: "after_claim_days", validity_days: days },
    };
  }
  if (values.benefit_validity_mode === "fixed_range" && values.benefit_validity_range?.[0] && values.benefit_validity_range?.[1]) {
    const [start, end] = values.benefit_validity_range;
    return {
      label: `${start.format("YYYY-MM-DD HH:mm")} 至 ${end.format("YYYY-MM-DD HH:mm")}`,
      config: {
        validity_type: "fixed_range",
        validity_start_at: start.format("YYYY-MM-DDTHH:mm:ss"),
        validity_end_at: end.format("YYYY-MM-DDTHH:mm:ss"),
      },
    };
  }
  const [start, end] = values.active_range || [];
  return {
    label: start && end ? `${start.format("YYYY-MM-DD HH:mm")} 至 ${end.format("YYYY-MM-DD HH:mm")}` : "随活动期有效",
    config: { validity_type: "campaign_period" },
  };
}

function getActivationIssues(record: Campaign) {
  const blocking: string[] = [];
  const warnings: string[] = [];
  const startAt = dayjs(record.start_at);
  const endAt = dayjs(record.end_at);
  const rules = rulesFromCampaign(record);

  if (!record.product_id) blocking.push("未关联产品，消费者扫码时不会自动命中该活动。");
  if (getBenefitCount(record) <= 0) blocking.push("未配置权益，消费者参与后无法领取奖励。");
  if (!startAt.isValid() || !endAt.isValid() || !endAt.isAfter(startAt)) blocking.push("投放时间无效，请检查开始和结束时间。");
  if (getStockTotal(record) <= 0) blocking.push("权益库存为 0，请先配置可领取库存。");
  if (!record.description && !rules.disclaimer) warnings.push("消费者说明为空，用户可能不清楚活动规则。");
  if (!rules.customer_service_contact) warnings.push("客服方式为空，用户遇到领取问题时无法联系品牌方。");
  if (endAt.isValid() && endAt.diff(dayjs(), "day") <= 3) warnings.push("活动即将结束，请确认仍需上线。");

  return { blocking, warnings };
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
    const validityDefaults = buildBenefitValidityDefaults(GOAL_PRESETS.first_scan_coupon.validity_period);
    setEditItem(null);
    form.resetFields();
    form.setFieldsValue({
      campaign_goal: "first_scan_coupon",
      campaign_type: GOAL_PRESETS.first_scan_coupon.campaign_type,
      name: GOAL_PRESETS.first_scan_coupon.name,
      description: GOAL_PRESETS.first_scan_coupon.description,
      participation_condition_type: GOAL_PRESETS.first_scan_coupon.participation_condition_type,
      claim_limit_count: GOAL_PRESETS.first_scan_coupon.claim_limit_count,
      participation_conditions: GOAL_PRESETS.first_scan_coupon.participation_conditions,
      claim_limits: GOAL_PRESETS.first_scan_coupon.claim_limits,
      validity_period: GOAL_PRESETS.first_scan_coupon.validity_period,
      disclaimer: GOAL_PRESETS.first_scan_coupon.disclaimer,
      benefit_enabled: GOAL_PRESETS.first_scan_coupon.benefit_enabled,
      benefit_name: GOAL_PRESETS.first_scan_coupon.benefit_name,
      benefit_stock_total: 100,
      benefit_per_person_limit: 1,
      ...validityDefaults,
      benefit_description: GOAL_PRESETS.first_scan_coupon.benefit_description,
    });
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
      participation_condition_type: participationTypeFromText(rules.participation_conditions),
      claim_limit_count: claimLimitCountFromText(rules.claim_limits),
      participation_conditions: rules.participation_conditions || "",
      claim_limits: rules.claim_limits || "",
      validity_period: rules.validity_period || "",
      disclaimer: rules.disclaimer || "",
      minor_notice: rules.minor_notice || "",
      customer_service_contact: rules.customer_service_contact || "",
      benefit_enabled: false,
    });
    setModalOpen(true);
  };

  const applyGoalPreset = (goal: string, options?: { keepBenefitChoice?: boolean }) => {
    const preset = GOAL_PRESETS[goal];
    if (!preset) return;
    const values = form.getFieldsValue();
    const productLabel = getProductLabel(products, values.product_id);
    const nextName = preset.name && productLabel ? `${productLabel}${preset.name}` : preset.name;
    const nextValues: Partial<CampaignFormValues> = {
      ...values,
      campaign_goal: goal,
      name: nextName || values.name,
      validity_period: preset.validity_period,
    };
    const benefitDefaults = buildBenefitDefaults(nextValues, products);
    const validityDefaults = buildBenefitValidityDefaults(preset.validity_period);
    const benefitEnabled = options?.keepBenefitChoice ? values.benefit_enabled : preset.benefit_enabled;
    form.setFieldsValue({
      campaign_type: preset.campaign_type,
      name: nextName,
      description: preset.description,
      participation_condition_type: preset.participation_condition_type,
      claim_limit_count: preset.claim_limit_count,
      participation_conditions: preset.participation_conditions,
      claim_limits: preset.claim_limits,
      validity_period: preset.validity_period,
      disclaimer: preset.disclaimer,
      benefit_enabled: benefitEnabled,
      benefit_name: !form.isFieldTouched("benefit_name") || !values.benefit_name
        ? benefitDefaults.name
        : values.benefit_name,
      benefit_per_person_limit: values.benefit_per_person_limit || 1,
      benefit_stock_total: values.benefit_stock_total || (benefitEnabled ? 100 : undefined),
      benefit_validity_mode: form.isFieldTouched("benefit_validity_mode")
        ? values.benefit_validity_mode
        : validityDefaults.benefit_validity_mode,
      benefit_validity_days: form.isFieldTouched("benefit_validity_days")
        ? values.benefit_validity_days
        : validityDefaults.benefit_validity_days,
      benefit_description: !form.isFieldTouched("benefit_description") || !values.benefit_description
        ? benefitDefaults.description
        : values.benefit_description,
    });
  };

  const fillBenefitDefaults = (overrides?: Partial<CampaignFormValues>) => {
    const values = { ...form.getFieldsValue(), ...overrides };
    const defaults = buildBenefitDefaults(values, products);
    const next: Partial<CampaignFormValues> = {};
    if (!form.isFieldTouched("benefit_name") || !values.benefit_name) {
      next.benefit_name = defaults.name;
    }
    if (!form.isFieldTouched("benefit_description") || !values.benefit_description) {
      next.benefit_description = defaults.description;
    }
    if (!values.benefit_per_person_limit) {
      next.benefit_per_person_limit = 1;
    }
    if (!values.benefit_stock_total && values.benefit_enabled) {
      next.benefit_stock_total = 100;
    }
    if (!values.benefit_validity_mode) {
      next.benefit_validity_mode = "campaign_period";
    }
    form.setFieldsValue(next);
  };

  const buildPayload = (values: CampaignFormValues) => {
    const [startAt, endAt] = values.active_range;
    const preset = values.campaign_goal ? GOAL_PRESETS[values.campaign_goal] : undefined;
    const validityPeriod = values.benefit_enabled
      ? formatBenefitValidity(values).label
      : values.validity_period || preset?.validity_period || "活动期内有效";
    return {
      name: values.name,
      campaign_type: values.campaign_type || preset?.campaign_type || "coupon",
      product_id: values.product_id || null,
      start_at: startAt.format("YYYY-MM-DDTHH:mm:ss"),
      end_at: endAt.format("YYYY-MM-DDTHH:mm:ss"),
      description: values.description || null,
      rules_json: {
        campaign_goal: values.campaign_goal || "custom",
        participation_condition_type: values.participation_condition_type || "any_scan",
        participation_conditions: formatParticipationCondition(values.participation_condition_type),
        claim_limit_count: values.claim_limit_count || 1,
        claim_limits: formatClaimLimit(values),
        validity_period: validityPeriod,
        disclaimer: values.disclaimer || "最终解释权归品牌方所有",
        minor_notice: values.minor_notice || "未成年人请在监护人陪同下参与",
        customer_service_contact: values.customer_service_contact || "",
      },
    };
  };

  const buildBenefitPayload = (values: CampaignFormValues) => {
    const validity = formatBenefitValidity(values);
    return {
      name: values.benefit_name || "活动权益",
      benefit_type: "platform_coupon",
      stock_total: values.benefit_stock_total || 0,
      per_person_limit: values.benefit_per_person_limit || 1,
      config_json: {
        title: values.benefit_name || "活动权益",
        description: values.benefit_description || "",
        validity_period: validity.label,
        ...validity.config,
        campaign_goal: values.campaign_goal || "custom",
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
        const { data: createdCampaign } = await api.post<Campaign>("/campaigns", payload);
        let createdBenefit = false;
        if (values.benefit_enabled) {
          try {
            await api.post(`/campaigns/${createdCampaign.id}/benefits`, buildBenefitPayload(values));
            createdBenefit = true;
          } catch {
            message.warning("活动草稿已创建，但权益创建失败，请继续配置权益后再上线");
          }
        }
        message.success(createdBenefit ? "活动草稿和基础权益已创建" : "活动草稿已创建，请继续配置权益后上线");
        setDetailItem({
          ...createdCampaign,
          benefit_count: createdBenefit ? 1 : getBenefitCount(createdCampaign),
          stock_total: createdBenefit ? values.benefit_stock_total || 0 : getStockTotal(createdCampaign),
          stock_used: getStockUsed(createdCampaign),
          claim_count: getClaimCount(createdCampaign),
        });
        mutate();
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
    const issues = getActivationIssues(record);
    if (issues.blocking.length > 0) {
      modal.warning({
        title: "活动暂不能上线",
        okText: "知道了",
        content: (
          <div className="space-y-3">
            <Text type="secondary">请先处理以下阻断项，再上线活动。</Text>
            <ul className="m-0 pl-5">
              {issues.blocking.map((item) => <li key={item}>{item}</li>)}
            </ul>
            <Button icon={<GiftOutlined />} onClick={() => configureBenefits(record)}>继续配置权益</Button>
          </div>
        ),
      });
      return;
    }
    modal.confirm({
      title: "确认上线活动？",
      okText: "上线活动",
      cancelText: "取消",
      content: (
        <div className="space-y-3">
          <Alert
            type={issues.warnings.length > 0 ? "warning" : "info"}
            showIcon
            title={issues.warnings.length > 0 ? "检查通过，但仍有需要确认的提醒。" : "检查通过，上线后消费者可在活动期内参与。"}
          />
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="活动">{record.name}</Descriptions.Item>
            <Descriptions.Item label="关联产品">{record.product_name || "未关联产品"}</Descriptions.Item>
            <Descriptions.Item label="活动时间">{formatCampaignTime(record).range}</Descriptions.Item>
            <Descriptions.Item label="权益配置">{getBenefitCount(record) > 0 ? `${getBenefitCount(record)} 个权益` : "未配置权益"}</Descriptions.Item>
          </Descriptions>
          {issues.warnings.length > 0 && (
            <ul className="m-0 pl-5">
              {issues.warnings.map((item) => <li key={item}>{item}</li>)}
            </ul>
          )}
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
        okText={editItem ? "保存活动" : "创建草稿并配置权益"}
        cancelText="取消"
        width={720}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          onValuesChange={(changed) => {
            if (!editItem && changed.campaign_goal) applyGoalPreset(changed.campaign_goal);
            if (!editItem && changed.product_id) {
              const goal = form.getFieldValue("campaign_goal");
              if (goal) applyGoalPreset(goal, { keepBenefitChoice: true });
            }
            if (!editItem && changed.name && form.getFieldValue("benefit_enabled")) {
              fillBenefitDefaults({ name: changed.name });
            }
            if (!editItem && changed.benefit_enabled) {
              fillBenefitDefaults();
            }
          }}
        >
          {!editItem && (
            <div className="mb-4 rounded border border-solid border-[var(--ant-color-border)] p-4">
              <Text strong>活动目标</Text>
              <Text type="secondary" className="mb-3 mt-1 block">先选择业务目标，系统会自动生成活动规则和权益建议。</Text>
              <Form.Item name="campaign_goal" label="活动目标" rules={[{ required: true, message: "请选择活动目标" }]}>
                <Select options={CAMPAIGN_GOAL_OPTIONS} data-testid="campaign-goal-select" />
              </Form.Item>
            </div>
          )}

          <div className="mb-4 rounded border border-solid border-[var(--ant-color-border)] p-4">
            <Text strong>基础信息</Text>
            <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
              <Form.Item name="name" label="活动名称" rules={[{ required: true, message: "请输入活动名称" }]}>
                <Input data-testid="campaign-name-input" />
              </Form.Item>
              <Form.Item name="campaign_type" label={editItem ? "活动类型" : "系统活动类型"} rules={[{ required: true, message: "请选择活动类型" }]}>
                <Select options={TYPE_OPTIONS} disabled={!editItem} data-testid="campaign-type-select" />
              </Form.Item>
            </div>
            <Form.Item
              name="product_id"
              label="关联产品"
              extra="活动会通过关联产品进入扫码页和运营数据统计。"
              rules={!editItem ? [{ required: true, message: "请选择关联产品" }] : undefined}
            >
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
              label="投放时间"
              rules={[
                { required: true, message: "请选择投放时间" },
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
          </div>

          {!editItem && (
            <div className="mb-4 rounded border border-solid border-[var(--ant-color-border)] p-4">
              <Text strong>权益与规则</Text>
              <Text type="secondary" className="mb-3 mt-1 block">先配置活动可发放的权益，创建后可在权益管理中继续调整库存和领取规则。</Text>
              <Form.Item name="benefit_enabled" valuePropName="checked">
                <Checkbox>创建基础平台券权益</Checkbox>
              </Form.Item>
              <Form.Item noStyle shouldUpdate={(prev, cur) => prev.benefit_enabled !== cur.benefit_enabled}>
                {({ getFieldValue }) => getFieldValue("benefit_enabled") ? (
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                    <Form.Item
                      name="benefit_name"
                      label="权益名称"
                      extra="已根据活动目标和关联产品生成，可直接修改。"
                      rules={[{ required: true, message: "请输入权益名称" }]}
                    >
                      <Input placeholder="例如：首扫专属优惠券" />
                    </Form.Item>
                    <Form.Item name="benefit_stock_total" label="总库存" rules={[{ required: true, message: "请输入总库存" }]}>
                      <InputNumber min={1} className="w-full" />
                    </Form.Item>
                    <Form.Item name="benefit_per_person_limit" label="每人限领" rules={[{ required: true, message: "请输入每人限领次数" }]}>
                      <InputNumber min={1} className="w-full" />
                    </Form.Item>
                    <Form.Item
                      name="benefit_validity_mode"
                      label="权益有效期"
                      className="md:col-span-2"
                      rules={[{ required: true, message: "请选择权益有效期" }]}
                    >
                      <Segmented block options={BENEFIT_VALIDITY_OPTIONS} />
                    </Form.Item>
                    <Form.Item
                      noStyle
                      shouldUpdate={(prev, cur) => prev.benefit_validity_mode !== cur.benefit_validity_mode}
                    >
                      {({ getFieldValue }) => {
                        const mode = getFieldValue("benefit_validity_mode");
                        if (mode === "after_claim_days") {
                          return (
                            <Form.Item
                              name="benefit_validity_days"
                              label="领取后有效天数"
                              rules={[{ required: true, message: "请输入有效天数" }]}
                            >
                              <InputNumber min={1} precision={0} addonAfter="天" className="w-full" />
                            </Form.Item>
                          );
                        }
                        if (mode === "fixed_range") {
                          return (
                            <Form.Item
                              name="benefit_validity_range"
                              label="权益有效日期"
                              className="md:col-span-2"
                              rules={[
                                { required: true, message: "请选择权益有效日期" },
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
                          );
                        }
                        return (
                          <Alert
                            type="info"
                            showIcon
                            title="权益有效期将与活动投放时间保持一致。"
                            className="md:col-span-2"
                          />
                        );
                      }}
                    </Form.Item>
                    <Form.Item
                      name="benefit_description"
                      label="券面说明"
                      className="md:col-span-2"
                      extra="会展示给运营用于核对权益内容，可按实际优惠规则调整。"
                    >
                      <TextArea rows={2} placeholder="例如：满 99 元可用，活动期内有效" />
                    </Form.Item>
                  </div>
                ) : (
                  <Alert type="warning" showIcon title="暂不配置权益时，活动创建后不能直接上线，需要先去权益管理补齐。" />
                )}
              </Form.Item>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <Form.Item
                  name="participation_condition_type"
                  label="谁可以参与"
                  rules={[{ required: true, message: "请选择参与条件" }]}
                >
                  <Select options={PARTICIPATION_OPTIONS} data-testid="campaign-conditions-select" />
                </Form.Item>
                <Form.Item
                  name="claim_limit_count"
                  label="每人最多参与/领取"
                  rules={[{ required: true, message: "请输入次数限制" }]}
                >
                  <InputNumber min={1} precision={0} addonAfter="次" className="w-full" data-testid="campaign-claim-limits-input" />
                </Form.Item>
              </div>
            </div>
          )}

          {editItem && (
            <div className="mb-4 rounded border border-solid border-[var(--ant-color-border)] p-4">
              <Text strong>活动规则</Text>
              <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
                <Form.Item
                  name="participation_condition_type"
                  label="谁可以参与"
                  rules={[{ required: true, message: "请选择参与条件" }]}
                >
                  <Select options={PARTICIPATION_OPTIONS} data-testid="campaign-conditions-select" />
                </Form.Item>
                <Form.Item
                  name="claim_limit_count"
                  label="每人最多参与/领取"
                  rules={[{ required: true, message: "请输入次数限制" }]}
                >
                  <InputNumber min={1} precision={0} addonAfter="次" className="w-full" data-testid="campaign-claim-limits-input" />
                </Form.Item>
              </div>
              <Alert type="info" showIcon title="完整权益维护请进入权益管理，避免编辑活动基础信息时误改已投放权益。" />
            </div>
          )}

          <div className="rounded border border-solid border-[var(--ant-color-border)] p-4">
            <Text strong>消费者说明</Text>
            <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
              <Form.Item name="description" label="活动说明" className="md:col-span-2">
                <TextArea rows={2} data-testid="campaign-description-input" />
              </Form.Item>
              <Form.Item name="customer_service_contact" label="客服方式">
                <Input placeholder="400-123-4567 或企业微信客服" data-testid="campaign-contact-input" />
              </Form.Item>
            </div>
            <Form.Item name="disclaimer" label="消费者说明">
              <TextArea rows={2} data-testid="campaign-disclaimer-input" />
            </Form.Item>
          </div>
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
              title="这里展示活动权益配置和领取记录，帮助判断活动是否可以投放。"
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
