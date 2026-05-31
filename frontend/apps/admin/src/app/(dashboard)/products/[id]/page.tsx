"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
  Progress,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { ArrowLeftOutlined, EditOutlined, FileTextOutlined, PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import FileUploadInput from "@/components/FileUploadInput";
import ImageUploadInput from "@/components/ImageUploadInput";
import api, { extractErrorMessage } from "@/lib/api";
import { createDefaultModules, createEmptyDSL } from "@/lib/page-dsl";
import type { Brand, Product, ProductAsset, ProductAssetType, ProductionBatch, SKU } from "../_components/types";

const { Title, Text } = Typography;
const { TextArea } = Input;

const ASSET_TYPE_OPTIONS: Array<{ value: ProductAssetType; label: string }> = [
  { value: "image", label: "产品图片" },
  { value: "video", label: "视频素材" },
  { value: "test_report", label: "检测报告" },
  { value: "certificate", label: "资质证书" },
  { value: "story", label: "图文故事" },
  { value: "other", label: "其他资料" },
];

const ASSET_TYPE_LABELS = Object.fromEntries(ASSET_TYPE_OPTIONS.map((item) => [item.value, item.label]));

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

type SpecEntry = { key?: string; value?: string };
type SKUFormValues = Omit<SKU, "id" | "status" | "specifications"> & { spec_entries?: SpecEntry[] };
type BatchFormValues = Omit<ProductionBatch, "id" | "status" | "production_date" | "expiry_date"> & {
  production_date: dayjs.Dayjs;
  expiry_date: dayjs.Dayjs;
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
  if (typeof err !== "object" || err === null || !("response" in err)) return undefined;

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

    const { data } = await api.get<PaginatedItems<Product>>("/products", { params: { page_size: 100 } });
    const product = (data.items || []).find((item) => item.id === productId);
    if (!product) throw err;
    return product;
  }
}

