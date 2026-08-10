"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useCrud } from "@/lib/hooks";
import {
  Alert,
  App,
  Button,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import {
  QrcodeOutlined,
  PlusOutlined,
  DownloadOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { codeAccessForPrincipal, type CodeAccess } from "@/lib/code-access";
import { formatDate } from "@/lib/format";
import { STATUS_COLORS } from "@/lib/status-colors";
import { useTenantPlanReadOnly } from "../_components/TenantPlanReadOnly";

const { Title } = Typography;

interface CodeBatch {
  id: string;
  batch_code: string;
  quantity: number;
  product_id: string;
  sku_id: string;
  production_batch_id?: string;
  code_type: string;
  generation_mode: string;
  status: string;
  source?: "generated" | "imported";
  expected_item_count?: number;
  created_at?: string;
  product_name?: string;
  sku_name?: string;
  sku_code?: string;
  production_batch_code?: string;
  production_date?: string;
  production_origin?: string;
}

interface Product {
  id: string;
  name: string;
}

interface SKU {
  id: string;
  name: string;
  code?: string;
  product_id: string;
}

interface ProductionBatch {
  id: string;
  batch_code: string;
  product_id: string;
  sku_id: string;
  production_date: string;
  expiry_date: string;
  origin?: string;
  status: string;
  effective_status: "active" | "recalled" | "expired";
}

interface CodeBatchFormValues {
  product_id: string;
  sku_id: string;
  production_batch_id: string;
  generation_mode: "item_level" | "batch_level";
  quantity?: number;
  code_type: string;
  source: "generated" | "imported";
}

interface DeliveryFormValues {
  reason: string;
  recipient: string;
  confirm: "deliver";
}

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待生成", color: STATUS_COLORS.neutral },
  generating: { label: "生成中", color: STATUS_COLORS.processing },
  completed: { label: "已生成", color: STATUS_COLORS.success },
  exported: { label: "已导出", color: STATUS_COLORS.processing },
  printing: { label: "印刷中", color: STATUS_COLORS.warning },
  delivered: { label: "已交付", color: STATUS_COLORS.warning },
  activated: { label: "已激活", color: STATUS_COLORS.processing },
  failed: { label: "失败", color: STATUS_COLORS.error },
};

const FILTER_STATUS_OPTIONS = [
  { value: "generating", label: STATUS_MAP.generating.label },
  { value: "completed", label: STATUS_MAP.completed.label },
  { value: "exported", label: STATUS_MAP.exported.label },
  { value: "printing", label: STATUS_MAP.printing.label },
  { value: "delivered", label: STATUS_MAP.delivered.label },
  { value: "activated", label: STATUS_MAP.activated.label },
  { value: "failed", label: STATUS_MAP.failed.label },
];

const CODE_TYPE_OPTIONS = [
  { value: "single", label: "普通二维码" },
  { value: "paired", label: "内外双码" },
];

const CODE_TYPE_DESCRIPTIONS: Record<string, string> = {
  single: "普通二维码：每个包装一个独立二维码，适合大多数溯源场景。",
  paired:
    "内外双码：生成内码和外码配对，用于外包装引流、内包装验真或权益核销。",
};

const GENERATION_MODE_OPTIONS = [
  { value: "item_level", label: "一物一码" },
  { value: "batch_level", label: "一批一码" },
];

const GENERATION_MODE_LABELS: Record<string, string> = {
  item_level: "一物一码",
  batch_level: "一批一码",
};

function getCodeTypeLabel(value?: string) {
  return (
    CODE_TYPE_OPTIONS.find((option) => option.value === value)?.label ||
    value ||
    "-"
  );
}

function getDownloadFilename(
  contentDisposition: string | undefined,
  fallback: string
) {
  if (!contentDisposition) return fallback;

  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    return decodeURIComponent(utf8Match[1].replace(/"/g, ""));
  }

  const filenameMatch = contentDisposition.match(/filename="?([^"]+)"?/i);
  return filenameMatch?.[1] || fallback;
}

function getProductDisplay(record: CodeBatch) {
  return record.product_name || "未获取到产品名称";
}

function getSkuDisplay(record: CodeBatch) {
  if (!record.sku_name) return "未获取到 SKU 名称";
  return `${record.sku_name}${record.sku_code ? `（${record.sku_code}）` : ""}`;
}

export default function CodesPage() {
  const user = useAuthStore((state) => state.user);
  const access = codeAccessForPrincipal(user);

  if (!access.canRead) {
    return <Alert type="warning" showIcon title="当前账号无权访问码管理" />;
  }

  return <CodesCatalog access={access} />;
}

function CodesCatalog({ access }: { access: CodeAccess }) {
  const { message, modal } = App.useApp();
  const planReadOnly = useTenantPlanReadOnly();
  const [products, setProducts] = useState<Product[]>([]);
  const [productsLoading, setProductsLoading] = useState(false);
  const [productsError, setProductsError] = useState(false);
  const [skus, setSKUs] = useState<SKU[]>([]);
  const [skusLoading, setSKUsLoading] = useState(false);
  const [skusError, setSKUsError] = useState(false);
  const [productionBatches, setProductionBatches] = useState<ProductionBatch[]>(
    []
  );
  const [productionBatchesLoading, setProductionBatchesLoading] =
    useState(false);
  const [productionBatchesError, setProductionBatchesError] = useState(false);
  const [status, setStatus] = useState<string | undefined>(undefined);
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm<CodeBatchFormValues>();
  const [deliveryForm] = Form.useForm<DeliveryFormValues>();
  const [selectedProduct, setSelectedProduct] = useState<string | undefined>(
    undefined
  );
  const [selectedSku, setSelectedSku] = useState<string | undefined>(undefined);
  const [creating, setCreating] = useState(false);
  const [activatingId, setActivatingId] = useState<string | undefined>(
    undefined
  );
  const [exportingId, setExportingId] = useState<string | undefined>(undefined);
  const [markingPrintingId, setMarkingPrintingId] = useState<
    string | undefined
  >(undefined);
  const [markingDeliveredId, setMarkingDeliveredId] = useState<
    string | undefined
  >(undefined);
  const [importingId, setImportingId] = useState<string | undefined>(undefined);
  const [deliveryTarget, setDeliveryTarget] = useState<CodeBatch | null>(null);
  const createAttempt = useRef<
    { idempotencyKey: string; payload: string } | undefined
  >(undefined);

  const generationMode = Form.useWatch("generation_mode", form) || "item_level";
  const codeType = Form.useWatch("code_type", form) || "single";
  const source = Form.useWatch("source", form) || "generated";
  const quantity = Form.useWatch("quantity", form);
  const selectedProductionBatchId = Form.useWatch("production_batch_id", form);

  const {
    items: batches,
    total,
    page,
    loading,
    error,
    setPage,
    setFilter,
    mutate,
    retry,
  } = useCrud<CodeBatch>("/code-batches");

  const fetchProducts = useCallback(async () => {
    setProductsLoading(true);
    setProductsError(false);
    try {
      const { data } = await api.get("/products", {
        params: { page_size: 100 },
      });
      setProducts(data.items || []);
    } catch {
      setProducts([]);
      setProductsError(true);
    } finally {
      setProductsLoading(false);
    }
  }, []);

  const fetchSKUs = useCallback(async (productId?: string) => {
    if (!productId) {
      setSKUs([]);
      setProductionBatches([]);
      setSKUsError(false);
      setProductionBatchesError(false);
      return;
    }
    setSKUsLoading(true);
    setSKUsError(false);
    try {
      const { data } = await api.get("/skus", {
        params: { product_id: productId, page_size: 100 },
      });
      setSKUs(data.items || []);
    } catch {
      setSKUs([]);
      setSKUsError(true);
    } finally {
      setSKUsLoading(false);
    }
  }, []);

  const fetchProductionBatches = useCallback(
    async (productId?: string, skuId?: string) => {
      if (!productId || !skuId) {
        setProductionBatches([]);
        setProductionBatchesError(false);
        return;
      }
      setProductionBatchesLoading(true);
      setProductionBatchesError(false);
      try {
        const { data } = await api.get("/production-batches", {
          params: { product_id: productId, sku_id: skuId, page_size: 100 },
        });
        setProductionBatches(
          (data.items || []).filter(
            (batch: ProductionBatch) => batch.effective_status === "active"
          )
        );
      } catch {
        setProductionBatches([]);
        setProductionBatchesError(true);
      } finally {
        setProductionBatchesLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    if (access.canGenerate) void fetchProducts();
  }, [access.canGenerate, fetchProducts]);

  const resetCreateState = () => {
    setCreateOpen(false);
    form.resetFields();
    setSelectedProduct(undefined);
    setSelectedSku(undefined);
    setSKUs([]);
    setProductionBatches([]);
    setSKUsError(false);
    setProductionBatchesError(false);
    createAttempt.current = undefined;
  };

  const submitCreate = async (values: CodeBatchFormValues) => {
    if (planReadOnly || !access.canGenerate) return;
    const payload = {
      product_id: values.product_id,
      sku_id: values.sku_id,
      production_batch_id: values.production_batch_id,
      generation_mode: values.generation_mode,
      quantity: values.generation_mode === "batch_level" ? 1 : values.quantity,
      code_type:
        values.generation_mode === "batch_level" ? "single" : values.code_type,
      source: values.source,
    };
    const serializedPayload = JSON.stringify(payload);
    if (
      !createAttempt.current ||
      createAttempt.current.payload !== serializedPayload
    ) {
      createAttempt.current = {
        idempotencyKey: crypto.randomUUID(),
        payload: serializedPayload,
      };
    }
    setCreating(true);
    try {
      await api.post("/code-batches", payload, {
        headers: { "Idempotency-Key": createAttempt.current.idempotencyKey },
      });
      message.success(
        values.source === "imported"
          ? "接管批次已创建，请导入声明数量的既有码"
          : "码批次已生成，可继续导出码表、激活或关联扫码页"
      );
      resetCreateState();
      mutate();
    } catch {
      message.error("创建失败");
    } finally {
      setCreating(false);
    }
  };

  const handleCreate = async (values: CodeBatchFormValues) => {
    if (
      values.generation_mode === "item_level" &&
      Number(values.quantity || 0) >= 10000
    ) {
      modal.confirm({
        title: "确认生成大量二维码？",
        content: `本次将生成 ${Number(values.quantity).toLocaleString()} 个二维码，生成后会进入码批次列表用于导出码表和激活。`,
        okText: "确认生成",
        cancelText: "再检查一下",
        onOk: () => submitCreate(values),
      });
      return;
    }
    await submitCreate(values);
  };

  const handleActivate = async (id: string) => {
    if (planReadOnly || !access.canManage) return;
    setActivatingId(id);
    try {
      await api.post(`/code-batches/${id}/activate`);
      message.success("码批次已激活");
      mutate();
    } catch {
      message.error("激活失败");
    } finally {
      setActivatingId(undefined);
    }
  };

  const handleExport = async (record: CodeBatch) => {
    if (planReadOnly || !access.canExport) return;
    setExportingId(record.id);
    try {
      const response = await api.post<Blob>(
        `/code-batches/${record.id}/export`,
        null,
        { responseType: "blob" }
      );
      const blob = response.data;
      if (!blob || blob.size === 0) {
        message.warning("当前码批次暂无可导出的码，请检查生成状态");
        return;
      }

      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = getDownloadFilename(
        response.headers["content-disposition"],
        `codes-${record.id}.csv`
      );
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      message.success("码表已导出，可用于打印二维码或交付印刷");
    } catch {
      message.error("导出失败，请确认码批次状态和账号权限");
    } finally {
      setExportingId(undefined);
    }
  };

  const renderActivationSummary = (record: CodeBatch) => (
    <div className="mt-3">
      <Alert
        className="mb-3"
        type="warning"
        showIcon
        title="激活后，该批二维码将对消费者扫码生效。请确认码表已导出，并已完成印刷或贴码安排。"
      />
      <Descriptions size="small" column={1} bordered>
        <Descriptions.Item label="产品">
          {getProductDisplay(record)}
        </Descriptions.Item>
        <Descriptions.Item label="SKU">
          {getSkuDisplay(record)}
        </Descriptions.Item>
        <Descriptions.Item label="生产批次">
          {record.production_batch_code || "未关联生产批次"}
        </Descriptions.Item>
        <Descriptions.Item label="生成方式">
          {GENERATION_MODE_LABELS[record.generation_mode] || "一物一码"}
        </Descriptions.Item>
        <Descriptions.Item label="数量">
          {Number(record.quantity || 0).toLocaleString()}
        </Descriptions.Item>
        <Descriptions.Item label="码类型">
          {getCodeTypeLabel(record.code_type)}
        </Descriptions.Item>
      </Descriptions>
    </div>
  );

  const showActivateConfirm = (record: CodeBatch) => {
    modal.confirm({
      title: "激活码批次",
      content: renderActivationSummary(record),
      okText: "确认激活",
      cancelText: "取消",
      width: 560,
      onOk: () => handleActivate(record.id),
    });
  };

  const handleMarkPrinting = async (id: string) => {
    if (planReadOnly || !access.canManage) return;
    setMarkingPrintingId(id);
    try {
      await api.post(`/code-batches/${id}/mark-printing`);
      message.success("已标记为印刷中");
      mutate();
    } catch {
      message.error("标记印刷中失败");
    } finally {
      setMarkingPrintingId(undefined);
    }
  };

  const handleMarkDelivered = async (values: DeliveryFormValues) => {
    if (!deliveryTarget) return;
    if (planReadOnly || !access.canManage) return;
    setMarkingDeliveredId(deliveryTarget.id);
    try {
      await api.post(
        `/code-batches/${deliveryTarget.id}/mark-delivered`,
        values
      );
      message.success("已标记为已交付");
      setDeliveryTarget(null);
      deliveryForm.resetFields();
      mutate();
    } catch {
      message.error("标记已交付失败");
    } finally {
      setMarkingDeliveredId(undefined);
    }
  };

  const handleImportExistingCodes = async (record: CodeBatch, file: File) => {
    if (planReadOnly || !access.canGenerate) return;
    if (!file.name.toLowerCase().endsWith(".csv")) {
      message.error("请选择 CSV 文件");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);
    setImportingId(record.id);
    try {
      await api.post("/imports/existing-codes", formData, {
        params: { code_batch_id: record.id },
      });
      message.success("既有码导入完成，码批次已进入可导出状态");
      mutate();
    } catch {
      message.error("既有码导入失败，请核对数量、格式和码值唯一性");
    } finally {
      setImportingId(undefined);
    }
  };

  const columns: ColumnsType<CodeBatch> = [
    { title: "批次号", dataIndex: "batch_code", key: "batch_code" },
    {
      title: "产品/SKU",
      key: "product_sku",
      render: (_: unknown, record: CodeBatch) => (
        <div>
          <div>{getProductDisplay(record)}</div>
          <div className="text-xs text-text-muted">{getSkuDisplay(record)}</div>
        </div>
      ),
    },
    {
      title: "生产批次",
      key: "production_batch",
      render: (_: unknown, record: CodeBatch) => (
        <div>
          <div>{record.production_batch_code || "未关联生产批次"}</div>
          {record.production_date ? (
            <div className="text-xs text-text-muted">
              {record.production_date}
            </div>
          ) : null}
        </div>
      ),
    },
    {
      title: "生成方式",
      dataIndex: "generation_mode",
      key: "generation_mode",
      render: (v: string) => GENERATION_MODE_LABELS[v] || "一物一码",
    },
    {
      title: "包装/码数量",
      dataIndex: "quantity",
      key: "quantity",
      render: (v: number, record: CodeBatch) => (
        <div>
          <div>
            {Number(v || 0).toLocaleString()}
            {record.code_type === "paired" ? " 组" : ""}
          </div>
          {record.expected_item_count != null ? (
            <div className="text-xs text-text-muted">
              {Number(record.expected_item_count).toLocaleString()} 个物理码
            </div>
          ) : null}
        </div>
      ),
    },
    {
      title: "码类型",
      dataIndex: "code_type",
      key: "code_type",
      render: (t: string) => getCodeTypeLabel(t),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = STATUS_MAP[s] || {
          label: s,
          color: STATUS_COLORS.neutral,
        };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v?: string) => formatDate(v),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: CodeBatch) => {
        const canImport =
          access.canGenerate &&
          record.source === "imported" &&
          record.status === "generating";
        const hasLifecycleAction = [
          "activated",
          "completed",
          "exported",
          "printing",
          "delivered",
        ].includes(record.status);

        return (
          <Space>
            <Link href={`/codes/${record.id}`}>详情</Link>
            {canImport ? (
              <Upload
                accept=".csv,text/csv"
                showUploadList={false}
                beforeUpload={(file) => {
                  void handleImportExistingCodes(record, file);
                  return Upload.LIST_IGNORE;
                }}
                disabled={planReadOnly || importingId === record.id}
              >
                <Button
                  size="small"
                  icon={<UploadOutlined />}
                  loading={importingId === record.id}
                  disabled={planReadOnly}
                >
                  导入既有码
                </Button>
              </Upload>
            ) : null}
            {access.canExport &&
              [
                "activated",
                "completed",
                "exported",
                "printing",
                "delivered",
              ].includes(record.status) && (
                <Button
                  size="small"
                  icon={<DownloadOutlined />}
                  onClick={() => handleExport(record)}
                  loading={exportingId === record.id}
                  disabled={planReadOnly}
                >
                  导出码表
                </Button>
              )}
            {access.canManage && record.status === "exported" && (
              <Button
                size="small"
                loading={markingPrintingId === record.id}
                onClick={() => handleMarkPrinting(record.id)}
                disabled={planReadOnly}
              >
                标记印刷中
              </Button>
            )}
            {access.canManage && record.status === "printing" && (
              <Button
                size="small"
                loading={markingDeliveredId === record.id}
                onClick={() => setDeliveryTarget(record)}
                disabled={planReadOnly}
              >
                标记已交付
              </Button>
            )}
            {access.canManage && record.status === "delivered" && (
              <Button
                size="small"
                type="primary"
                loading={activatingId === record.id}
                onClick={() => showActivateConfirm(record)}
                disabled={planReadOnly}
              >
                激活码批次
              </Button>
            )}
            {!canImport && !hasLifecycleAction && (
              <Typography.Text type="secondary">暂无可用操作</Typography.Text>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          码管理
        </Title>
        <Space>
          <Select
            placeholder="按状态筛选"
            allowClear
            style={{ width: 150 }}
            value={status}
            onChange={(v) => {
              setStatus(v);
              setFilter(v ? { status: v } : {});
            }}
            options={FILTER_STATUS_OPTIONS}
          />
          {access.canGenerate ? (
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setCreateOpen(true)}
              disabled={planReadOnly || productsLoading || productsError}
            >
              生成码批次
            </Button>
          ) : null}
        </Space>
      </div>
      {error ? (
        <Alert
          type="error"
          showIcon
          title="码批次加载失败"
          action={
            <Button size="small" onClick={() => void retry()}>
              重试
            </Button>
          }
        />
      ) : batches.length === 0 && !loading ? (
        <Empty
          image={
            <QrcodeOutlined
              style={{
                fontSize: "var(--ymt-font-size-4xl)",
                color: "var(--ymt-color-text-tertiary)",
              }}
            />
          }
          description="暂无码批次，请先生成码批次"
        />
      ) : (
        <Table
          columns={columns}
          dataSource={batches}
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
      )}
      <Modal
        title="生成码批次"
        open={createOpen}
        onCancel={resetCreateState}
        onOk={() => form.submit()}
        okText="生成码批次"
        confirmLoading={creating}
        okButtonProps={{
          disabled:
            planReadOnly ||
            !access.canGenerate ||
            productsLoading ||
            productsError ||
            skusLoading ||
            skusError ||
            productionBatchesLoading ||
            productionBatchesError,
        }}
        forceRender
        width={500}
      >
        <Form<CodeBatchFormValues>
          form={form}
          layout="vertical"
          onFinish={handleCreate}
          disabled={
            planReadOnly ||
            !access.canGenerate ||
            productsLoading ||
            productsError
          }
          initialValues={{
            source: "generated",
            generation_mode: "item_level",
            code_type: "single",
          }}
        >
          {productsError ? (
            <Alert
              className="mb-4"
              type="error"
              showIcon
              title="产品选项加载失败"
              action={
                <Button size="small" onClick={() => void fetchProducts()}>
                  重试
                </Button>
              }
            />
          ) : null}
          <Form.Item name="source" label="码来源" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "generated", label: "系统生成新码" },
                { value: "imported", label: "接管已有印刷码" },
              ]}
              onChange={(value) => {
                if (value === "imported") {
                  form.setFieldsValue({
                    generation_mode: "item_level",
                    code_type: "single",
                  });
                }
              }}
            />
          </Form.Item>
          {source === "imported" ? (
            <Alert
              className="mb-4"
              type="info"
              showIcon
              title="先创建接管批次，再一次性导入声明数量的既有码。"
            />
          ) : null}
          <Form.Item
            name="product_id"
            label="关联产品"
            rules={[{ required: true, message: "请选择产品" }]}
          >
            <Select
              placeholder="选择产品"
              options={products.map((p) => ({ value: p.id, label: p.name }))}
              showSearch
              optionFilterProp="label"
              onChange={(v) => {
                setSelectedProduct(v);
                setSelectedSku(undefined);
                fetchSKUs(v);
                setProductionBatches([]);
                form.setFieldsValue({
                  sku_id: undefined,
                  production_batch_id: undefined,
                });
              }}
              data-testid="code-batch-product-select"
            />
          </Form.Item>
          <Form.Item
            name="sku_id"
            label="关联 SKU"
            rules={[{ required: true, message: "请选择 SKU" }]}
          >
            <Select
              placeholder="选择 SKU"
              options={skus.map((s) => ({
                value: s.id,
                label: `${s.name}${s.code ? `（${s.code}）` : ""}`,
              }))}
              showSearch
              optionFilterProp="label"
              disabled={!selectedProduct || skusLoading || skusError}
              notFoundContent={
                selectedProduct ? (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="该产品暂无 SKU，请先创建 SKU 后再生成码批次"
                  >
                    <Button type="link" href={`/products/${selectedProduct}`}>
                      去产品工作台创建 SKU
                    </Button>
                  </Empty>
                ) : null
              }
              onChange={(v) => {
                setSelectedSku(v);
                fetchProductionBatches(selectedProduct, v);
                form.setFieldValue("production_batch_id", undefined);
              }}
              data-testid="code-batch-sku-select"
            />
          </Form.Item>
          {skusError ? (
            <Alert
              className="mb-4"
              type="error"
              showIcon
              title="SKU 选项加载失败"
              action={
                <Button
                  size="small"
                  onClick={() => void fetchSKUs(selectedProduct)}
                >
                  重试
                </Button>
              }
            />
          ) : null}
          <Form.Item
            name="production_batch_id"
            label="关联生产批次"
            rules={[{ required: true, message: "请选择生产批次" }]}
          >
            <Select
              placeholder="选择生产批次"
              options={productionBatches.map((b) => ({
                value: b.id,
                label: `${b.batch_code} · ${b.production_date}${b.origin ? ` · ${b.origin}` : ""}`,
              }))}
              showSearch
              optionFilterProp="label"
              disabled={
                !selectedSku ||
                productionBatchesLoading ||
                productionBatchesError
              }
              notFoundContent={
                selectedSku ? (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="该 SKU 暂无生产批次，请先创建批次后再生成码批次"
                  >
                    <Button type="link" href={`/products/${selectedProduct}`}>
                      去产品工作台创建批次
                    </Button>
                  </Empty>
                ) : null
              }
              data-testid="code-batch-production-batch-select"
            />
          </Form.Item>
          {productionBatchesError ? (
            <Alert
              className="mb-4"
              type="error"
              showIcon
              title="生产批次选项加载失败"
              action={
                <Button
                  size="small"
                  onClick={() =>
                    void fetchProductionBatches(selectedProduct, selectedSku)
                  }
                >
                  重试
                </Button>
              }
            />
          ) : null}
          <Form.Item
            name="generation_mode"
            label="生成方式"
            rules={[{ required: true, message: "请选择生成方式" }]}
          >
            <Select
              options={GENERATION_MODE_OPTIONS}
              disabled={source === "imported"}
              onChange={(v) => {
                if (v === "batch_level")
                  form.setFieldsValue({ quantity: 1, code_type: "single" });
              }}
              data-testid="code-batch-generation-mode-select"
            />
          </Form.Item>
          {generationMode === "item_level" ? (
            <Form.Item
              name="quantity"
              label="生成数量"
              rules={[{ required: true, message: "请输入数量" }]}
              extra="将生成可导出的二维码数量，生成后可导出码表、激活或关联扫码页。"
            >
              <InputNumber
                min={1}
                max={codeType === "paired" ? 5000 : 10000}
                style={{ width: "100%" }}
                placeholder={codeType === "paired" ? "1-5000 组" : "1-10000"}
                data-testid="code-batch-quantity-input"
              />
            </Form.Item>
          ) : (
            <Alert
              className="mb-4"
              type="info"
              showIcon
              title="一批一码会为当前生产批次生成 1 个共用二维码。"
            />
          )}
          <Form.Item
            name="code_type"
            label="码类型"
            extra={
              generationMode === "batch_level"
                ? "一批一码默认使用普通二维码。"
                : CODE_TYPE_DESCRIPTIONS[codeType]
            }
          >
            <Select
              options={CODE_TYPE_OPTIONS}
              disabled={
                generationMode === "batch_level" || source === "imported"
              }
              data-testid="code-batch-type-select"
            />
          </Form.Item>
          {selectedProductionBatchId ? (
            <Descriptions bordered size="small" column={1} title="生成确认">
              <Descriptions.Item label="产品">
                {products.find((p) => p.id === selectedProduct)?.name || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="SKU">
                {skus.find((s) => s.id === selectedSku)?.name || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="生产批次">
                {productionBatches.find(
                  (b) => b.id === selectedProductionBatchId
                )?.batch_code || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="生成方式">
                {GENERATION_MODE_LABELS[generationMode]}
              </Descriptions.Item>
              <Descriptions.Item label="生成数量">
                {generationMode === "batch_level"
                  ? 1
                  : `${Number(quantity || 0).toLocaleString()}${codeType === "paired" ? " 组" : ""}`}
              </Descriptions.Item>
              <Descriptions.Item label="预计物理码">
                {generationMode === "batch_level"
                  ? 1
                  : Number(quantity || 0) * (codeType === "paired" ? 2 : 1)}
              </Descriptions.Item>
              <Descriptions.Item label="码类型">
                {getCodeTypeLabel(
                  generationMode === "batch_level" ? "single" : codeType
                )}
              </Descriptions.Item>
            </Descriptions>
          ) : null}
        </Form>
      </Modal>
      <Modal
        title="确认码表已交付"
        open={deliveryTarget !== null}
        onCancel={() => {
          setDeliveryTarget(null);
          deliveryForm.resetFields();
        }}
        onOk={() => deliveryForm.submit()}
        okText="确认交付"
        confirmLoading={markingDeliveredId === deliveryTarget?.id}
        okButtonProps={{ disabled: planReadOnly || !access.canManage }}
        forceRender
      >
        <Form<DeliveryFormValues>
          form={deliveryForm}
          layout="vertical"
          onFinish={handleMarkDelivered}
          disabled={planReadOnly || !access.canManage}
          initialValues={{ confirm: "deliver" }}
        >
          <Form.Item
            name="recipient"
            label="交付对象"
            rules={[{ required: true, whitespace: true, max: 255 }]}
          >
            <Input placeholder="例如：华东印刷供应商" />
          </Form.Item>
          <Form.Item
            name="reason"
            label="交付说明"
            rules={[{ required: true, whitespace: true, max: 500 }]}
          >
            <Input.TextArea rows={3} placeholder="说明本次交付用途或交付单据" />
          </Form.Item>
          <Form.Item name="confirm" hidden>
            <input />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
