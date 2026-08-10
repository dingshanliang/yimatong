"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useCrud } from "@/lib/hooks";
import {
  Alert,
  App,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import ProductionBatchFormFields, {
  buildBatchPayload,
  formatBatchSkuLabel,
  type ProductionBatchFormValues,
} from "@/components/ProductionBatchFormFields";
import api from "@/lib/api";
import dayjs from "dayjs";
import { STATUS_COLORS } from "@/lib/status-colors";
import { useAuthStore } from "@/lib/auth";
import { catalogAccessForPrincipal } from "@/lib/catalog-access";
import { useTenantPlanReadOnly } from "../_components/TenantPlanReadOnly";

const { Title } = Typography;

interface ProductionBatch {
  id: string;
  product_id: string;
  product_name?: string;
  sku_id: string;
  sku_name?: string;
  sku_code?: string;
  batch_code: string;
  production_date: string;
  expiry_date: string;
  origin?: string;
  status: string;
  effective_status: string;
  recall_reason?: string | null;
  recalled_at?: string | null;
  recalled_by?: string | null;
}

interface Product {
  id: string;
  name: string;
  origin?: string;
}

interface SKU {
  id: string;
  name: string;
  product_id: string;
  code?: string;
}

const BATCH_STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "有效", color: STATUS_COLORS.success },
  recalled: { label: "已召回", color: STATUS_COLORS.error },
  expired: { label: "已过期", color: STATUS_COLORS.neutral },
};

export default function BatchesPage() {
  const user = useAuthStore((state) => state.user);
  const access = catalogAccessForPrincipal(user);

  if (!access.canRead) {
    return (
      <Alert type="warning" showIcon title="当前账号无权访问生产批次目录" />
    );
  }

  return (
    <BatchesCatalog canWrite={access.canWrite} canRecall={access.canRecall} />
  );
}

