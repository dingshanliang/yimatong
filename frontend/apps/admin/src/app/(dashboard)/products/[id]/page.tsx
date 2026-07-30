"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import dayjs from "dayjs";
import {
  App,
  Button,
  DatePicker,
  Descriptions,
  Form,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  EditOutlined,
  FileTextOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import FileUploadInput from "@/components/FileUploadInput";
import ImageUploadInput from "@/components/ImageUploadInput";
import ProductionBatchFormFields, {
  buildBatchPayload,
  formatBatchSkuLabel,
  type ProductionBatchFormValues,
} from "@/components/ProductionBatchFormFields";
import SKUFormFields, {
  buildSkuPayload,
  type SKUFormValues,
} from "@/components/SKUFormFields";
import api, { extractErrorMessage } from "@/lib/api";
import { useCategories } from "@/lib/use-categories";
import { createDefaultModules, createEmptyDSL } from "@/lib/page-dsl";
import type {
  Brand,
  Product,
  ProductAsset,
  ProductAssetType,
  ProductionBatch,
  SKU,
} from "../_components/types";

const { Title, Text } = Typography;
const { TextArea } = Input;

const ASSET_TYPE_OPTIONS: Array<{ value: ProductAssetType; label: string }> = [
  { value: "test_report", label: "检测报告" },
  { value: "certificate", label: "资质证书" },
  { value: "image", label: "图片素材" },
  { value: "video", label: "视频素材" },
  { value: "story", label: "图文故事" },
  { value: "other", label: "其他资料" },
];

const ASSET_TYPE_LABELS = Object.fromEntries(
  ASSET_TYPE_OPTIONS.map((item) => [item.value, item.label])
);

type AssetFormFeature =
  "issuer" | "validUntil" | "file" | "image" | "description" | "content";

interface AssetFormConfig {
  nameLabel: string;
  namePlaceholder: string;
  issuerLabel?: string;
  issuerPlaceholder?: string;
  validUntilLabel?: string;
  fileLabel?: string;
  filePlaceholder?: string;
  fileExtra?: string;
  fileRequired?: boolean;
  fileButtonText?: string;
  imageLabel?: string;
  imagePlaceholder?: string;
  imageExtra?: string;
  imageRequired?: boolean;
  imageButtonText?: string;
  descriptionLabel?: string;
  descriptionPlaceholder?: string;
  contentLabel?: string;
  contentPlaceholder?: string;
  contentRequired?: boolean;
  features: AssetFormFeature[];
}

const ASSET_FORM_CONFIGS: Record<ProductAssetType, AssetFormConfig> = {
  test_report: {
    nameLabel: "报告名称",
    namePlaceholder: "例如 2026 年农残检测报告",
    issuerLabel: "检测机构",
    issuerPlaceholder: "例如 黑龙江省农产品质量检测中心",
    validUntilLabel: "有效期至",
    fileLabel: "报告文件",
    filePlaceholder: "上传 PDF/图片，或粘贴公开可访问的报告链接",
    fileExtra:
      "用于扫码页检测报告展示，支持 PDF、PNG、JPG、WebP，单个文件不超过 20MB。",
    fileRequired: true,
    fileButtonText: "上传报告",
    contentLabel: "检测解读",
    contentPlaceholder:
      "用用户能理解的语言说明检测结论，例如各项指标符合标准。",
    features: ["issuer", "validUntil", "file", "content"],
  },
  certificate: {
    nameLabel: "证书名称",
    namePlaceholder: "例如 绿色食品认证证书",
    issuerLabel: "发证机构",
    issuerPlaceholder: "例如 中国绿色食品发展中心",
    validUntilLabel: "有效期至",
    fileLabel: "证书文件",
    filePlaceholder: "上传 PDF/图片，或粘贴公开可访问的证书链接",
    fileExtra:
      "用于扫码页资质证书展示，支持 PDF、PNG、JPG、WebP，单个文件不超过 20MB。",
    fileRequired: true,
    fileButtonText: "上传证书",
    descriptionLabel: "说明",
    descriptionPlaceholder: "补充证书适用范围或展示说明。",
    features: ["issuer", "validUntil", "file", "description"],
  },
  image: {
    nameLabel: "素材名称",
    namePlaceholder: "例如 产地航拍图",
    imageLabel: "图片",
    imagePlaceholder: "上传图片，或粘贴公开可访问的图片链接",
    imageExtra: "用于扫码页图文素材展示，支持 PNG、JPG、WebP，单张不超过 5MB。",
    imageRequired: true,
    imageButtonText: "上传图片",
    descriptionLabel: "说明",
    descriptionPlaceholder: "说明图片内容或适用场景。",
    features: ["image", "description"],
  },
  video: {
    nameLabel: "视频名称",
    namePlaceholder: "例如 产地采收过程视频",
    fileLabel: "公开视频链接",
    filePlaceholder: "粘贴公开视频链接，例如 https://...",
    fileExtra:
      "第一版视频素材仅支持公开可访问的视频链接，暂不支持直接上传视频文件。",
    fileRequired: true,
    imageLabel: "封面图",
    imagePlaceholder: "上传封面图，或粘贴公开可访问的图片链接",
    imageExtra: "用于扫码页视频卡片封面，支持 PNG、JPG、WebP，单张不超过 5MB。",
    imageButtonText: "上传封面",
    descriptionLabel: "说明",
    descriptionPlaceholder: "说明视频内容或推荐展示位置。",
    features: ["file", "image", "description"],
  },
  story: {
    nameLabel: "故事标题",
    namePlaceholder: "例如 来自核心产区的安心好物",
    issuerLabel: "来源",
    issuerPlaceholder: "例如 品牌方、合作社、基地负责人",
    imageLabel: "配图",
    imagePlaceholder: "上传配图，或粘贴公开可访问的图片链接",
    imageExtra: "用于扫码页图文故事展示，支持 PNG、JPG、WebP，单张不超过 5MB。",
    contentLabel: "正文内容",
    contentPlaceholder: "补充品牌、产地、种植/生产过程等消费者关心的信息。",
    contentRequired: true,
    features: ["issuer", "image", "content"],
  },
  other: {
    nameLabel: "资料名称",
    namePlaceholder: "例如 供应商声明文件",
    issuerLabel: "来源",
    issuerPlaceholder: "例如 供应商、合作机构或内部团队",
    fileLabel: "资料文件",
    filePlaceholder: "上传 PDF/图片，或粘贴公开可访问的资料链接",
    fileExtra:
      "用于后台留档或扫码页资料展示，支持 PDF、PNG、JPG、WebP，单个文件不超过 20MB。",
    fileButtonText: "上传资料",
    imageLabel: "资料图片",
    imagePlaceholder: "上传图片，或粘贴公开可访问的图片链接",
    imageExtra: "可作为资料预览图或扫码页展示图。",
    descriptionLabel: "说明",
    descriptionPlaceholder: "说明资料用途或展示方式。",
    features: ["issuer", "file", "image", "description"],
  },
};

const ASSET_FILE_LINK_TEXT: Record<ProductAssetType, string> = {
  test_report: "查看报告",
  certificate: "查看证书",
  image: "查看图片",
  video: "查看视频",
  story: "查看故事",
  other: "查看资料",
};

const ASSET_STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "启用", color: "green" },
  inactive: { label: "停用", color: "default" },
};

