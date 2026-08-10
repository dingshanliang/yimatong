"use client";

import { Button, Form, Input, Select, Space, Typography } from "antd";
import type { FormInstance } from "antd";
import { ThunderboltOutlined } from "@ant-design/icons";
import ImageUploadInput from "@/components/ImageUploadInput";
import { validateCatalogPublicUrl } from "@/lib/catalog-public-url";

const { Text } = Typography;

export interface SKUFormValues {
  product_id?: string;
  code?: string;
  name?: string;
  package_type?: string | string[];
  barcode?: string;
  image_url?: string;
  spec_entries?: SpecEntry[];
}

export type SpecEntry = { key?: string; value?: string };

export const PACKAGE_TYPE_OPTIONS = [
  "袋装",
  "盒装",
  "瓶装",
  "罐装",
  "礼盒装",
  "箱装",
  "散装",
].map((value) => ({
  value,
  label: value,
}));

export const SPEC_QUICK_KEYS = [
  "净含量",
  "规格",
  "包装",
  "等级",
  "口味",
  "箱规",
];

const PACKAGE_CODE_MAP: Record<string, string> = {
  袋装: "BAG",
  盒装: "BOX",
  瓶装: "BTL",
  罐装: "CAN",
  礼盒装: "GIFT",
  箱装: "CASE",
  散装: "BULK",
};

function extractCodeTokens(value?: string): string[] {
  if (!value) return [];
  return (
    value
      .toUpperCase()
      .match(/[A-Z0-9]+/g)
      ?.filter((token) => token.length > 0) || []
  );
}

function compactUniqueTokens(tokens: string[]): string[] {
  const seen = new Set<string>();
  return tokens.filter((token) => {
    if (!token || seen.has(token)) return false;
    seen.add(token);
    return true;
  });
}

export function normalizeSkuSpecifications(
  specEntries?: SpecEntry[]
): Record<string, string> {
  return (specEntries || []).reduce<Record<string, string>>((acc, entry) => {
    const key = entry.key?.trim();
    const value = entry.value?.trim();
    if (key && value) acc[key] = value;
    return acc;
  }, {});
}

export function buildSkuPayload(
  values: SKUFormValues,
  productId?: string
): Record<string, unknown> {
  const specifications = normalizeSkuSpecifications(values.spec_entries);
  const packageType = Array.isArray(values.package_type)
    ? values.package_type[0]
    : values.package_type;
  return {
    product_id: productId || values.product_id,
    code: values.code,
    name: values.name,
    package_type: packageType || null,
    barcode: values.barcode || null,
    image_url: values.image_url || null,
    specifications: Object.keys(specifications).length ? specifications : null,
  };
}

export function generateSkuCode(values: SKUFormValues): string {
  const specifications = normalizeSkuSpecifications(values.spec_entries);
  const specTokens = Object.values(specifications).flatMap(extractCodeTokens);
  const packageType = Array.isArray(values.package_type)
    ? values.package_type[0]
    : values.package_type;
  const packageToken = packageType ? PACKAGE_CODE_MAP[packageType] : undefined;
  const tokens = compactUniqueTokens([
    ...extractCodeTokens(values.name),
    ...(packageToken ? [packageToken] : []),
    ...specTokens,
  ]);
  return `SKU-${tokens.length ? tokens.join("-") : Date.now().toString(36).toUpperCase()}`;
}

interface ProductOption {
  id: string;
  name: string;
}

interface SKUFormFieldsProps {
  form: FormInstance;
  products?: ProductOption[];
  showProductSelect?: boolean;
}

export default function SKUFormFields({
  form,
  products = [],
  showProductSelect = false,
}: SKUFormFieldsProps) {
  const fillGeneratedCode = () => {
    form.setFieldValue(
      "code",
      generateSkuCode(form.getFieldsValue(true) as SKUFormValues)
    );
  };

  return (
    <>
      {showProductSelect && (
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
          />
        </Form.Item>
      )}

      <Form.Item
        name="name"
        label="SKU 名称"
        rules={[{ required: true, message: "请输入 SKU 名称" }]}
      >
        <Input placeholder="例如 5kg 袋装" />
      </Form.Item>

      <Form.List name="spec_entries">
        {(fields, { add, remove }) => (
          <div className="mb-4">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <span>规格属性</span>
              <Space wrap size={6}>
                {SPEC_QUICK_KEYS.map((key) => (
                  <Button
                    key={key}
                    size="small"
                    onClick={() => add({ key, value: "" })}
                  >
                    {key}
                  </Button>
                ))}
                <Button
                  size="small"
                  type="primary"
                  ghost
                  onClick={() => add({ key: "", value: "" })}
                >
                  添加规格
                </Button>
              </Space>
            </div>
            {fields.length === 0 && (
              <Text type="secondary" className="mb-2 block">
                可添加消费者会关注的规格，例如 净含量 = 5kg。
              </Text>
            )}
            {fields.map((field) => (
              <Space key={field.key} className="mb-2 flex" align="baseline">
                <Form.Item
                  {...field}
                  name={[field.name, "key"]}
                  className="!mb-0"
                  rules={[{ required: true, message: "请输入规格名" }]}
                >
                  <Input placeholder="规格名，如 净含量" />
                </Form.Item>
                <Form.Item
                  {...field}
                  name={[field.name, "value"]}
                  className="!mb-0"
                  rules={[{ required: true, message: "请输入规格值" }]}
                >
                  <Input placeholder="规格值，如 5kg" />
                </Form.Item>
                <Button danger type="link" onClick={() => remove(field.name)}>
                  删除
                </Button>
              </Space>
            ))}
          </div>
        )}
      </Form.List>

      <Form.Item name="package_type" label="包装类型">
        <Select
          showSearch
          allowClear
          options={PACKAGE_TYPE_OPTIONS}
          placeholder="选择或输入包装类型，例如 礼盒装"
          optionFilterProp="label"
          mode="tags"
          maxCount={1}
        />
      </Form.Item>

      <Form.Item
        label="SKU 编码"
        extra="用于后台识别、批次和码绑定；可自动生成后再修改。"
        required
      >
        <Space.Compact className="w-full">
          <Form.Item
            name="code"
            noStyle
            rules={[{ required: true, message: "请输入 SKU 编码" }]}
          >
            <Input placeholder="例如 RICE-5KG-GIFT" />
          </Form.Item>
          <Button icon={<ThunderboltOutlined />} onClick={fillGeneratedCode}>
            自动生成
          </Button>
        </Space.Compact>
      </Form.Item>

      <Form.Item
        name="barcode"
        label="条码/GTIN"
        extra="包装上的商品条码，没有可不填。"
      >
        <Input placeholder="例如 6901234567890" />
      </Form.Item>

      <Form.Item
        name="image_url"
        label="SKU 图片（可选）"
        rules={[{ validator: validateCatalogPublicUrl }]}
      >
        <ImageUploadInput
          module="sku-image"
          previewAlt="SKU 图片预览"
          variant="uploadFirst"
          emptyText="用于展示具体规格包装，支持 PNG、JPG、WebP，单张不超过 5MB"
        />
      </Form.Item>
    </>
  );
}