async function fetchOptionalItems<T>(request: Promise<{ data: PaginatedItems<T> }>): Promise<T[]> {
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

  const [productForm] = Form.useForm();
  const [assetForm] = Form.useForm();
  const [skuForm] = Form.useForm();
  const [batchForm] = Form.useForm();
  const [pageForm] = Form.useForm();
  const [assetModalOpen, setAssetModalOpen] = useState(false);
  const [skuModalOpen, setSkuModalOpen] = useState(false);
  const [batchModalOpen, setBatchModalOpen] = useState(false);
  const [pageModalOpen, setPageModalOpen] = useState(false);
  const [editingAsset, setEditingAsset] = useState<ProductAsset | null>(null);
  const [editingSku, setEditingSku] = useState<SKU | null>(null);
  const [editingBatch, setEditingBatch] = useState<ProductionBatch | null>(null);
  const [activeTab, setActiveTab] = useState("profile");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const productResp = await fetchProductDetail(productId);
      const [assetItems, skuItems, batchItems, pageItems, brandItems] = await Promise.all([
        fetchOptionalItems<ProductAsset>(api.get(`/products/${productId}/assets`, { params: { page_size: 100 } })),
        fetchOptionalItems<SKU>(api.get(`/products/${productId}/skus`, { params: { page_size: 100 } })),
        fetchOptionalItems<ProductionBatch>(api.get(`/products/${productId}/batches`, { params: { page_size: 100 } })),
        fetchOptionalItems<PageTemplate>(api.get("/page-templates", { params: { product_id: productId, page_size: 100 } })),
        fetchOptionalItems<Brand>(api.get("/brands", { params: { page_size: 100 } })),
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
      { key: "profile", label: "基础资料", done: Boolean(product.name && product.brand_id && product.category && product.origin && product.image_url && (product.description || product.story_content)) },
      { key: "skus", label: "SKU", done: skus.length > 0 },
      { key: "batches", label: "批次", done: batches.length > 0 },
      { key: "assets", label: "检测报告", done: assets.some((asset) => asset.asset_type === "test_report") },
      { key: "assets", label: "资质证书", done: assets.some((asset) => asset.asset_type === "certificate") },
      { key: "pages", label: "扫码页", done: pages.length > 0 },
    ];
  }, [assets, batches.length, pages.length, product, skus.length]);

  const nextStep = completionSteps.find((step) => !step.done);

  const scrollToProfileField = useCallback((fieldName: string) => {
    window.setTimeout(() => {
      productForm.scrollToField(fieldName, {
        behavior: "smooth",
        block: "center",
        focus: true,
      });
      message.info(`请补充${PROFILE_FIELD_LABELS[fieldName] || "基础资料"}`);
    }, 0);
  }, [message, productForm]);

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
  }, [activeTab, getFirstIncompleteProfileField, nextStep, scrollToProfileField]);

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
    assetForm.setFieldsValue(asset ? {
      ...asset,
      valid_until: asset.valid_until ? dayjs(asset.valid_until) : undefined,
    } : { asset_type: "test_report" });
    setAssetModalOpen(true);
  };

  const handleAssetSubmit = async (values: Record<string, unknown>) => {
    try {
      const payload = {
        ...values,
        valid_until: values.valid_until ? (values.valid_until as dayjs.Dayjs).format("YYYY-MM-DD") : undefined,
      };
      if (editingAsset) await api.patch(`/product-assets/${editingAsset.id}`, payload);
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
    skuForm.setFieldsValue(sku ? {
      ...sku,
      spec_entries: Object.entries(sku.specifications || {}).map(([key, value]) => ({ key, value })),
    } : { product_id: productId });
    setSkuModalOpen(true);
  };

  const handleSkuSubmit = async (values: SKUFormValues) => {
    try {
      const specifications = (values.spec_entries || []).reduce<Record<string, string>>((acc, entry) => {
        const key = entry.key?.trim();
        const value = entry.value?.trim();
        if (key && value) acc[key] = value;
        return acc;
      }, {});
      const rest = { ...values };
      delete rest.spec_entries;
      const payload = { ...rest, product_id: productId, specifications };
      if (editingSku) await api.patch(`/skus/${editingSku.id}`, payload);
      else await api.post("/skus", payload);
      message.success(editingSku ? "SKU 已更新" : "SKU 已新增");
      setSkuModalOpen(false);
      load();
    } catch (err) {
      message.error(extractErrorMessage(err, "保存 SKU 失败"));
    }
  };

  const openBatchModal = (batch?: ProductionBatch) => {
    setEditingBatch(batch || null);
    batchForm.resetFields();
    batchForm.setFieldsValue(batch ? {
      ...batch,
      production_date: dayjs(batch.production_date),
      expiry_date: dayjs(batch.expiry_date),
    } : { product_id: productId });
    setBatchModalOpen(true);
  };

  const handleBatchSubmit = async (values: BatchFormValues) => {
    try {
      const payload = {
        ...values,
        product_id: productId,
        production_date: values.production_date.format("YYYY-MM-DD"),
        expiry_date: values.expiry_date.format("YYYY-MM-DD"),
      };
      if (editingBatch) {
        const updatePayload: Record<string, unknown> = { ...payload };
        delete updatePayload.product_id;
        delete updatePayload.sku_id;
        await api.patch(`/production-batches/${editingBatch.id}`, updatePayload);
      } else {
        await api.post("/production-batches", payload);
      }
      message.success(editingBatch ? "批次已更新" : "批次已新增");
      setBatchModalOpen(false);
      load();
    } catch (err) {
      message.error(extractErrorMessage(err, "保存批次失败"));
    }
  };

  const handlePageCreate = async (values: Record<string, string>) => {
    try {
      const { data: tpl } = await api.post("/page-templates", { ...values, product_id: productId });
      const dsl = createEmptyDSL();
      dsl.modules = createDefaultModules();
      await api.post(`/page-templates/${tpl.id}/versions`, { config_json: dsl });
      message.success("扫码页已创建");
      setPageModalOpen(false);
      pageForm.resetFields();
      load();
    } catch (err) {
      message.error(extractErrorMessage(err, "创建扫码页失败"));
    }
  };

  const assetColumns: ColumnsType<ProductAsset> = [
    { title: "类型", dataIndex: "asset_type", key: "asset_type", render: (v: ProductAssetType) => ASSET_TYPE_LABELS[v] || v },
    { title: "名称", dataIndex: "name", key: "name" },
    { title: "机构/来源", dataIndex: "issuer", key: "issuer", render: (v?: string) => v || "-" },
    { title: "有效期", dataIndex: "valid_until", key: "valid_until", render: (v?: string) => v || "-" },
    {
      title: "文件",
      key: "file",
      render: (_: unknown, record) => {
        const url = record.file_url || record.image_url;
        return url ? <Typography.Link href={url} target="_blank">查看文件</Typography.Link> : "-";
      },
    },
    { title: "操作", key: "actions", render: (_: unknown, record) => <Button type="link" size="small" onClick={() => openAssetModal(record)}>编辑</Button> },
  ];

  const skuColumns: ColumnsType<SKU> = [
    { title: "编码", dataIndex: "code", key: "code" },
    { title: "名称", dataIndex: "name", key: "name" },
    { title: "包装", dataIndex: "package_type", key: "package_type", render: (v?: string) => v || "-" },
    { title: "条码/GTIN", dataIndex: "barcode", key: "barcode", render: (v?: string) => v || "-" },
    { title: "规格", dataIndex: "specifications", key: "specifications", render: (v?: Record<string, string>) => v ? Object.entries(v).map(([k, val]) => `${k}: ${val}`).join("，") : "-" },
    { title: "操作", key: "actions", render: (_: unknown, record) => <Button type="link" size="small" onClick={() => openSkuModal(record)}>编辑</Button> },
  ];

  const batchColumns: ColumnsType<ProductionBatch> = [
    { title: "批次号", dataIndex: "batch_code", key: "batch_code" },
    { title: "SKU", dataIndex: "sku_name", key: "sku_name", render: (v?: string) => v || "-" },
    { title: "生产日期", dataIndex: "production_date", key: "production_date" },
    { title: "保质期至", dataIndex: "expiry_date", key: "expiry_date" },
    { title: "产地", dataIndex: "origin", key: "origin", render: (v?: string) => v || product?.origin || "-" },
    { title: "状态", dataIndex: "status", key: "status", render: (s: string) => <Tag color={BATCH_STATUS_MAP[s]?.color || "default"}>{BATCH_STATUS_MAP[s]?.label || s}</Tag> },
    { title: "操作", key: "actions", render: (_: unknown, record) => <Button type="link" size="small" onClick={() => openBatchModal(record)}>编辑</Button> },
  ];

  const pageColumns: ColumnsType<PageTemplate> = [
    { title: "页面名称", dataIndex: "name", key: "name" },
    { title: "类型", dataIndex: "template_type", key: "template_type" },
    { title: "发布版本", key: "published", render: (_: unknown, record) => record.published_version ? <Tag color="blue">v{record.published_version.version}</Tag> : <Tag>未发布</Tag> },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record) => (
        <Space>
          <Button size="small" onClick={() => router.push(`/pages/${record.id}/edit`)}>编辑</Button>
          <Button size="small" onClick={() => router.push(`/pages/${record.id}`)}>版本</Button>
        </Space>
      ),
    },
  ];

  if (!product) {
    return <div className="py-20 text-center text-gray-400">{loading ? "加载中..." : "产品不存在"}</div>;
  }

  return (
    <div>
      <div className="mb-5 flex items-start justify-between gap-4">
        <Space direction="vertical" size={4}>
          <Button type="link" className="!px-0" icon={<ArrowLeftOutlined />} onClick={() => router.push("/products")}>返回产品列表</Button>
          <Title level={4} className="!mb-0">{product.name}</Title>
          <Text type="secondary">{product.brand_name || "未关联品牌"} · {product.category || "未填写品类"} · {product.origin || "未填写产地"}</Text>
        </Space>
        <div className="w-56">
          <div className="mb-1 flex justify-between text-xs text-gray-500">
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
                <Form form={productForm} layout="vertical" onFinish={handleProductSave}>
                  <div className="grid grid-cols-1 gap-x-4 md:grid-cols-2">
                    <Form.Item name="name" label="产品名称" rules={[{ required: true, message: "请输入产品名称" }]}><Input /></Form.Item>
                    <Form.Item name="brand_id" label="品牌" rules={[{ required: true, message: "请选择品牌" }]}>
                      <Select
                        showSearch
                        allowClear
                        placeholder="选择品牌"
                        optionFilterProp="label"
                        options={brands.map((brand) => ({ value: brand.id, label: brand.name }))}
                      />
                    </Form.Item>
                    <Form.Item name="category" label="品类"><Input /></Form.Item>
                    <Form.Item name="origin" label="产地"><Input placeholder="省/市/县/基地" /></Form.Item>
                    <Form.Item
                      name="image_url"
                      label="产品主图"
                      rules={[{ type: "url", message: "请输入以 http:// 或 https:// 开头的图片链接" }]}
                    >
                      <ImageUploadInput
                        module="product-image"
                        previewAlt="产品主图预览"
                        variant="uploadFirst"
                        emptyText="用于扫码页和产品资料展示，支持 PNG、JPG、WebP，单张不超过 5MB"
                      />
                    </Form.Item>
                  </div>
                  <Form.Item name="description" label="产品介绍"><TextArea rows={3} placeholder="一句话说明产品特点，用于扫码页摘要展示" /></Form.Item>
                  <Form.Item name="story_title" label="故事标题"><Input placeholder="例如 来自核心产区的安心好物" /></Form.Item>
                  <Form.Item name="story_content" label="品牌/产品故事"><TextArea rows={6} placeholder="补充品牌、产地、种植/生产过程等消费者关心的信息" /></Form.Item>
                  <Button type="primary" htmlType="submit" icon={<EditOutlined />}>保存基础资料</Button>
                </Form>
                <Space direction="vertical" size={12} className="w-full">
                  <Descriptions bordered size="small" column={1}>
                    <Descriptions.Item label="SKU 数">{skus.length}</Descriptions.Item>
                    <Descriptions.Item label="批次数">{batches.length}</Descriptions.Item>
                    <Descriptions.Item label="检测报告">{assets.filter((a) => a.asset_type === "test_report").length}</Descriptions.Item>
                    <Descriptions.Item label="资质证书">{assets.filter((a) => a.asset_type === "certificate").length}</Descriptions.Item>
                    <Descriptions.Item label="扫码页">{pages.length}</Descriptions.Item>
                  </Descriptions>
                  <div>
                    <div className="mb-2 text-sm font-medium">上线资料清单</div>
                    <Space wrap size={[6, 6]}>
                      {completionSteps.map((step) => (
                        <Tag key={`${step.key}-${step.label}`} color={step.done ? "green" : "default"}>{step.label}</Tag>
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
                <div className="mb-3 flex justify-end"><Button type="primary" icon={<PlusOutlined />} onClick={() => openAssetModal()}>新增资料</Button></div>
                <Table columns={assetColumns} dataSource={assets} rowKey="id" loading={loading} pagination={false} />
              </div>
            ),
          },
          {
            key: "skus",
            label: "SKU",
            children: (
              <div>
                <div className="mb-3 flex justify-end"><Button type="primary" icon={<PlusOutlined />} onClick={() => openSkuModal()}>新增 SKU</Button></div>
                <Table columns={skuColumns} dataSource={skus} rowKey="id" loading={loading} pagination={false} />
              </div>
            ),
          },
          {
            key: "batches",
            label: "批次",
            children: (
              <div>
                <div className="mb-3 flex justify-end"><Button type="primary" icon={<PlusOutlined />} onClick={() => openBatchModal()} disabled={skus.length === 0}>新增批次</Button></div>
                <Table columns={batchColumns} dataSource={batches} rowKey="id" loading={loading} pagination={false} />
              </div>
            ),
          },
          {
            key: "pages",
            label: "关联扫码页",
            children: (
              <div>
                <div className="mb-3 flex justify-end"><Button type="primary" icon={<FileTextOutlined />} onClick={() => setPageModalOpen(true)}>新建扫码页</Button></div>
                <Table columns={pageColumns} dataSource={pages} rowKey="id" loading={loading} pagination={false} />
              </div>
            ),
          },
        ]}
      />

      <Modal title={editingAsset ? "编辑资料" : "新增资料"} open={assetModalOpen} onCancel={() => setAssetModalOpen(false)} onOk={() => assetForm.submit()} width={640}>
        <Form form={assetForm} layout="vertical" onFinish={handleAssetSubmit}>
          <Form.Item name="asset_type" label="资料类型" rules={[{ required: true }]}><Select options={ASSET_TYPE_OPTIONS} /></Form.Item>
          <Form.Item name="name" label="资料名称" rules={[{ required: true, message: "请输入资料名称" }]}><Input /></Form.Item>
          <Form.Item name="issuer" label="机构/来源"><Input placeholder="检测机构、签发机构或素材来源" /></Form.Item>
          <Form.Item name="valid_until" label="有效期至"><DatePicker className="w-full" /></Form.Item>
          <Form.Item
            name="file_url"
            label="报告/证书文件（可选）"
            extra="用于检测报告、资质证书等资料。可直接上传 PDF 或图片，也可粘贴公开文件链接。"
            rules={[{ type: "url", message: "请输入以 http:// 或 https:// 开头的文件链接" }]}
          >
            <FileUploadInput module="product-document" buttonText="上传资料" />
          </Form.Item>
          <Form.Item
            name="image_url"
            label="图片素材（可选）"
            extra="用于图文素材展示。图片可直接上传；视频请粘贴公开视频地址。"
            rules={[{ type: "url", message: "请输入以 http:// 或 https:// 开头的地址" }]}
          >
            <ImageUploadInput module="product-asset" previewAlt="资料图片预览" />
          </Form.Item>
          <Form.Item name="description" label="说明"><TextArea rows={3} /></Form.Item>
          <Form.Item name="content_text" label="正文/检测解读"><TextArea rows={4} /></Form.Item>
        </Form>
      </Modal>

      <Modal title={editingSku ? "编辑 SKU" : "新增 SKU"} open={skuModalOpen} onCancel={() => setSkuModalOpen(false)} onOk={() => skuForm.submit()} width={640}>
        <Form form={skuForm} layout="vertical" onFinish={handleSkuSubmit}>
          <Form.Item name="code" label="SKU 编码" rules={[{ required: true }]}><Input placeholder="例如 RICE-5KG" /></Form.Item>
          <Form.Item name="name" label="SKU 名称" rules={[{ required: true }]}><Input placeholder="例如 5kg 袋装" /></Form.Item>
          <Form.Item name="package_type" label="包装类型"><Input /></Form.Item>
          <Form.Item name="barcode" label="条码/GTIN"><Input /></Form.Item>
          <Form.Item
            name="image_url"
            label="SKU 图片（可选）"
            extra="用于展示具体规格包装。可直接上传，也可粘贴公开图片链接。"
            rules={[{ type: "url", message: "请输入以 http:// 或 https:// 开头的图片链接" }]}
          >
            <ImageUploadInput module="sku-image" previewAlt="SKU 图片预览" />
          </Form.Item>
          <Form.List name="spec_entries">
            {(fields, { add, remove }) => (
              <div>
                <div className="mb-2 flex justify-between"><span>规格属性</span><Button size="small" onClick={() => add({ key: "", value: "" })}>添加规格</Button></div>
                {fields.map((field) => (
                  <Space key={field.key} className="mb-2 flex" align="baseline">
                    <Form.Item {...field} name={[field.name, "key"]} className="!mb-0"><Input placeholder="规格名" /></Form.Item>
                    <Form.Item {...field} name={[field.name, "value"]} className="!mb-0"><Input placeholder="规格值" /></Form.Item>
                    <Button danger type="link" onClick={() => remove(field.name)}>删除</Button>
                  </Space>
                ))}
              </div>
            )}
          </Form.List>
        </Form>
      </Modal>

      <Modal title={editingBatch ? "编辑批次" : "新增批次"} open={batchModalOpen} onCancel={() => setBatchModalOpen(false)} onOk={() => batchForm.submit()} width={560}>
        <Form form={batchForm} layout="vertical" onFinish={handleBatchSubmit}>
          <Form.Item name="sku_id" label="关联 SKU" rules={[{ required: true }]}><Select options={skus.map((sku) => ({ value: sku.id, label: `${sku.name} (${sku.code})` }))} disabled={!!editingBatch} /></Form.Item>
          <Form.Item name="batch_code" label="批次号" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="origin" label="批次产地"><Input placeholder={product.origin || "省/市/县/基地"} /></Form.Item>
          <Space>
            <Form.Item name="production_date" label="生产日期" rules={[{ required: true }]}><DatePicker /></Form.Item>
            <Form.Item name="expiry_date" label="保质期至" rules={[{ required: true }]}><DatePicker /></Form.Item>
          </Space>
        </Form>
      </Modal>

      <Modal title="新建关联扫码页" open={pageModalOpen} onCancel={() => setPageModalOpen(false)} onOk={() => pageForm.submit()}>
        <Form form={pageForm} layout="vertical" onFinish={handlePageCreate} initialValues={{ template_type: "traceability" }}>
          <Form.Item name="name" label="页面名称" rules={[{ required: true }]}><Input placeholder={`${product.name} 扫码页`} /></Form.Item>
          <Form.Item name="template_type" label="页面类型" rules={[{ required: true }]}>
            <Select options={[
              { value: "product_info", label: "产品信息" },
              { value: "traceability", label: "溯源页" },
              { value: "brand_story", label: "品牌故事" },
              { value: "campaign", label: "活动页" },
            ]} />
          </Form.Item>
          <Form.Item name="description" label="描述"><TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