function BatchesCatalog({
  canWrite,
  canRecall,
}: {
  canWrite: boolean;
  canRecall: boolean;
}) {
  const { message } = App.useApp();
  const router = useRouter();
  const planReadOnly = useTenantPlanReadOnly();
  const [products, setProducts] = useState<Product[]>([]);
  const [productsLoading, setProductsLoading] = useState(true);
  const [productLoadError, setProductLoadError] = useState(false);
  const [skus, setSKUs] = useState<SKU[]>([]);
  const [skusLoading, setSKUsLoading] = useState(false);
  const [skuLoadError, setSkuLoadError] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<ProductionBatch | null>(null);
  const [recallItem, setRecallItem] = useState<ProductionBatch | null>(null);
  const [recalling, setRecalling] = useState(false);
  const [form] = Form.useForm<ProductionBatchFormValues>();
  const [recallForm] = Form.useForm<{ reason: string }>();
  const [selectedProduct, setSelectedProduct] = useState<string | undefined>(
    undefined
  );

  const {
    items: batches,
    total,
    page,
    loading,
    error,
    setPage,
    setFilter,
    create,
    update,
    retry,
  } = useCrud<ProductionBatch>("/production-batches");
  const writesDisabled = planReadOnly || !canWrite;
  const createDisabled =
    writesDisabled ||
    productsLoading ||
    productLoadError ||
    products.length === 0;
  const submitDisabled =
    writesDisabled ||
    productLoadError ||
    productsLoading ||
    skuLoadError ||
    skusLoading ||
    skus.length === 0 ||
    Boolean(editItem && editItem.effective_status !== "active");

  const fetchProducts = useCallback(async () => {
    setProductsLoading(true);
    setProductLoadError(false);
    try {
      const { data } = await api.get("/products", {
        params: { page_size: 100 },
      });
      setProducts(data.items || []);
    } catch {
      setProductLoadError(true);
    } finally {
      setProductsLoading(false);
    }
  }, []);

  const fetchSKUs = useCallback(async (productId?: string) => {
    if (!productId) {
      setSKUs([]);
      setSkuLoadError(false);
      setSKUsLoading(false);
      return;
    }
    setSKUsLoading(true);
    setSkuLoadError(false);
    try {
      const { data } = await api.get("/skus", {
        params: { product_id: productId, page_size: 100 },
      });
      setSKUs(data.items || []);
    } catch {
      setSkuLoadError(true);
    } finally {
      setSKUsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProducts();
  }, [fetchProducts]);

  const openCreate = () => {
    if (createDisabled) return;
    setEditItem(null);
    form.resetFields();
    setSelectedProduct(undefined);
    setSKUs([]);
    setModalOpen(true);
  };

  const openEdit = async (batch: ProductionBatch) => {
    if (writesDisabled || batch.effective_status !== "active") return;
    setEditItem(batch);
    setSelectedProduct(batch.product_id);
    form.setFieldsValue({
      ...batch,
      production_date: dayjs(batch.production_date),
      expiry_date: dayjs(batch.expiry_date),
    });
    setModalOpen(true);
    await fetchSKUs(batch.product_id);
  };

  const handleSubmit = async (values: ProductionBatchFormValues) => {
    if (submitDisabled) return;
    try {
      const payload: Record<string, unknown> = buildBatchPayload(values);
      if (editItem) {
        const updatePayload = { ...payload };
        delete updatePayload.product_id;
        delete updatePayload.sku_id;
        await update(editItem.id, updatePayload);
        message.success("生产批次更新成功");
      } else {
        await create(payload);
        message.success("批次已创建，可继续生成码批次或维护扫码页关联");
      }
      setModalOpen(false);
      form.resetFields();
      setSelectedProduct(undefined);
    } catch {
      message.error("保存批次失败");
    }
  };

  const handleProductChange = (productId?: string) => {
    setSelectedProduct(productId);
    void fetchSKUs(productId);
    const product = products.find((item) => item.id === productId);
    form.setFieldsValue({ origin: product?.origin || undefined });
  };

  const handleCreateSkuClick = () => {
    if (selectedProduct) router.push(`/products/${selectedProduct}`);
  };

  const openRecall = (batch: ProductionBatch) => {
    if (!canRecall || planReadOnly || batch.effective_status !== "active")
      return;
    recallForm.resetFields();
    setRecallItem(batch);
  };

  const handleRecall = async ({ reason }: { reason: string }) => {
    if (
      !recallItem ||
      !canRecall ||
      planReadOnly ||
      recallItem.effective_status !== "active"
    )
      return;
    setRecalling(true);
    try {
      await api.post(`/production-batches/${recallItem.id}/recall`, {
        reason: reason.trim(),
        confirm: "recall",
      });
      message.success("生产批次已召回，关联码的权益入口已关闭");
      setRecallItem(null);
      recallForm.resetFields();
      await retry();
    } catch {
      message.error("召回失败，请重试");
    } finally {
      setRecalling(false);
    }
  };

  const columns: ColumnsType<ProductionBatch> = [
    {
      title: "产品",
      dataIndex: "product_name",
      key: "product_name",
      render: (v?: string) => v || "未关联",
    },
    {
      title: "SKU",
      key: "sku",
      render: (_: unknown, record) =>
        formatBatchSkuLabel({
          name: record.sku_name || "",
          code: record.sku_code,
        }),
    },
    { title: "批次号", dataIndex: "batch_code", key: "batch_code" },
    { title: "生产日期", dataIndex: "production_date", key: "production_date" },
    { title: "过期日期", dataIndex: "expiry_date", key: "expiry_date" },
    {
      title: "产地",
      dataIndex: "origin",
      key: "origin",
      render: (v?: string) => v || "未填写",
    },
    {
      title: "状态",
      dataIndex: "effective_status",
      key: "status",
      render: (s: string) => {
        const info = BATCH_STATUS_MAP[s] || {
          label: s,
          color: STATUS_COLORS.neutral,
        };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record) => (
        <Space>
          <Button
            type="link"
            size="small"
            disabled={writesDisabled || record.effective_status !== "active"}
            title={
              planReadOnly
                ? "套餐已到期，续期后可编辑生产批次"
                : record.effective_status !== "active"
                  ? "终态生产批次不可编辑"
                  : undefined
            }
            onClick={() => void openEdit(record)}
          >
            编辑
          </Button>
          {canRecall && record.effective_status === "active" && (
            <Button
              danger
              type="link"
              size="small"
              disabled={planReadOnly}
              title={planReadOnly ? "套餐已到期，续期后可执行召回" : undefined}
              onClick={() => openRecall(record)}
            >
              召回批次
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          生产批次管理
        </Title>
        <Space>
          <Select
            placeholder="按产品筛选"
            loading={productsLoading}
            allowClear
            style={{ width: 200 }}
            onChange={(v) => setFilter(v ? { product_id: v } : {})}
            options={products.map((p) => ({ value: p.id, label: p.name }))}
            showSearch
            optionFilterProp="label"
          />
          <Button
            type="primary"
            icon={<PlusOutlined />}
            disabled={createDisabled}
            title={
              planReadOnly ? "套餐已到期，续期后可新建生产批次" : undefined
            }
            onClick={openCreate}
          >
            新建批次
          </Button>
        </Space>
      </div>
      {productLoadError && (
        <Alert
          className="mb-4"
          type="error"
          showIcon
          title="产品选项加载失败，新建批次暂不可用"
          action={<Button onClick={() => void fetchProducts()}>重试</Button>}
        />
      )}
      {!productsLoading && !productLoadError && products.length === 0 && (
        <Alert
          className="mb-4"
          type="info"
          showIcon
          title="暂无可用产品，请先创建产品和 SKU 后再新增生产批次"
        />
      )}
      {Boolean(error) && (
        <Alert
          className="mb-4"
          type="error"
          showIcon
          title="生产批次列表加载失败"
          action={<Button onClick={() => void retry()}>重试</Button>}
        />
      )}
      {!error && (
        <Table
          columns={columns}
          dataSource={batches}
          rowKey="id"
          loading={loading}
          locale={{ emptyText: <Empty description="暂无生产批次" /> }}
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
        title={editItem ? "编辑生产批次" : "新建生产批次"}
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false);
          setSelectedProduct(undefined);
          setEditItem(null);
        }}
        onOk={() => form.submit()}
        okText={editItem ? "更新批次" : "创建批次"}
        okButtonProps={{ disabled: submitDisabled }}
        width={560}
        forceRender
      >
        <Form<ProductionBatchFormValues>
          form={form}
          layout="vertical"
          disabled={
            writesDisabled ||
            Boolean(editItem && editItem.effective_status !== "active")
          }
          onFinish={handleSubmit}
        >
          <ProductionBatchFormFields
            form={form}
            products={products}
            skus={skus}
            selectedProductId={selectedProduct}
            editing={!!editItem}
            skusLoading={skusLoading}
            skuLoadError={skuLoadError}
            onProductChange={handleProductChange}
            onCreateSkuClick={handleCreateSkuClick}
            onRetrySkus={() => void fetchSKUs(selectedProduct)}
            onDateRangeReset={() =>
              message.warning("保质期至不能早于生产日期，已清空原日期")
            }
          />
        </Form>
      </Modal>
      <Modal
        title={`召回生产批次${recallItem ? `：${recallItem.batch_code}` : ""}`}
        open={Boolean(recallItem)}
        okText="确认召回"
        okButtonProps={{ danger: true, disabled: planReadOnly }}
        confirmLoading={recalling}
        onCancel={() => {
          setRecallItem(null);
          recallForm.resetFields();
        }}
        onOk={() => recallForm.submit()}
        forceRender
      >
        <Alert
          className="mb-4"
          type="warning"
          showIcon
          title="召回后该批次不可继续编辑，关联码不再提供权益入口"
        />
        <Form form={recallForm} layout="vertical" onFinish={handleRecall}>
          <Form.Item
            name="reason"
            label="召回原因"
            rules={[
              { required: true, whitespace: true, message: "请输入召回原因" },
              { max: 500, message: "召回原因不能超过 500 个字符" },
            ]}
            normalize={(value: string) => value.trimStart()}
          >
            <Input.TextArea maxLength={500} showCount rows={4} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