const BATCH_STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "有效", color: "green" },
  recalled: { label: "已召回", color: "red" },
  expired: { label: "已过期", color: "gray" },
};

const WORKBENCH_STEP_ACTIONS: Record<string, string> = {
  profile: "完善基础资料",
  skus: "继续维护 SKU",
  batches: "新增生产批次",
  assets: "上传报告证书",
  pages: "配置扫码页",
};

const PROFILE_FIELD_LABELS: Record<string, string> = {
  brand_id: "品牌",
  category: "品类",
  origin: "产地",
  image_url: "产品主图",
  description: "产品介绍",
};

interface PageTemplate {
  id: string;
  name: string;
  template_type: string;
  status: string;
  description?: string;
  published_version?: { version: number } | null;
}

interface PaginatedItems<T> {
  items?: T[];
}

function getErrorStatus(err: unknown): number | undefined {
  if (typeof err !== "object" || err === null || !("response" in err))
    return undefined;

  const response = (err as { response?: { status?: number } }).response;
  return response?.status;
}

async function fetchProductDetail(productId: string): Promise<Product> {
  try {
    const { data } = await api.get<Product>(`/products/${productId}`);
    return data;
  } catch (err) {
    const status = getErrorStatus(err);
    if (status !== 404 && status !== 405) throw err;

    const { data } = await api.get<PaginatedItems<Product>>("/products", {
      params: { page_size: 100 },
    });
    const product = (data.items || []).find((item) => item.id === productId);
    if (!product) throw err;
    return product;
  }
}

async function fetchOptionalItems<T>(
  request: Promise<{ data: PaginatedItems<T> }>
): Promise<T[]> {
  try {
    const { data } = await request;
    return data.items || [];
  } catch {
    return [];
  }
}

