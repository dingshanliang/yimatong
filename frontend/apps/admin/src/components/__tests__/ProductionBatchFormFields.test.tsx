import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Form } from "antd";
import dayjs from "dayjs";
import { describe, expect, it, vi } from "vitest";

import ProductionBatchFormFields, {
  type ProductionBatchFormValues,
} from "../ProductionBatchFormFields";

const products = [{ id: "product-1", name: "五常稻花香大米" }];
const skus = [
  {
    id: "sku-1",
    product_id: "product-1",
    name: "五常稻花香 5kg 礼盒装",
    code: "RICE-5KG-001",
  },
];

function FormHarness({
  initialValues,
  editing = false,
  skuOptions = skus,
  skuLoadError = false,
  onRetrySkus,
}: {
  initialValues?: ProductionBatchFormValues;
  editing?: boolean;
  skuOptions?: typeof skus;
  skuLoadError?: boolean;
  onRetrySkus?: () => void;
}) {
  const [form] = Form.useForm<ProductionBatchFormValues>();

  return (
    <Form form={form} initialValues={initialValues}>
      <ProductionBatchFormFields
        form={form}
        products={products}
        skus={skuOptions}
        editing={editing}
        skuLoadError={skuLoadError}
        onRetrySkus={onRetrySkus}
      />
    </Form>
  );
}

describe("ProductionBatchFormFields", () => {
  it("disables batch-code generation until SKU and production date are selected", () => {
    render(<FormHarness />);

    expect(screen.getByRole("button", { name: "一键生成" })).toBeDisabled();
  });

  it("generates a batch code from the selected SKU and production date", async () => {
    render(
      <FormHarness
        initialValues={{
          product_id: "product-1",
          sku_id: "sku-1",
          production_date: dayjs("2026-07-29"),
        }}
      />
    );

    const generateButton = screen.getByRole("button", { name: "一键生成" });
    await waitFor(() => expect(generateButton).toBeEnabled());
    fireEvent.click(generateButton);

    expect(screen.getByPlaceholderText("例如 PB-20260531-001")).toHaveValue(
      "PB-20260729-RICE5KG0"
    );
  });

  it("does not expose lifecycle status as an ordinary edit field", () => {
    render(<FormHarness editing />);

    expect(screen.queryByLabelText("状态")).not.toBeInTheDocument();
  });

  it("distinguishes SKU option failure and offers a retry without clearing the form", () => {
    const retry = vi.fn();
    render(
      <FormHarness
        initialValues={{ product_id: "product-1", origin: "已输入产地" }}
        skuOptions={[]}
        skuLoadError
        onRetrySkus={retry}
      />
    );

    expect(screen.getByText(/SKU 选项加载失败/)).toBeVisible();
    expect(screen.getByLabelText("关联 SKU")).toBeDisabled();
    expect(screen.getByLabelText("本批次产地")).toHaveValue("已输入产地");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
