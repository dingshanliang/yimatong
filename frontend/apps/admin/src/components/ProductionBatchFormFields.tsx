import {
  Button,
  DatePicker,
  Form,
  Input,
  Select,
  Space,
  Typography,
} from "antd";
import type { FormInstance } from "antd";
import type { Dayjs } from "dayjs";

const { Text } = Typography;

export interface BatchProductOption {
  id: string;
  name: string;
  origin?: string;
}

export interface BatchSKUOption {
  id: string;
  product_id: string;
  name: string;
  code?: string;
}

export interface ProductionBatchFormValues {
  product_id?: string;
  sku_id?: string;
  batch_code?: string;
  origin?: string | null;
  production_date?: Dayjs;
  expiry_date?: Dayjs;
}

interface ProductionBatchFormFieldsProps {
  form: FormInstance<ProductionBatchFormValues>;
  products?: BatchProductOption[];
  skus: BatchSKUOption[];
  selectedProductId?: string;
  productLocked?: boolean;
  editing?: boolean;
  skusLoading?: boolean;
  skuLoadError?: boolean;
  onProductChange?: (productId?: string) => void;
  onCreateSkuClick?: () => void;
  onRetrySkus?: () => void;
  onDateRangeReset?: () => void;
}

function buildBatchToken(input?: string) {
  const token = (input || "")
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "")
    .slice(0, 8);
  return token || "001";
}

export function formatBatchSkuLabel(
  sku?: Pick<BatchSKUOption, "name" | "code">
) {
  if (!sku || !sku.name) return "未关联";
  return sku.code ? `${sku.name}（${sku.code}）` : sku.name;
}

export function buildBatchPayload(
  values: ProductionBatchFormValues,
  productId?: string
) {
  return {
    ...values,
    product_id: productId || values.product_id,
    origin: values.origin?.trim() || null,
    production_date: values.production_date?.format("YYYY-MM-DD"),
    expiry_date: values.expiry_date?.format("YYYY-MM-DD"),
  };
}

export default function ProductionBatchFormFields({
  form,
  products = [],
  skus,
  selectedProductId,
  productLocked = false,
  editing = false,
  skusLoading = false,
  skuLoadError = false,
  onProductChange,
  onCreateSkuClick,
  onRetrySkus,
  onDateRangeReset,
}: ProductionBatchFormFieldsProps) {
  const productionDate = Form.useWatch("production_date", form);
  const skuId = Form.useWatch("sku_id", form);
  const watchedProductId = Form.useWatch("product_id", form);
  const productId = selectedProductId || watchedProductId;
  const hasProduct = Boolean(productId);
  const hasSku = skus.length > 0;
  const canGenerateBatchCode = Boolean(skuId && productionDate);

  const handleGenerateBatchCode = () => {
    const values = form.getFieldsValue();
    const date = values.production_date?.format("YYYYMMDD");
    const sku = skus.find((item) => item.id === values.sku_id);
    if (!date || !sku) return;

    const token = buildBatchToken(sku.code || sku.name);
    form.setFieldValue("batch_code", `PB-${date}-${token}`);
  };

  const handleProductionDateChange = () => {
    const values = form.getFieldsValue();
    if (
      values.production_date &&
      values.expiry_date &&
      values.expiry_date.isBefore(values.production_date, "day")
    ) {
      form.setFieldValue("expiry_date", undefined);
      onDateRangeReset?.();
    }
  };

  return (
    <>
      {!productLocked && (
        <Form.Item
          name="product_id"
          label="关联产品"
          rules={[{ required: true, message: "请选择产品" }]}
        >
          <Select
            placeholder="选择产品"
            options={products.map((product) => ({
              value: product.id,
              label: product.name,
            }))}
            showSearch
            optionFilterProp="label"
            disabled={editing}
            onChange={(value) => {
              form.setFieldsValue({ sku_id: undefined });
              onProductChange?.(value);
            }}
            allowClear
          />
        </Form.Item>
      )}

      <Form.Item
        name="sku_id"
        label="关联 SKU"
        rules={[{ required: true, message: "请选择 SKU" }]}
        extra={
          hasProduct && skuLoadError ? (
            <Space size={8}>
              <Text type="danger">SKU 选项加载失败，暂不能保存批次</Text>
              {onRetrySkus && (
                <Button
                  type="link"
                  size="small"
                  className="!px-0"
                  onClick={onRetrySkus}
                >
                  重试
                </Button>
              )}
            </Space>
          ) : hasProduct && skusLoading ? (
            <Text type="secondary">正在加载 SKU 选项</Text>
          ) : hasProduct && !hasSku ? (
            <Space size={8}>
              <Text type="secondary">
                该产品暂无 SKU，请先创建 SKU 后再新增批次
              </Text>
              {onCreateSkuClick && (
                <Button
                  type="link"
                  size="small"
                  className="!px-0"
                  onClick={onCreateSkuClick}
                >
                  去创建 SKU
                </Button>
              )}
            </Space>
          ) : undefined
        }
      >
        <Select
          placeholder={hasProduct ? "选择 SKU" : "请先选择产品"}
          options={skus.map((sku) => ({
            value: sku.id,
            label: formatBatchSkuLabel(sku),
          }))}
          showSearch
          optionFilterProp="label"
          loading={skusLoading}
          disabled={
            !hasProduct || editing || !hasSku || skusLoading || skuLoadError
          }
        />
      </Form.Item>

      <Form.Item label="批次号" required>
        <Space.Compact className="w-full">
          <Form.Item
            name="batch_code"
            noStyle
            rules={[{ required: true, message: "请输入批次号" }]}
          >
            <Input placeholder="例如 PB-20260531-001" />
          </Form.Item>
          <Button
            disabled={!canGenerateBatchCode}
            title={
              !canGenerateBatchCode ? "请先选择 SKU 和生产日期" : undefined
            }
            onClick={handleGenerateBatchCode}
          >
            一键生成
          </Button>
        </Space.Compact>
        <Text type="secondary" className="mt-1 block text-xs">
          用于后台识别和溯源码绑定，可自动生成后再修改。
        </Text>
      </Form.Item>

      <Form.Item name="origin" label="本批次产地">
        <Input
          allowClear
          placeholder="默认带出产品产地，可按本批次实际产地修改"
        />
      </Form.Item>

      <div className="grid grid-cols-1 gap-x-4 md:grid-cols-2">
        <Form.Item
          name="production_date"
          label="生产日期"
          rules={[{ required: true, message: "请选择生产日期" }]}
        >
          <DatePicker
            className="w-full"
            onChange={handleProductionDateChange}
          />
        </Form.Item>
        <Form.Item
          name="expiry_date"
          label="保质期至"
          dependencies={["production_date"]}
          rules={[
            { required: true, message: "请选择保质期至" },
            ({ getFieldValue }) => ({
              validator(_, value: Dayjs | undefined) {
                const start = getFieldValue("production_date") as
                  Dayjs | undefined;
                if (!start || !value || !value.isBefore(start, "day"))
                  return Promise.resolve();
                return Promise.reject(new Error("保质期至不能早于生产日期"));
              },
            }),
          ]}
        >
          <DatePicker className="w-full" minDate={productionDate} />
        </Form.Item>
      </div>
    </>
  );
}