export default function ProductWorkbenchPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { message } = App.useApp();
  const productId = params.id;

  const [product, setProduct] = useState<Product | null>(null);
  const [assets, setAssets] = useState<ProductAsset[]>([]);
  const [skus, setSkus] = useState<SKU[]>([]);
  const [batches, setBatches] = useState<ProductionBatch[]>([]);
  const [pages, setPages] = useState<PageTemplate[]>([]);
  const [brands, setBrands] = useState<Brand[]>([]);
  const [loading, setLoading] = useState(true);
  const { categories: tenantCategories } = useCategories();

  const [productForm] = Form.useForm();
  const [assetForm] = Form.useForm();
  const [skuForm] = Form.useForm();
  const [batchForm] = Form.useForm<ProductionBatchFormValues>();
  const [pageForm] = Form.useForm();
  const [assetModalOpen, setAssetModalOpen] = useState(false);
  const [skuModalOpen, setSkuModalOpen] = useState(false);
  const [batchModalOpen, setBatchModalOpen] = useState(false);
  const [pageModalOpen, setPageModalOpen] = useState(false);
  const [editingAsset, setEditingAsset] = useState<ProductAsset | null>(null);
  const [editingSku, setEditingSku] = useState<SKU | null>(null);
  const [editingBatch, setEditingBatch] = useState<ProductionBatch | null>(
    null
  );
  const [activeTab, setActiveTab] = useState("profile");
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<{
    imported: number;
    errors: string[];
  } | null>(null);
  const skuSubmitModeRef = useRef<"close" | "continue">("close");
  const watchedAssetType = Form.useWatch<ProductAssetType>(
    "asset_type",
    assetForm
  );
  const assetFormType =
    watchedAssetType || editingAsset?.asset_type || "test_report";
  const assetFormConfig = ASSET_FORM_CONFIGS[assetFormType];

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const productResp = await fetchProductDetail(productId);
      const [assetItems, skuItems, batchItems, pageItems, brandItems] =
        await Promise.all([
          fetchOptionalItems<ProductAsset>(
            api.get(`/products/${productId}/assets`, {
              params: { page_size: 100 },
            })
          ),
          fetchOptionalItems<SKU>(
            api.get(`/products/${productId}/skus`, {
              params: { page_size: 100 },
            })
          ),
          fetchOptionalItems<ProductionBatch>(
            api.get(`/products/${productId}/batches`, {
              params: { page_size: 100 },
            })
          ),
          fetchOptionalItems<PageTemplate>(
            api.get("/page-templates", {
              params: { product_id: productId, page_size: 100 },
            })
          ),
          fetchOptionalItems<Brand>(
            api.get("/brands", { params: { page_size: 100 } })
          ),
        ]);
      setProduct(productResp);
      setAssets(assetItems);
      setSkus(skuItems);
      setBatches(batchItems);
      setPages(pageItems);
      setBrands(brandItems);
      productForm.setFieldsValue(productResp);
    } catch (err) {
      setProduct(null);
      setAssets([]);
      setSkus([]);
      setBatches([]);
      setPages([]);
      setBrands([]);
      message.error(extractErrorMessage(err, "加载产品工作台失败"));
    } finally {
      setLoading(false);
    }
  }, [message, productForm, productId]);

  useEffect(() => {
    load();
  }, [load]);

  const completeness = useMemo(() => {
    if (!product) return 0;
    const checks = [
      product.name,
      product.brand_id,
      product.category,
      product.origin,
      product.image_url,
      product.description || product.story_content,
      skus.length > 0,
      batches.length > 0,
      assets.some((asset) => asset.asset_type === "test_report"),
      assets.some((asset) => asset.asset_type === "certificate"),
    ];
    return Math.round((checks.filter(Boolean).length / checks.length) * 100);
  }, [assets, batches.length, product, skus.length]);

  const completionSteps = useMemo(() => {
    if (!product) return [];
    return [
      {
        key: "profile",
        label: "基础资料",
        done: Boolean(
          product.name &&
          product.brand_id &&
          product.category &&
          product.origin &&
          product.image_url &&
          (product.description || product.story_content)
        ),
      },
      { key: "skus", label: "SKU", done: skus.length > 0 },
      { key: "batches", label: "批次", done: batches.length > 0 },
      {
        key: "assets",
        label: "检测报告",
        done: assets.some((asset) => asset.asset_type === "test_report"),
      },
      {
        key: "assets",
        label: "资质证书",
        done: assets.some((asset) => asset.asset_type === "certificate"),
      },
      { key: "pages", label: "扫码页", done: pages.length > 0 },
    ];
  }, [assets, batches.length, pages.length, product, skus.length]);

  const nextStep = completionSteps.find((step) => !step.done);

  const scrollToProfileField = useCallback(
    (fieldName: string) => {
      window.setTimeout(() => {
        productForm.scrollToField(fieldName, {
          behavior: "smooth",
          block: "center",
          focus: true,
        });
        message.info(`请补充${PROFILE_FIELD_LABELS[fieldName] || "基础资料"}`);
      }, 0);
    },
    [message, productForm]
  );

  const getFirstIncompleteProfileField = useCallback(() => {
    if (!product) return undefined;

    const values = { ...product, ...productForm.getFieldsValue() } as Product;
    if (!values.brand_id) return "brand_id";
    if (!values.category) return "category";
    if (!values.origin) return "origin";
    if (!values.image_url) return "image_url";
    if (!values.description && !values.story_content) return "description";
    return undefined;
  }, [product, productForm]);

  const handleNextStepClick = useCallback(() => {
    if (!nextStep) return;

    if (nextStep.key !== "profile") {
      setActiveTab(nextStep.key);
      return;
    }

    const incompleteField = getFirstIncompleteProfileField();
    if (activeTab !== "profile") {
      setActiveTab("profile");
    }
    if (incompleteField) {
      scrollToProfileField(incompleteField);
    }
  }, [
    activeTab,
    getFirstIncompleteProfileField,
    nextStep,
    scrollToProfileField,
  ]);

  const handleProductSave = async (values: Record<string, unknown>) => {
    try {
      await api.patch(`/products/${productId}`, values);
      message.success("产品资料已保存");
      load();
    } catch (err) {
      message.error(extractErrorMessage(err, "保存失败"));
    }
  };

  const openAssetModal = (asset?: ProductAsset) => {
    setEditingAsset(asset || null);
    assetForm.resetFields();
    assetForm.setFieldsValue(
      asset
        ? {
            ...asset,
            valid_until: asset.valid_until
              ? dayjs(asset.valid_until)
              : undefined,
          }
        : { asset_type: "test_report" }
    );
    setAssetModalOpen(true);
  };

  const handleAssetTypeChange = (assetType: ProductAssetType) => {
    const currentName = assetForm.getFieldValue("name");
    assetForm.setFieldsValue({
      asset_type: assetType,
      name: currentName,
      issuer: undefined,
      valid_until: undefined,
      file_url: undefined,
      image_url: undefined,
      description: undefined,
      content_text: undefined,
    });
  };

  const handleAssetSubmit = async (values: Record<string, unknown>) => {
    try {
      const assetType = values.asset_type as ProductAssetType;
      const config = ASSET_FORM_CONFIGS[assetType];
      const hasFeature = (feature: AssetFormFeature) =>
        config.features.includes(feature);
      const payload = {
        asset_type: assetType,
        name: values.name,
        issuer: hasFeature("issuer") ? values.issuer : null,
        valid_until:
          hasFeature("validUntil") && values.valid_until
            ? (values.valid_until as dayjs.Dayjs).format("YYYY-MM-DD")
            : null,
        file_url: hasFeature("file") ? values.file_url : null,
        image_url: hasFeature("image") ? values.image_url : null,
        description: hasFeature("description") ? values.description : null,
        content_text: hasFeature("content") ? values.content_text : null,
      };
      if (editingAsset)
        await api.patch(`/product-assets/${editingAsset.id}`, payload);
      else await api.post(`/products/${productId}/assets`, payload);
      message.success(editingAsset ? "资料已更新" : "资料已新增");
      setAssetModalOpen(false);
      load();
    } catch (err) {
      message.error(extractErrorMessage(err, "保存资料失败"));
    }
  };

  const openSkuModal = (sku?: SKU) => {
    setEditingSku(sku || null);
    skuForm.resetFields();
    skuForm.setFieldsValue(
      sku
        ? {
            ...sku,
            package_type: sku.package_type ? [sku.package_type] : undefined,
            spec_entries: Object.entries(sku.specifications || {}).map(
              ([key, value]) => ({ key, value })
            ),
          }
        : { product_id: productId }
    );
    setSkuModalOpen(true);
  };

  const handleSkuSubmit = async (values: SKUFormValues) => {
    try {
      const payload = buildSkuPayload(values, productId);
      if (editingSku) await api.patch(`/skus/${editingSku.id}`, payload);
      else await api.post("/skus", payload);
      message.success(editingSku ? "SKU 已更新" : "SKU 已新增");
      if (skuSubmitModeRef.current === "continue" && !editingSku) {
        skuForm.resetFields();
        skuForm.setFieldsValue({ product_id: productId });
      } else {
        setSkuModalOpen(false);
      }
      load();
    } catch (err) {
      message.error(extractErrorMessage(err, "保存 SKU 失败"));
    }
  };

  const openBatchModal = (batch?: ProductionBatch) => {
    setEditingBatch(batch || null);
    batchForm.resetFields();
    batchForm.setFieldsValue(
      batch
        ? {
            ...batch,
            production_date: dayjs(batch.production_date),
            expiry_date: dayjs(batch.expiry_date),
          }
        : { product_id: productId, origin: product?.origin || undefined }
    );
    setBatchModalOpen(true);
  };

  const handleBatchSubmit = async (values: ProductionBatchFormValues) => {
    try {
      const payload = buildBatchPayload(values, productId);
      if (editingBatch) {
        const updatePayload: Record<string, unknown> = { ...payload };
        delete updatePayload.product_id;
        delete updatePayload.sku_id;
        await api.patch(
          `/production-batches/${editingBatch.id}`,
          updatePayload
        );
      } else {
        await api.post("/production-batches", payload);
      }
      message.success(
        editingBatch
          ? "批次已更新"
          : "批次已创建，可继续生成码批次或维护扫码页关联"
      );
      setBatchModalOpen(false);
      load();
    } catch (err) {
      message.error(extractErrorMessage(err, "保存批次失败"));
    }
  };

  const handleImportCsv = async (values: { sku_id: string; file: File }) => {
    setImporting(true);
    try {
      const formData = new FormData();
      formData.append("product_id", productId);
      formData.append("sku_id", values.sku_id);
      formData.append("file", values.file as unknown as Blob);
      const { data } = await api.post(
        "/production-batches/import-csv",
        formData,
        {
          headers: { "Content-Type": "multipart/form-data" },
        }
      );
      setImportResult(data);
      load();
    } catch (err) {
      message.error(extractErrorMessage(err, "导入失败"));
    } finally {
      setImporting(false);
    }
  };

  const handlePageCreate = async (values: Record<string, string>) => {
    try {
      const { data: tpl } = await api.post("/page-templates", {
        ...values,
        product_id: productId,
      });
      const dsl = createEmptyDSL();
      dsl.modules = createDefaultModules();
      await api.post(`/page-templates/${tpl.id}/versions`, {
        config_json: dsl,
      });
      message.success("扫码页已创建");
      setPageModalOpen(false);
      pageForm.resetFields();
      load();
    } catch (err) {
      message.error(extractErrorMessage(err, "创建扫码页失败"));
    }
  };

  const assetColumns: ColumnsType<ProductAsset> = [
    {
      title: "类型",
      dataIndex: "asset_type",
      key: "asset_type",
      render: (v: ProductAssetType) => ASSET_TYPE_LABELS[v] || v,
    },
    { title: "名称", dataIndex: "name", key: "name" },
    {
      title: "机构/来源",
      dataIndex: "issuer",
      key: "issuer",
      render: (v?: string) => v || "-",
    },
    {
      title: "有效期",
      dataIndex: "valid_until",
      key: "valid_until",
      render: (v?: string) => v || "-",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (v: string) => {
        const status = ASSET_STATUS_MAP[v] || { label: v, color: "default" };
        return <Tag color={status.color}>{status.label}</Tag>;
      },
    },
    {
      title: "文件",
      key: "file",
      render: (_: unknown, record) => {
        const url = record.file_url || record.image_url;
        return url ? (
          <Typography.Link href={url} target="_blank">
            {ASSET_FILE_LINK_TEXT[record.asset_type] || "查看资料"}
          </Typography.Link>
        ) : (
          "未上传"
        );
      },
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: ProductAsset) => (
        <Space>
          <Button
            type="link"
            size="small"
            onClick={() => openAssetModal(record)}
          >
            编辑
          </Button>
          <Popconfirm
            title="确认删除资料"
            description={`删除「${record.name}」？`}
            onConfirm={async () => {
              try {
                await api.delete(`/product-assets/${record.id}`);
                message.success("资料已删除");
                load();
              } catch (err) {
                message.error(extractErrorMessage(err, "删除失败"));
              }
            }}
            okText="删除"
            okButtonProps={{ danger: true }}
          >
            <Button type="link" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const skuColumns: ColumnsType<SKU> = [
    { title: "编码", dataIndex: "code", key: "code" },
    { title: "名称", dataIndex: "name", key: "name" },
    {
      title: "包装",
      dataIndex: "package_type",
      key: "package_type",
      render: (v?: string) => v || "-",
    },
    {
      title: "条码/GTIN",
      dataIndex: "barcode",
      key: "barcode",
      render: (v?: string) => v || "-",
    },
    {
      title: "规格",
      dataIndex: "specifications",
      key: "specifications",
      render: (v?: Record<string, string>) =>
        v && Object.keys(v).length
          ? Object.entries(v)
              .map(([k, val]) => `${k}: ${val}`)
              .join("，")
          : "未填写",
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: SKU) => (
        <Space>
          <Button type="link" size="small" onClick={() => openSkuModal(record)}>
            编辑
          </Button>
          <Popconfirm
            title="确认删除 SKU"
            description={`删除「${record.name}」？有关联批次时将被阻止。`}
            onConfirm={async () => {
              try {
                await api.delete(`/skus/${record.id}`);
                message.success("SKU 已删除");
                load();
              } catch (err) {
                message.error(
                  extractErrorMessage(err, "删除失败，请检查是否有关联批次")
                );
              }
            }}
            okText="删除"
            okButtonProps={{ danger: true }}
          >
            <Button type="link" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const batchColumns: ColumnsType<ProductionBatch> = [
    { title: "批次号", dataIndex: "batch_code", key: "batch_code" },
    {
      title: "SKU",
      key: "sku",
      render: (_: unknown, record) =>
        formatBatchSkuLabel({
          name: record.sku_name || "",
          code: record.sku_code,
        }),
    },
    { title: "生产日期", dataIndex: "production_date", key: "production_date" },
    { title: "保质期至", dataIndex: "expiry_date", key: "expiry_date" },
    {
      title: "产地",
      dataIndex: "origin",
      key: "origin",
      render: (v?: string) => v || product?.origin || "未填写",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => (
        <Tag color={BATCH_STATUS_MAP[s]?.color || "default"}>
          {BATCH_STATUS_MAP[s]?.label || s}
        </Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: ProductionBatch) => (
        <Space>
          <Button
            type="link"
            size="small"
            onClick={() => openBatchModal(record)}
          >
            编辑
          </Button>
          <Popconfirm
            title="确认删除批次"
            description={`删除批次「${record.batch_code}」？有关联码批次时将被阻止。`}
            onConfirm={async () => {
              try {
                await api.delete(`/production-batches/${record.id}`);
                message.success("批次已删除");
                load();
              } catch (err) {
                message.error(
                  extractErrorMessage(err, "删除失败，请检查是否有关联码批次")
                );
              }
            }}
            okText="删除"
            okButtonProps={{ danger: true }}
          >
            <Button type="link" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const pageColumns: ColumnsType<PageTemplate> = [
    { title: "页面名称", dataIndex: "name", key: "name" },
    { title: "类型", dataIndex: "template_type", key: "template_type" },
    {
      title: "发布版本",
      key: "published",
      render: (_: unknown, record) =>
        record.published_version ? (
          <Tag color="blue">v{record.published_version.version}</Tag>
        ) : (
          <Tag>未发布</Tag>
        ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record) => (
        <Space>
          <Button
            size="small"
            onClick={() => router.push(`/pages/${record.id}/edit`)}
          >
            编辑
          </Button>
          <Button
            size="small"
            onClick={() => router.push(`/pages/${record.id}`)}
          >
            版本
          </Button>
        </Space>
      ),
    },
  ];

  if (!product) {
    return (
      <div className="py-20 text-center text-text-muted">
        {loading ? "加载中..." : "产品不存在"}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-5 flex items-start justify-between gap-4">
        <Space direction="vertical" size={4}>
          <Button
            type="link"
            className="!px-0"
            icon={<ArrowLeftOutlined />}
            onClick={() => router.push("/products")}
          >
            返回产品列表
          </Button>
          <Title level={4} className="!mb-0">
            {product.name}
          </Title>
          <Text type="secondary">
            {product.brand_name || "未关联品牌"} ·{" "}
            {product.category || "未填写品类"} ·{" "}
            {product.origin || "未填写产地"}
          </Text>
        </Space>
        <div className="w-56">
          <div className="mb-1 flex justify-between text-xs text-text-muted">
            <span>资料完整度</span>
            <span>{completeness}%</span>
          </div>
          <Progress percent={completeness} size="small" />
        </div>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "profile",
            label: "基础资料",
            children: (
              <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
                <Form
                  form={productForm}
                  layout="vertical"
                  onFinish={handleProductSave}
                >
                  <div className="grid grid-cols-1 gap-x-4 md:grid-cols-2">
                    <Form.Item
                      name="name"
                      label="产品名称"
                      rules={[{ required: true, message: "请输入产品名称" }]}
                    >
                      <Input />
                    </Form.Item>
                    <Form.Item
                      name="brand_id"
                      label="品牌"
                      rules={[{ required: true, message: "请选择品牌" }]}
                    >
                      <Select
                        showSearch
                        allowClear
                        placeholder="选择品牌"
                        optionFilterProp="label"
                        options={brands.map((brand) => ({
                          value: brand.id,
                          label: brand.name,
                        }))}
                      />
                    </Form.Item>
                    <Form.Item name="category" label="品类">
                      <Select
                        showSearch
                        allowClear
                        placeholder="选择或输入品类"
                        options={tenantCategories.map((v) => ({
                          value: v,
                          label: v,
                        }))}
                      />
                    </Form.Item>
                    <Form.Item name="origin" label="产地">
                      <Input placeholder="省/市/县/基地" />
                    </Form.Item>
                    <Form.Item
                      name="image_url"
                      label="产品主图"
                      rules={[
                        {
                          type: "url",
                          message:
                            "请输入以 http:// 或 https:// 开头的图片链接",
                        },
                      ]}
                    >
                      <ImageUploadInput
                        module="product-image"
                        previewAlt="产品主图预览"
                        variant="uploadFirst"
                        emptyText="用于扫码页和产品资料展示，支持 PNG、JPG、WebP，单张不超过 5MB"
                      />
                    </Form.Item>
                  </div>
                  <Form.Item name="description" label="产品介绍">
                    <TextArea
                      rows={3}
                      placeholder="一句话说明产品特点，用于扫码页摘要展示"
                    />
                  </Form.Item>
                  <Form.Item name="story_title" label="故事标题">
                    <Input placeholder="例如 来自核心产区的安心好物" />
                  </Form.Item>
                  <Form.Item name="story_content" label="品牌/产品故事">
                    <TextArea
                      rows={6}
                      placeholder="补充品牌、产地、种植/生产过程等消费者关心的信息"
                    />
                  </Form.Item>
                  <Button
                    type="primary"
                    htmlType="submit"
                    icon={<EditOutlined />}
                  >
                    保存基础资料
                  </Button>
                </Form>
                <Space direction="vertical" size={12} className="w-full">
                  <Descriptions bordered size="small" column={1}>
                    <Descriptions.Item label="SKU 数">
                      {skus.length}
                    </Descriptions.Item>
                    <Descriptions.Item label="批次数">
                      {batches.length}
                    </Descriptions.Item>
                    <Descriptions.Item label="检测报告">
                      {
                        assets.filter((a) => a.asset_type === "test_report")
                          .length
                      }
                    </Descriptions.Item>
                    <Descriptions.Item label="资质证书">
                      {
                        assets.filter((a) => a.asset_type === "certificate")
                          .length
                      }
                    </Descriptions.Item>
                    <Descriptions.Item label="扫码页">
                      {pages.length}
                    </Descriptions.Item>
                  </Descriptions>
                  <div>
                    <div className="mb-2 text-sm font-medium">上线资料清单</div>
                    <Space wrap size={[6, 6]}>
                      {completionSteps.map((step) => (
                        <Tag
                          key={`${step.key}-${step.label}`}
                          color={step.done ? "green" : "default"}
                        >
                          {step.label}
                        </Tag>
                      ))}
                    </Space>
                  </div>
                  {nextStep && (
                    <Button type="primary" block onClick={handleNextStepClick}>
                      {WORKBENCH_STEP_ACTIONS[nextStep.key] || "继续完善资料"}
                    </Button>
                  )}
                </Space>
              </div>
            ),
          },
          {
            key: "assets",
            label: "报告证书/素材",
            children: (
              <div>
                <div className="mb-3 flex justify-end">
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    onClick={() => openAssetModal()}
                  >
                    新增资料
                  </Button>
                </div>
                <Table
                  columns={assetColumns}
                  dataSource={assets}
                  rowKey="id"
                  loading={loading}
                  pagination={{
                    pageSize: 10,
                    showSizeChanger: true,
                    showTotal: (t) => `共 ${t} 条`,
                  }}
                />
              </div>
            ),
          },
          {
            key: "skus",
            label: "SKU",
            children: (
              <div>
                <div className="mb-3 flex justify-end">
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    onClick={() => openSkuModal()}
                  >
                    新增 SKU
                  </Button>
                </div>
                <Table
                  columns={skuColumns}
                  dataSource={skus}
                  rowKey="id"
                  loading={loading}
                  pagination={{
                    pageSize: 10,
                    showSizeChanger: true,
                    showTotal: (t) => `共 ${t} 条`,
                  }}
                />
              </div>
            ),
          },
          {
            key: "batches",
            label: "批次",
            children: (
              <div>
                <div className="mb-3 flex items-center justify-between gap-3">
                  {skus.length === 0 ? (
                    <Text type="secondary">
                      该产品暂无 SKU，请先创建 SKU 后再新增批次。
                    </Text>
                  ) : (
                    <span />
                  )}
                  <Space>
                    {skus.length === 0 && (
                      <Button onClick={() => setActiveTab("skus")}>
                        去创建 SKU
                      </Button>
                    )}
                    <Button
                      type="primary"
                      icon={<PlusOutlined />}
                      onClick={() => openBatchModal()}
                      disabled={skus.length === 0}
                    >
                      新增批次
                    </Button>
                    <Button
                      icon={<FileTextOutlined />}
                      onClick={() => setImportModalOpen(true)}
                      disabled={skus.length === 0}
                    >
                      批量导入
                    </Button>
                  </Space>
                </div>
                <Table
                  columns={batchColumns}
                  dataSource={batches}
                  rowKey="id"
                  loading={loading}
                  pagination={{
                    pageSize: 10,
                    showSizeChanger: true,
                    showTotal: (t) => `共 ${t} 条`,
                  }}
                />
              </div>
            ),
          },
          {
            key: "pages",
            label: "关联扫码页",
            children: (
              <div>
                <div className="mb-3 flex justify-end">
                  <Button
                    type="primary"
                    icon={<FileTextOutlined />}
                    onClick={() => setPageModalOpen(true)}
                  >
                    新建扫码页
                  </Button>
                </div>
                <Table
                  columns={pageColumns}
                  dataSource={pages}
                  rowKey="id"
                  loading={loading}
                  pagination={{
                    pageSize: 10,
                    showSizeChanger: true,
                    showTotal: (t) => `共 ${t} 条`,
                  }}
                />
              </div>
            ),
          },
        ]}
      />

      <Modal
        title={editingAsset ? "编辑资料" : "新增资料"}
        open={assetModalOpen}
        onCancel={() => setAssetModalOpen(false)}
        onOk={() => assetForm.submit()}
        okText={editingAsset ? "更新资料" : "保存资料"}
        width={640}
        forceRender
      >
        <Form form={assetForm} layout="vertical" onFinish={handleAssetSubmit}>
          <Form.Item
            name="asset_type"
            label="资料类型"
            rules={[{ required: true }]}
          >
            <Select
              options={ASSET_TYPE_OPTIONS}
              onChange={handleAssetTypeChange}
            />
          </Form.Item>
          <Form.Item
            name="name"
            label={assetFormConfig.nameLabel}
            rules={[
              { required: true, message: `请输入${assetFormConfig.nameLabel}` },
            ]}
          >
            <Input placeholder={assetFormConfig.namePlaceholder} />
          </Form.Item>
          {assetFormConfig.features.includes("issuer") && (
            <Form.Item name="issuer" label={assetFormConfig.issuerLabel}>
              <Input placeholder={assetFormConfig.issuerPlaceholder} />
            </Form.Item>
          )}
          {assetFormConfig.features.includes("validUntil") && (
            <Form.Item
              name="valid_until"
              label={assetFormConfig.validUntilLabel}
            >
              <DatePicker className="w-full" />
            </Form.Item>
          )}
          {assetFormConfig.features.includes("file") && (
            <Form.Item
              name="file_url"
              label={assetFormConfig.fileLabel}
              extra={
                assetFormType === "video"
                  ? assetFormConfig.fileExtra
                  : undefined
              }
              rules={[
                ...(assetFormConfig.fileRequired
                  ? [
                      {
                        required: true,
                        message: `请填写或上传${assetFormConfig.fileLabel}`,
                      },
                    ]
                  : []),
                {
                  type: "url",
                  message: "请输入以 http:// 或 https:// 开头的链接",
                },
              ]}
            >
              {assetFormType === "video" ? (
                <Input
                  placeholder={assetFormConfig.filePlaceholder}
                  allowClear
                />
              ) : (
                <FileUploadInput
                  module="product-document"
                  buttonText={assetFormConfig.fileButtonText}
                  placeholder={assetFormConfig.filePlaceholder}
                  emptyText={assetFormConfig.fileExtra}
                  variant="uploadFirst"
                />
              )}
            </Form.Item>
          )}
          {assetFormConfig.features.includes("image") && (
            <Form.Item
              name="image_url"
              label={assetFormConfig.imageLabel}
              rules={[
                ...(assetFormConfig.imageRequired
                  ? [
                      {
                        required: true,
                        message: `请上传${assetFormConfig.imageLabel}`,
                      },
                    ]
                  : []),
                {
                  type: "url",
                  message: "请输入以 http:// 或 https:// 开头的图片链接",
                },
              ]}
            >
              <ImageUploadInput
                module="product-asset"
                buttonText={assetFormConfig.imageButtonText}
                placeholder={assetFormConfig.imagePlaceholder}
                previewAlt={`${assetFormConfig.imageLabel || "图片"}预览`}
                emptyText={assetFormConfig.imageExtra}
                variant="uploadFirst"
              />
            </Form.Item>
          )}
          {assetFormConfig.features.includes("description") && (
            <Form.Item
              name="description"
              label={assetFormConfig.descriptionLabel}
            >
              <TextArea
                rows={3}
                placeholder={assetFormConfig.descriptionPlaceholder}
              />
            </Form.Item>
          )}
          {assetFormConfig.features.includes("content") && (
            <Form.Item
              name="content_text"
              label={assetFormConfig.contentLabel}
              rules={
                assetFormConfig.contentRequired
                  ? [
                      {
                        required: true,
                        message: `请输入${assetFormConfig.contentLabel}`,
                      },
                    ]
                  : undefined
              }
            >
              <TextArea
                rows={4}
                placeholder={assetFormConfig.contentPlaceholder}
              />
            </Form.Item>
          )}
        </Form>
      </Modal>

      <Modal
        title={editingSku ? "编辑 SKU" : "新增 SKU"}
        open={skuModalOpen}
        onCancel={() => setSkuModalOpen(false)}
        onOk={() => {
          skuSubmitModeRef.current = "close";
          skuForm.submit();
        }}
        okText={editingSku ? "更新 SKU" : "保存 SKU"}
        width={640}
        forceRender
        footer={(_, { OkBtn, CancelBtn }) => (
          <>
            <CancelBtn />
            {!editingSku && (
              <Button
                onClick={() => {
                  skuSubmitModeRef.current = "continue";
                  skuForm.submit();
                }}
              >
                保存并继续添加
              </Button>
            )}
            <OkBtn />
          </>
        )}
      >
        <Form form={skuForm} layout="vertical" onFinish={handleSkuSubmit}>
          <SKUFormFields form={skuForm} />
        </Form>
      </Modal>

      <Modal
        title={editingBatch ? "编辑批次" : "新增批次"}
        open={batchModalOpen}
        onCancel={() => setBatchModalOpen(false)}
        onOk={() => batchForm.submit()}
        okText={editingBatch ? "更新批次" : "创建批次"}
        width={560}
        forceRender
      >
        <Form<ProductionBatchFormValues>
          form={batchForm}
          layout="vertical"
          onFinish={handleBatchSubmit}
        >
          <ProductionBatchFormFields
            form={batchForm}
            products={[product]}
            skus={skus}
            selectedProductId={productId}
            productLocked
            editing={!!editingBatch}
            onCreateSkuClick={() => {
              setBatchModalOpen(false);
              setActiveTab("skus");
            }}
            onDateRangeReset={() =>
              message.warning("保质期至不能早于生产日期，已清空原日期")
            }
          />
        </Form>
      </Modal>

      <Modal
        title="新建关联扫码页"
        open={pageModalOpen}
        onCancel={() => setPageModalOpen(false)}
        onOk={() => pageForm.submit()}
        forceRender
      >
        <Form
          form={pageForm}
          layout="vertical"
          onFinish={handlePageCreate}
          initialValues={{ template_type: "traceability" }}
        >
          <Form.Item name="name" label="页面名称" rules={[{ required: true }]}>
            <Input placeholder={`${product.name} 扫码页`} />
          </Form.Item>
          <Form.Item
            name="template_type"
            label="页面类型"
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: "product_info", label: "产品信息" },
                { value: "traceability", label: "溯源页" },
                { value: "brand_story", label: "品牌故事" },
                { value: "campaign", label: "活动页" },
              ]}
            />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="批量导入生产批次"
        open={importModalOpen}
        onCancel={() => {
          setImportModalOpen(false);
          setImportResult(null);
        }}
        footer={null}
        width={520}
        forceRender
      >
        {importResult ? (
          <div>
            <p>导入完成：成功 {importResult.imported} 条</p>
            {importResult.errors.length > 0 && (
              <div className="mt-2">
                <p style={{ color: "var(--ymt-color-feedback-danger)" }}>
                  失败 {importResult.errors.length} 条：
                </p>
                <ul
                  className="max-h-40 overflow-auto text-sm"
                  style={{ color: "var(--ymt-color-feedback-danger)" }}
                >
                  {importResult.errors.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </div>
            )}
            <div className="mt-4 flex justify-end">
              <Button
                onClick={() => {
                  setImportModalOpen(false);
                  setImportResult(null);
                }}
              >
                关闭
              </Button>
            </div>
          </div>
        ) : (
          <Form layout="vertical" onFinish={handleImportCsv}>
            <Form.Item
              name="sku_id"
              label="选择 SKU"
              rules={[{ required: true, message: "请选择 SKU" }]}
              initialValue={skus[0]?.id}
            >
              <Select
                options={skus.map((s) => ({
                  value: s.id,
                  label: `${s.name} (${s.code})`,
                }))}
                placeholder="选择 SKU"
              />
            </Form.Item>
            <Form.Item
              name="file"
              label="CSV 文件"
              rules={[{ required: true, message: "请选择 CSV 文件" }]}
              valuePropName="file"
            >
              <input
                type="file"
                accept=".csv"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    const form = e.target.closest("form") as HTMLFormElement;
                    const skuSelect = form?.querySelector(
                      '[name="sku_id"]'
                    ) as HTMLSelectElement;
                    handleImportCsv({
                      sku_id: skuSelect?.value || skus[0]?.id,
                      file,
                    } as unknown as { sku_id: string; file: File });
                  }
                }}
              />
            </Form.Item>
            <p className="text-xs text-text-muted">
              CSV 格式：batch_code, production_date, expiry_date, origin（可选）
            </p>
            <div className="mt-4 flex justify-end">
              <Button onClick={() => setImportModalOpen(false)}>取消</Button>
              <Button
                type="primary"
                htmlType="submit"
                loading={importing}
                className="ml-2"
              >
                导入
              </Button>
            </div>
          </Form>
        )}
      </Modal>
    </div>
  );
}
