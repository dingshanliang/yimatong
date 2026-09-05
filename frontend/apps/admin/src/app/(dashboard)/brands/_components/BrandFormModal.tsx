"use client";

import { App, Form, Input, Modal } from "antd";
import ImageUploadInput from "@/components/ImageUploadInput";
import api, { extractErrorMessage } from "@/lib/api";
import { validateCatalogPublicUrl } from "@/lib/catalog-public-url";

interface Brand {
  id: string;
  name: string;
  logo_url?: string;
  description?: string;
  status?: string;
}

interface BrandFormModalProps {
  open: boolean;
  initialValues?: Partial<Brand>;
  onSuccess: () => void;
  onCancel: () => void;
  mode: "create" | "edit";
  readOnly?: boolean;
}

export default function BrandFormModal({
  open,
  initialValues,
  onSuccess,
  onCancel,
  mode,
  readOnly = false,
}: BrandFormModalProps) {
  const { message } = App.useApp();
  const [form] = Form.useForm();

  const handleSubmit = async (values: Record<string, unknown>) => {
    if (readOnly) return;
    try {
      if (mode === "edit" && initialValues?.id) {
        await api.patch(`/brands/${initialValues.id}`, values);
        message.success("品牌更新成功");
      } else {
        const createValues = { ...values };
        delete createValues.status;
        await api.post("/brands", createValues);
        message.success("品牌创建成功");
      }
      onSuccess();
      form.resetFields();
    } catch (err) {
      message.error(
        extractErrorMessage(err, mode === "edit" ? "更新失败" : "创建失败")
      );
    }
  };

  return (
    <Modal
      title={mode === "edit" ? "编辑品牌" : "新建品牌"}
      open={open}
      onCancel={onCancel}
      onOk={() => form.submit()}
      okButtonProps={{ disabled: readOnly }}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        disabled={readOnly}
        onFinish={handleSubmit}
        initialValues={initialValues}
      >
        <Form.Item
          name="name"
          label="品牌名称"
          rules={[{ required: true, message: "请输入品牌名称" }]}
        >
          <Input />
        </Form.Item>
        <Form.Item
          name="logo_url"
          label="品牌 Logo 图片（可选）"
          extra="用于溯源码页面和品牌展示。可直接上传，也可粘贴公开可访问的图片链接。"
          rules={[{ validator: validateCatalogPublicUrl }]}
        >
          <ImageUploadInput module="brand-logo" previewAlt="品牌 Logo 预览" />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <Input.TextArea rows={3} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
