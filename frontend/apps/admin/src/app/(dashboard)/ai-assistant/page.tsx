"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Alert,
  App,
  Button,
  Card,
  Collapse,
  Descriptions,
  Divider,
  Empty,
  Form,
  Input,
  Modal,
  Radio,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  CopyOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  GiftOutlined,
  ProductOutlined,
  RocketOutlined,
  SendOutlined,
} from "@ant-design/icons";
import ImageUploadInput from "@/components/ImageUploadInput";
import api from "@/lib/api";
import {
  extractFromText,
  generateCampaign,
  generateCopywriting,
  generatePageCopy,
  getAIErrorMessage,
  recognizeImage,
  type CampaignGoal,
  type CampaignResult,
  type CopywritingItem,
  type CopywritingResult,
  type CopywritingType,
  type ExtractResult,
  type PageCopyResult,
} from "@/lib/ai";
import {
  createDefaultModules,
  createEmptyDSL,
  type ModuleConfig,
} from "@/lib/page-dsl";
import type { Product } from "../products/_components/types";
import { FIELD_LABELS } from "./_components/constants";

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

type TaskKey = "extract" | "copywriting" | "page" | "campaign";
type ExtractMode = "text" | "image";

type AIResult =
  | { kind: "extract"; data: ExtractResult }
  | { kind: "copywriting"; data: CopywritingResult; copyType: CopywritingType }
  | { kind: "page"; data: PageCopyResult }
  | { kind: "campaign"; data: CampaignResult; goal: CampaignGoal };

interface ProductListResponse {
  items?: Product[];
}

interface ProductApplyValues {
  name?: string;
  category?: string;
  origin?: string;
  description?: string;
  story_title?: string;
  story_content?: string;
}

interface CampaignDraftValues {
  name: string;
  campaign_type: string;
  start_at: string;
  end_at: string;
  description?: string;
  participation_conditions?: string;
  claim_limits?: string;
  validity_period?: string;
  disclaimer?: string;
}

const TASKS: Array<{
  key: TaskKey;
  title: string;
  description: string;
  emptyTitle: string;
  emptyDescription: string;
  icon: React.ReactNode;
}> = [
  {
    key: "extract",
    title: "识别包装/资料",
    description: "识别包装、报告或描述文本，生成可确认的产品字段建议。",
    emptyTitle: "生成后可应用到产品资料",
    emptyDescription:
      "AI 会提取产品名称、品类、产地、规格和保质期等字段，确认后写入当前产品。",
    icon: <ExperimentOutlined />,
  },
  {
    key: "copywriting",
    title: "补全产品介绍",
    description: "围绕选中产品生成产品介绍、卖点和品牌/产品故事。",
    emptyTitle: "生成后可写入介绍或故事",
    emptyDescription:
      "适合补齐产品介绍、产品卖点、故事标题和品牌/产品故事，不会自动覆盖资料。",
    icon: <CopyOutlined />,
  },
  {
    key: "page",
    title: "生成扫码页草稿",
    description: "基于产品资料生成 H5 页面文案和模块建议，并进入编辑器。",
    emptyTitle: "生成后可创建扫码页草稿",
    emptyDescription:
      "AI 会给出页面文案和模块建议，创建后进入页面编辑器做发布检查。",
    icon: <FileTextOutlined />,
  },
  {
    key: "campaign",
    title: "策划扫码活动",
    description: "生成活动玩法、规则和利益点，确认后创建活动草稿。",
    emptyTitle: "生成后可创建活动草稿",
    emptyDescription:
      "AI 会生成活动玩法、规则和利益点，确认时间与类型后创建为草稿。",
    icon: <GiftOutlined />,
  },
];

const CATEGORY_OPTIONS = [
  "大米",
  "面粉",
  "食用油",
  "茶叶",
  "水果",
  "蔬菜",
  "肉类",
  "乳制品",
  "酒类",
  "饮料",
  "零食",
  "保健品",
  "其他",
].map((value) => ({ value, label: value }));

const CAMPAIGN_TYPE_OPTIONS = [
  { value: "coupon", label: "优惠券" },
  { value: "lottery", label: "抽奖" },
  { value: "points", label: "积分" },
];

const goalToCampaignType: Record<CampaignGoal, string> = {
  promotion: "coupon",
  retention: "points",
  brand_awareness: "lottery",
  festival: "coupon",
};

function splitKeywords(value?: string) {
  return value ? value.split(/[,，、\s]+/).filter(Boolean) : [];
}

function formatLocalDateTime(date: Date) {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:00`;
}

function buildProductHints(product?: Product | null) {
  if (!product) return "";
  return [
    product.category,
    product.origin,
    product.description,
    product.story_title,
  ]
    .filter(Boolean)
    .join(", ");
}

function getProductGaps(product?: Product | null) {
  if (!product) return ["未选择产品"];
  const gaps: string[] = [];
  if (!product.origin) gaps.push("未填写产地");
  if (!product.category) gaps.push("未填写品类");
  if (!product.description) gaps.push("缺产品介绍");
  if (!product.story_content) gaps.push("缺产品故事");
  return gaps;
}

function getRecommendedTask(product?: Product | null): {
  task: TaskKey;
  reason: string;
} {
  if (!product) {
    return {
      task: "extract",
      reason: "先选择产品，AI 结果才能应用回业务资料。",
    };
  }
  if (!product.origin || !product.category) {
    return {
      task: "extract",
      reason: "当前产品基础字段不完整，建议先识别包装或资料补齐。",
    };
  }
  if (!product.description || !product.story_content) {
    return {
      task: "copywriting",
      reason: "当前产品介绍或故事不完整，建议先生成可应用文案。",
    };
  }
  return {
    task: "page",
    reason: "产品基础资料较完整，建议生成扫码页草稿进入发布闭环。",
  };
}

function renderCopyContent(content: CopywritingResult["content"]) {
  if (typeof content === "string") {
    return (
      <Paragraph
        className="!mb-0"
        style={{ whiteSpace: "pre-wrap", lineHeight: 1.8 }}
      >
        {content}
      </Paragraph>
    );
  }
  const items = content.items.map(normalizeCopywritingItem);
  return (
    <ul className="m-0 list-inside list-disc space-y-2 pl-1">
      {items.map((item, index) => (
        <li key={index}>
          {item.title ? <Text strong>{item.title}</Text> : null}
          {item.title && item.detail ? <span>：</span> : null}
          {item.detail}
        </li>
      ))}
    </ul>
  );
}

function normalizeCopywritingItem(item: CopywritingItem): {
  title?: string;
  detail: string;
} {
  if (typeof item === "string") return { detail: item };
  const title = typeof item.title === "string" ? item.title : undefined;
  const detail =
    typeof item.detail === "string"
      ? item.detail
      : Object.entries(item)
          .filter(
            ([key, value]) => key !== "title" && value != null && value !== ""
          )
          .map(([key, value]) => `${key}: ${String(value)}`)
          .join("；");
  return { title, detail: detail || title || "" };
}

function contentToText(content: CopywritingResult["content"]) {
  if (typeof content === "string") return content;
  return content.items
    .map(normalizeCopywritingItem)
    .map((item) => [item.title, item.detail].filter(Boolean).join("："))
    .join("\n");
}

function buildExtractDescription(fields: ExtractResult["fields"]) {
  const parts = [
    fields.source ? `识别说明：${fields.source}` : "",
    fields.weight ? `规格/净含量：${fields.weight}` : "",
    fields.shelf_life ? `保质期：${fields.shelf_life}` : "",
  ].filter(Boolean);
  return parts.join("\n");
}

function buildPageDSL(pageResult: PageCopyResult) {
  const dsl = createEmptyDSL();
  const defaultModules = createDefaultModules();
  const suggestedTypes = new Set(
    pageResult.result.page_suggestion.modules.map((mod) => mod.type)
  );
  const modules = defaultModules.map<ModuleConfig>((module) => ({
    ...module,
    enabled: module.enabled || suggestedTypes.has(module.type),
    config: {
      ...(module.config || {}),
      ...(module.type === "product_hero"
        ? { subtitle_template: pageResult.result.copywriting.hero_subtitle }
        : {}),
      ...(module.type === "media_section"
        ? { story_content: pageResult.result.copywriting.brand_story }
        : {}),
    },
  }));
  dsl.modules = modules;
  return dsl;
}

function getCampaignField(campaign: CampaignResult["campaign"], key: string) {
  const value = campaign[key];
  if (Array.isArray(value)) return value.join("；");
  if (value == null) return "";
  return String(value);
}

export default function AIAssistantPage() {
  const router = useRouter();
  const { message, modal } = App.useApp();
  const [activeTask, setActiveTask] = useState<TaskKey>("extract");
  const [products, setProducts] = useState<Product[]>([]);
  const [selectedProductId, setSelectedProductId] = useState<string>();
  const [extractMode, setExtractMode] = useState<ExtractMode>("text");
  const [result, setResult] = useState<AIResult | null>(null);
  const [loadingTask, setLoadingTask] = useState<TaskKey | null>(null);
  const [applyingProduct, setApplyingProduct] = useState(false);
  const [creatingPage, setCreatingPage] = useState(false);
  const [creatingCampaign, setCreatingCampaign] = useState(false);
  const [productApplyOpen, setProductApplyOpen] = useState(false);
  const [campaignDraftOpen, setCampaignDraftOpen] = useState(false);
  const [productApplyForm] = Form.useForm<ProductApplyValues>();
  const [campaignDraftForm] = Form.useForm<CampaignDraftValues>();

  const selectedProduct = useMemo(
    () => products.find((product) => product.id === selectedProductId),
    [products, selectedProductId]
  );

  const productOptions = useMemo(
    () =>
      products.map((product) => ({
        value: product.id,
        label: `${product.name}${product.category ? ` · ${product.category}` : ""}`,
      })),
    [products]
  );

  const productGaps = useMemo(
    () => getProductGaps(selectedProduct),
    [selectedProduct]
  );
  const recommended = useMemo(
    () => getRecommendedTask(selectedProduct),
    [selectedProduct]
  );
  const productHints = useMemo(
    () => buildProductHints(selectedProduct),
    [selectedProduct]
  );
  const currentTask = TASKS.find((task) => task.key === activeTask) || TASKS[0];
  const targetContext = selectedProductId
    ? { target_type: "product", target_id: selectedProductId }
    : undefined;

  const handleTaskSelect = (task: TaskKey) => {
    setActiveTask(task);
    setResult(null);
  };

  const fetchProducts = useCallback(async () => {
    try {
      const { data } = await api.get<ProductListResponse>("/products", {
        params: { page_size: 100 },
      });
      setProducts(data.items || []);
    } catch {
      setProducts([]);
    }
  }, []);

  useEffect(() => {
    void fetchProducts();
  }, [fetchProducts]);

  const requireProduct = (actionLabel: string) => {
    if (selectedProduct) return true;
    message.warning(`${actionLabel}前请先选择产品`);
    return false;
  };

  const getGenerationProductName = (inputName?: string) => {
    return selectedProduct?.name || inputName?.trim() || "";
  };

  const copyResultText = async () => {
    if (!result) return;
    const text =
      result.kind === "extract"
        ? JSON.stringify(result.data.fields, null, 2)
        : result.kind === "copywriting"
          ? contentToText(result.data.content)
          : result.kind === "page"
            ? JSON.stringify(result.data.result, null, 2)
            : JSON.stringify(result.data.campaign, null, 2);
    try {
      await navigator.clipboard.writeText(text);
      message.success("结果已复制");
    } catch {
      message.warning("当前浏览器不允许自动复制，请手动选择结果内容复制");
    }
  };

  const handleExtract = async (values: {
    text?: string;
    image_url?: string;
    filename?: string;
  }) => {
    setLoadingTask("extract");
    setResult(null);
    try {
      const data =
        extractMode === "text"
          ? await extractFromText(values.text || "")
          : await recognizeImage(
              values.image_url || "",
              values.filename || "image.jpg"
            );
      setResult({ kind: "extract", data });
      message.success("已生成产品资料建议");
    } catch (err) {
      message.error(getAIErrorMessage(err));
    } finally {
      setLoadingTask(null);
    }
  };

  const handleCopywriting = async (values: {
    type: CopywritingType;
    product_name?: string;
    keywords?: string;
  }) => {
    const productName = getGenerationProductName(values.product_name);
    if (!productName) {
      message.warning("请先选择产品，或手动填写产品名称");
      return;
    }
    setLoadingTask("copywriting");
    setResult(null);
    try {
      const data = await generateCopywriting(
        values.type,
        productName,
        splitKeywords(values.keywords),
        targetContext
      );
      setResult({ kind: "copywriting", data, copyType: values.type });
      message.success("产品文案已生成");
    } catch (err) {
      message.error(getAIErrorMessage(err));
    } finally {
      setLoadingTask(null);
    }
  };

  const handlePagePlan = async (values: {
    product_name?: string;
    category?: string;
    keywords?: string;
  }) => {
    const productName = getGenerationProductName(values.product_name);
    if (!productName) {
      message.warning("请先选择产品，或手动填写产品名称");
      return;
    }
    const category = selectedProduct?.category || values.category || "其他";
    setLoadingTask("page");
    setResult(null);
    try {
      const data = await generatePageCopy(
        productName,
        category,
        splitKeywords(values.keywords),
        targetContext
      );
      setResult({ kind: "page", data });
      message.success("扫码页草稿建议已生成");
    } catch (err) {
      message.error(getAIErrorMessage(err));
    } finally {
      setLoadingTask(null);
    }
  };

  const handleCampaignPlan = async (values: {
    product_name?: string;
    goal: CampaignGoal;
    target_audience: string;
  }) => {
    const productName = getGenerationProductName(values.product_name);
    if (!productName) {
      message.warning("请先选择产品，或手动填写产品名称");
      return;
    }
    setLoadingTask("campaign");
    setResult(null);
    try {
      const data = await generateCampaign(
        productName,
        values.goal,
        values.target_audience,
        targetContext
      );
      setResult({ kind: "campaign", data, goal: values.goal });
      message.success("活动方案已生成");
    } catch (err) {
      message.error(getAIErrorMessage(err));
    } finally {
      setLoadingTask(null);
    }
  };

  const openProductApply = () => {
    if (!requireProduct("应用 AI 建议")) return;
    const baseValues: ProductApplyValues = {
      name: selectedProduct?.name,
      category: selectedProduct?.category,
      origin: selectedProduct?.origin,
      description: selectedProduct?.description,
      story_title: selectedProduct?.story_title,
      story_content: selectedProduct?.story_content,
    };
    if (result?.kind === "extract") {
      const fields = result.data.fields;
      productApplyForm.setFieldsValue({
        ...baseValues,
        name: String(fields.product_name || baseValues.name || ""),
        category: String(fields.category || baseValues.category || ""),
        origin: String(fields.origin || baseValues.origin || ""),
        description: buildExtractDescription(fields) || baseValues.description,
      });
    }
    if (result?.kind === "copywriting") {
      const text = contentToText(result.data.content);
      productApplyForm.setFieldsValue({
        ...baseValues,
        ...(result.copyType === "brand_story"
          ? {
              story_title:
                baseValues.story_title ||
                `${selectedProduct?.name || "产品"}的安心故事`,
              story_content: text,
            }
          : { description: text }),
      });
    }
    setProductApplyOpen(true);
  };

  const confirmProductNameChange = (nextName: string) =>
    new Promise<boolean>((resolve) => {
      if (!selectedProduct || nextName === selectedProduct.name) {
        resolve(true);
        return;
      }
      modal.confirm({
        title: "确认修改产品名称？",
        okText: "确认修改",
        cancelText: "返回检查",
        content: `产品名称将从「${selectedProduct.name}」改为「${nextName}」。`,
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      });
    });

  const handleProductApply = async () => {
    if (!selectedProduct) return;
    const values = await productApplyForm.validateFields();
    if (values.name && !(await confirmProductNameChange(values.name))) return;
    setApplyingProduct(true);
    try {
      await api.patch(`/products/${selectedProduct.id}`, values);
      message.success("产品资料已更新");
      setProductApplyOpen(false);
      await fetchProducts();
    } catch {
      message.error("更新产品资料失败");
    } finally {
      setApplyingProduct(false);
    }
  };

  const createPageDraft = async () => {
    if (!requireProduct("创建扫码页草稿")) return;
    if (!selectedProduct || result?.kind !== "page") return;
    setCreatingPage(true);
    try {
      const name = `${selectedProduct.name}扫码信任页`;
      const { data: template } = await api.post("/page-templates", {
        name,
        template_type: "traceability",
        product_id: selectedProduct.id,
        description: "由 AI 助手生成的扫码页草稿，需在编辑器中确认后发布。",
      });
      await api.post(`/page-templates/${template.id}/versions`, {
        config_json: buildPageDSL(result.data),
      });
      message.success("扫码页草稿已创建，继续编辑");
      router.push(`/pages/${template.id}/edit`);
    } catch {
      message.error("创建扫码页草稿失败");
    } finally {
      setCreatingPage(false);
    }
  };

  const openCampaignDraft = () => {
    if (!requireProduct("创建活动")) return;
    if (result?.kind !== "campaign") return;
    const now = new Date();
    const end = new Date(now);
    end.setDate(now.getDate() + 30);
    const campaign = result.data.campaign;
    campaignDraftForm.setFieldsValue({
      name: campaign.name || `${selectedProduct?.name || "产品"}扫码福利活动`,
      campaign_type: goalToCampaignType[result.goal],
      start_at: formatLocalDateTime(now),
      end_at: formatLocalDateTime(end),
      description: getCampaignField(campaign, "description"),
      participation_conditions:
        getCampaignField(campaign, "rules") || "消费者扫码后参与",
      claim_limits: "每人限参与1次",
      validity_period: getCampaignField(campaign, "duration") || "活动期内有效",
      disclaimer: "最终解释权归品牌方所有",
    });
    setCampaignDraftOpen(true);
  };

  const handleCampaignDraftCreate = async () => {
    const values = await campaignDraftForm.validateFields();
    setCreatingCampaign(true);
    try {
      await api.post("/campaigns", {
        name: values.name,
        campaign_type: values.campaign_type,
        start_at: values.start_at,
        end_at: values.end_at,
        description: values.description,
        rules_json: {
          participation_conditions: values.participation_conditions || "",
          claim_limits: values.claim_limits || "每人限参与1次",
          validity_period: values.validity_period || "活动期内有效",
          disclaimer: values.disclaimer || "最终解释权归品牌方所有",
          product_id: selectedProductId,
          ai_generated: true,
        },
      });
      message.success("活动草稿已创建，可在活动管理继续维护");
      setCampaignDraftOpen(false);
    } catch {
      message.error("创建活动草稿失败");
    } finally {
      setCreatingCampaign(false);
    }
  };

  const renderTaskForm = () => {
    if (activeTask === "extract") {
      return (
        <Form
          key={`extract-${extractMode}`}
          layout="vertical"
          onFinish={handleExtract}
        >
          <Form.Item label="识别来源">
            <Radio.Group
              value={extractMode}
              onChange={(event) => {
                setExtractMode(event.target.value);
                setResult(null);
              }}
              optionType="button"
              buttonStyle="solid"
              options={[
                { value: "text", label: "包装/资料文本" },
                { value: "image", label: "图片识别" },
              ]}
            />
          </Form.Item>
          {extractMode === "text" ? (
            <Form.Item
              name="text"
              label="产品描述文本"
              rules={[{ required: true, message: "请输入产品描述文本" }]}
            >
              <TextArea
                rows={7}
                placeholder="粘贴包装、配料表、检测报告摘要或产品介绍，AI 会提取可补齐的产品字段。"
              />
            </Form.Item>
          ) : (
            <>
              <Form.Item
                name="image_url"
                label="产品图片"
                rules={[
                  { required: true, message: "请上传或填写图片地址" },
                  {
                    type: "url",
                    message: "请输入以 http:// 或 https:// 开头的图片链接",
                  },
                ]}
              >
                <ImageUploadInput
                  module="ai-recognition"
                  previewAlt="待识别图片预览"
                  variant="uploadFirst"
                />
              </Form.Item>
              <Form.Item name="filename" label="文件名">
                <Input placeholder="image.jpg" />
              </Form.Item>
            </>
          )}
          <Button
            type="primary"
            htmlType="submit"
            icon={<ExperimentOutlined />}
            loading={loadingTask === "extract"}
          >
            识别并生成资料建议
          </Button>
        </Form>
      );
    }

    if (activeTask === "copywriting") {
      return (
        <Form
          key={`copywriting-${selectedProductId || "manual"}`}
          layout="vertical"
          onFinish={handleCopywriting}
          initialValues={{
            type: "brand_story",
            product_name: selectedProduct?.name,
            keywords: productHints,
          }}
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Form.Item
              name="type"
              label="文案用途"
              rules={[{ required: true, message: "请选择文案用途" }]}
            >
              <Select
                options={[
                  { value: "brand_story", label: "品牌/产品故事" },
                  { value: "selling_points", label: "产品卖点/介绍" },
                ]}
              />
            </Form.Item>
            {!selectedProduct && (
              <Form.Item
                name="product_name"
                label="产品名称"
                rules={[{ required: true, message: "请输入产品名称" }]}
              >
                <Input placeholder="例：五常稻花香大米 5kg" />
              </Form.Item>
            )}
          </div>
          <Form.Item name="keywords" label="关键词">
            <Input placeholder="例：核心产区、检测合格、适合家庭复购" />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            icon={<CopyOutlined />}
            loading={loadingTask === "copywriting"}
          >
            生成可应用文案
          </Button>
        </Form>
      );
    }

    if (activeTask === "page") {
      return (
        <Form
          key={`page-${selectedProductId || "manual"}`}
          layout="vertical"
          onFinish={handlePagePlan}
          initialValues={{
            product_name: selectedProduct?.name,
            category: selectedProduct?.category || "其他",
            keywords: productHints,
          }}
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {!selectedProduct && (
              <Form.Item
                name="product_name"
                label="产品名称"
                rules={[{ required: true, message: "请输入产品名称" }]}
              >
                <Input placeholder="例：五常稻花香大米 5kg" />
              </Form.Item>
            )}
            {!selectedProduct && (
              <Form.Item
                name="category"
                label="品类"
                rules={[{ required: true, message: "请选择品类" }]}
              >
                <Select showSearch allowClear options={CATEGORY_OPTIONS} />
              </Form.Item>
            )}
          </div>
          <Form.Item name="keywords" label="页面重点">
            <Input placeholder="例：产地溯源、检测报告、首扫福利、私域承接" />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            icon={<FileTextOutlined />}
            loading={loadingTask === "page"}
          >
            生成扫码页草稿建议
          </Button>
        </Form>
      );
    }

    return (
      <Form
        key={`campaign-${selectedProductId || "manual"}`}
        layout="vertical"
        onFinish={handleCampaignPlan}
        initialValues={{
          goal: "promotion",
          product_name: selectedProduct?.name,
          target_audience: selectedProduct?.category
            ? `${selectedProduct.category}目标消费者`
            : "关注产品品质和溯源可信的消费者",
        }}
      >
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {!selectedProduct && (
            <Form.Item
              name="product_name"
              label="产品名称"
              rules={[{ required: true, message: "请输入产品名称" }]}
            >
              <Input placeholder="例：五常稻花香大米 5kg" />
            </Form.Item>
          )}
          <Form.Item
            name="goal"
            label="活动目标"
            rules={[{ required: true, message: "请选择活动目标" }]}
          >
            <Select
              options={[
                { value: "promotion", label: "拉新推广" },
                { value: "retention", label: "复购留存" },
                { value: "brand_awareness", label: "品牌认知" },
                { value: "festival", label: "节日活动" },
              ]}
            />
          </Form.Item>
        </div>
        <Form.Item
          name="target_audience"
          label="目标受众"
          rules={[{ required: true, message: "请描述目标受众" }]}
        >
          <Input placeholder="例：关注品质、产地和家庭健康消费的人群" />
        </Form.Item>
        <Button
          type="primary"
          htmlType="submit"
          icon={<GiftOutlined />}
          loading={loadingTask === "campaign"}
        >
          生成活动草稿方案
        </Button>
      </Form>
    );
  };

  const renderResult = () => {
    if (loadingTask) {
      return (
        <div className="flex min-h-90 items-center justify-center">
          <Space orientation="vertical" align="center">
            <RocketOutlined className="text-3xl text-blue-500" />
            <Text type="secondary">AI 正在生成建议...</Text>
          </Space>
        </div>
      );
    }
    if (!result) {
      return (
        <div className="flex min-h-75 items-center justify-center">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space orientation="vertical" size={4}>
                <Text strong>{currentTask.emptyTitle}</Text>
                <Text type="secondary">{currentTask.emptyDescription}</Text>
                {!selectedProduct && (
                  <Text type="secondary">
                    选择产品后，可把结果应用回产品、页面或活动。
                  </Text>
                )}
              </Space>
            }
          />
        </div>
      );
    }
    return (
      <Space orientation="vertical" size="middle" className="w-full">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Space>
            <CheckCircleOutlined
              style={{ color: "var(--ymt-color-feedback-success)" }}
            />
            <Text strong>AI 结果预览</Text>
          </Space>
          <Button size="small" icon={<CopyOutlined />} onClick={copyResultText}>
            复制结果
          </Button>
        </div>

        {result.kind === "extract" && (
          <>
            <Descriptions bordered size="small" column={1}>
              {Object.entries(result.data.fields).map(([key, value]) => {
                if (value == null || value === "") return null;
                const display =
                  key === "confidence" && typeof value === "number"
                    ? `${Math.round(value * 100)}%`
                    : String(value);
                return (
                  <Descriptions.Item key={key} label={FIELD_LABELS[key] || key}>
                    {display}
                  </Descriptions.Item>
                );
              })}
            </Descriptions>
            <Button
              type="primary"
              icon={<ProductOutlined />}
              disabled={!selectedProduct}
              loading={applyingProduct}
              onClick={openProductApply}
            >
              应用到产品资料
            </Button>
          </>
        )}

        {result.kind === "copywriting" && (
          <>
            <Card size="small">{renderCopyContent(result.data.content)}</Card>
            <Button
              type="primary"
              icon={<ProductOutlined />}
              disabled={!selectedProduct}
              loading={applyingProduct}
              onClick={openProductApply}
            >
              应用到产品资料
            </Button>
          </>
        )}

        {result.kind === "page" && (
          <>
            <Card size="small" title="页面文案">
              <Paragraph style={{ whiteSpace: "pre-wrap" }}>
                {result.data.result.copywriting.brand_story}
              </Paragraph>
              <Divider className="!my-3" />
              <Space wrap>
                {result.data.result.copywriting.selling_points.map((item) => (
                  <Tag key={item.title} color="blue">
                    {item.title}
                  </Tag>
                ))}
              </Space>
            </Card>
            <Card size="small" title="推荐模块">
              <Space wrap>
                {result.data.result.page_suggestion.modules.map((module) => (
                  <Tag
                    key={module.id}
                    color={module.enabled ? "green" : "default"}
                  >
                    {module.type}
                  </Tag>
                ))}
              </Space>
            </Card>
            <Button
              type="primary"
              icon={<SendOutlined />}
              disabled={!selectedProduct}
              loading={creatingPage}
              onClick={createPageDraft}
            >
              创建扫码页草稿
            </Button>
          </>
        )}

        {result.kind === "campaign" && (
          <>
            <Descriptions bordered size="small" column={1}>
              {Object.entries(result.data.campaign).map(([key, value]) => {
                if (value == null || value === "") return null;
                return (
                  <Descriptions.Item key={key} label={key}>
                    {Array.isArray(value) ? value.join("；") : String(value)}
                  </Descriptions.Item>
                );
              })}
            </Descriptions>
            <Button
              type="primary"
              icon={<GiftOutlined />}
              disabled={!selectedProduct}
              loading={creatingCampaign}
              onClick={openCampaignDraft}
            >
              创建活动草稿
            </Button>
          </>
        )}

        {"generation_id" in result.data && (
          <Tag color="blue">生成 ID: {result.data.generation_id}</Tag>
        )}
      </Space>
    );
  };

  return (
    <div className="space-y-4">
      <div>
        <Title level={4} className="!mb-1">
          AI 运营助手
        </Title>
        <Text type="secondary">
          先选择产品，再让 AI 帮你补资料、写文案、建扫码页草稿或策划活动。
        </Text>
      </div>

      <Card className="ai-context-card">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_2fr]">
          <div>
            <Text strong>产品上下文</Text>
            <div
              className="mt-1 text-sm"
              style={{ color: "var(--ymt-color-text-secondary)" }}
            >
              应用结果前必须选择产品；生成文本可先不选。
            </div>
          </div>
          <div>
            <Select
              className="w-full"
              showSearch
              allowClear
              placeholder="选择产品，让 AI 自动带入产品资料"
              value={selectedProductId}
              options={productOptions}
              optionFilterProp="label"
              onChange={(value) => {
                setSelectedProductId(value);
                setResult(null);
              }}
            />
            {selectedProduct ? (
              <>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Tag color="blue">{selectedProduct.name}</Tag>
                  <Tag color={selectedProduct.category ? "default" : "orange"}>
                    {selectedProduct.category || "未填写品类"}
                  </Tag>
                  <Tag color={selectedProduct.origin ? "default" : "orange"}>
                    {selectedProduct.origin || "未填写产地"}
                  </Tag>
                  {selectedProduct.description ? (
                    <Tag color="green">已有产品介绍</Tag>
                  ) : (
                    <Tag color="orange">缺产品介绍</Tag>
                  )}
                  {selectedProduct.story_content ? (
                    <Tag color="green">已有产品故事</Tag>
                  ) : (
                    <Tag color="orange">缺产品故事</Tag>
                  )}
                </div>
                <Alert
                  className="mt-3"
                  type={productGaps.length ? "warning" : "success"}
                  showIcon
                  title={`推荐下一步：${TASKS.find((task) => task.key === recommended.task)?.title}`}
                  description={recommended.reason}
                  action={
                    <Button
                      size="small"
                      type="primary"
                      onClick={() => handleTaskSelect(recommended.task)}
                    >
                      开始处理
                    </Button>
                  }
                />
              </>
            ) : (
              <Alert
                className="mt-3"
                type="warning"
                showIcon
                title="未选择产品时，AI 结果只能复制，不能写入资料、页面或活动。"
              />
            )}
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-4">
        {TASKS.map((task) => (
          <Card
            key={task.key}
            hoverable
            size="small"
            onClick={() => handleTaskSelect(task.key)}
            className={activeTask === task.key ? "shadow-sm" : ""}
            style={
              activeTask === task.key
                ? { borderColor: "var(--ymt-color-feedback-info)" }
                : undefined
            }
            styles={{
              body: {
                minHeight: 104,
                background:
                  activeTask === task.key
                    ? "var(--ymt-color-feedback-info-bg)"
                    : undefined,
              },
            }}
          >
            <Space align="start" className="w-full">
              <span
                style={{
                  color:
                    activeTask === task.key
                      ? "var(--ymt-color-feedback-info)"
                      : "var(--ymt-color-text-secondary)",
                }}
              >
                {task.icon}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <Text strong>{task.title}</Text>
                  {recommended.task === task.key && (
                    <Tag color="gold">推荐</Tag>
                  )}
                </div>
                <div
                  className="mt-1 text-xs"
                  style={{ color: "var(--ymt-color-text-secondary)" }}
                >
                  {task.description}
                </div>
              </div>
            </Space>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(420px,0.9fr)_minmax(480px,1.1fr)]">
        <Card title={currentTask.title}>
          {renderTaskForm()}
          <Collapse
            className="mt-5"
            size="small"
            ghost
            items={[
              {
                key: "advanced",
                label: "高级配置",
                children: (
                  <Text type="secondary">
                    当前任务会复用现有 AI
                    能力并自动传入产品上下文。生成结果只作为建议，写入产品、页面或活动前都需要人工确认。
                  </Text>
                ),
              },
            ]}
          />
        </Card>

        <Card title="结果预览与应用">{renderResult()}</Card>
      </div>

      <Modal
        title="确认应用到产品资料？"
        width={720}
        open={productApplyOpen}
        okText="应用到产品资料"
        cancelText="取消"
        confirmLoading={applyingProduct}
        forceRender
        onCancel={() => setProductApplyOpen(false)}
        onOk={handleProductApply}
      >
        <div className="pt-3">
          <Alert
            className="mb-4"
            type="info"
            showIcon
            title="AI 结果会先写入下方字段，保存前可人工修改；不会自动发布消费者页面。"
          />
          <Form form={productApplyForm} layout="vertical">
            <Form.Item name="name" label="产品名称">
              <Input />
            </Form.Item>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <Form.Item name="category" label="品类">
                <Select showSearch allowClear options={CATEGORY_OPTIONS} />
              </Form.Item>
              <Form.Item name="origin" label="产地">
                <Input />
              </Form.Item>
            </div>
            <Form.Item name="description" label="产品介绍">
              <TextArea rows={3} />
            </Form.Item>
            <Form.Item name="story_title" label="故事标题">
              <Input />
            </Form.Item>
            <Form.Item name="story_content" label="品牌/产品故事">
              <TextArea rows={4} />
            </Form.Item>
          </Form>
        </div>
      </Modal>

      <Modal
        title="确认创建活动草稿？"
        width={680}
        open={campaignDraftOpen}
        okText="创建活动草稿"
        cancelText="取消"
        confirmLoading={creatingCampaign}
        forceRender
        onCancel={() => setCampaignDraftOpen(false)}
        onOk={handleCampaignDraftCreate}
      >
        <div className="pt-3">
          <Alert
            className="mb-4"
            type="info"
            showIcon
            title="活动会以草稿状态创建，不会自动上线。创建后可在活动管理继续维护权益、规则和投放。"
          />
          <Form form={campaignDraftForm} layout="vertical">
            <Form.Item
              name="name"
              label="活动名称"
              rules={[{ required: true, message: "请输入活动名称" }]}
            >
              <Input />
            </Form.Item>
            <Form.Item
              name="campaign_type"
              label="活动类型"
              rules={[{ required: true, message: "请选择活动类型" }]}
            >
              <Select options={CAMPAIGN_TYPE_OPTIONS} />
            </Form.Item>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <Form.Item
                name="start_at"
                label="开始时间"
                rules={[{ required: true, message: "请输入开始时间" }]}
              >
                <Input placeholder="2026-06-01T00:00:00" />
              </Form.Item>
              <Form.Item
                name="end_at"
                label="结束时间"
                rules={[{ required: true, message: "请输入结束时间" }]}
              >
                <Input placeholder="2026-06-30T23:59:59" />
              </Form.Item>
            </div>
            <Form.Item name="description" label="活动描述">
              <TextArea rows={2} />
            </Form.Item>
            <Form.Item name="participation_conditions" label="参与条件">
              <TextArea rows={2} />
            </Form.Item>
            <Form.Item name="claim_limits" label="领取限制">
              <Input />
            </Form.Item>
            <Form.Item name="validity_period" label="有效期">
              <Input />
            </Form.Item>
            <Form.Item name="disclaimer" label="免责声明">
              <TextArea rows={2} />
            </Form.Item>
          </Form>
        </div>
      </Modal>
    </div>
  );
}
