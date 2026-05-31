"use client";

import { useEffect, useState, useCallback } from "react";
import { useCrud } from "@/lib/hooks";
import { App, Button, Form, Input, Modal, Select, Space, Table, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import ImageUploadInput from "@/components/ImageUploadInput";
import api from "@/lib/api";

const { Title } = Typography;

interface SKU {
  id: string;
  product_id: string;
  product_name?: string;
  code: string;
  name: string;
  specifications?: Record<string, string>;
  package_type?: string;
  barcode?: string;
  image_url?: string;
  status: string;
}

interface Product {
  id: string;
  name: string;
}

type SpecEntry = { key?: string; value?: string };
type SKUFormValues = Omit<SKU, "id" | "status" | "specifications"> & { spec_entries?: SpecEntry[] };

export default function SKUsPage() {
  const { message } = App.useApp();
  const [products, setProducts] = useState<Product[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<SKU | null>(null);
  const [form] = Form.useForm();

  const {
    items: skus, total, page, loading, setPage,
    setFilter, create, update,
  } = useCrud<SKU>("/skus");

  const fetchProducts = useCallback(async () => {
    try {
      const { data } = await api.get("/products", { params: { page_size: 100 } });
      setProducts(data.items || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchProducts(); }, [fetchProducts]);

  const openCreate = () => {
    setEditItem(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (sku: SKU) => {
    setEditItem(sku);
    form.setFieldsValue({
      ...sku,
      spec_entries: Object.entries(sku.specifications || {}).map(([key, value]) => ({ key, value })),
    });
    setModalOpen(true);
  };

  const handleSubmit = async (values: SKUFormValues) => {
    try {
      const specifications = (values.spec_entries || []).reduce<Record<string, string>>((acc, entry) => {
        const key = entry.key?.trim();
        const value = entry.value?.trim();
        if (key && value) acc[key] = value;
        return acc;
      }, {});
      const rest = { ...values };
      delete rest.spec_entries;
      const payload = { ...rest, specifications: Object.keys(specifications).length ? specifications : undefined };
      if (editItem) {
        await update(editItem.id, payload);
        message.success("SKU 更新成功");
      } else {
        await create(payload);
        message.success("SKU 创建成功");
      }
      setModalOpen(false);
      form.resetFields();
    } catch {
      message.error(editItem ? "更新失败" : "创建失败");
    }
  };

  const columns: ColumnsType<SKU> = [
    { title: "产品", dataIndex: "product_name", key: "product_name", render: (v?: string) => v || "-" },
    { title: "SKU 编码", dataIndex: "code", key: "code" },
    { title: "名称", dataIndex: "name", key: "name" },
    { title: "包装", dataIndex: "package_type", key: "package_type", render: (v?: string) => v || "-" },
    { title: "条码/GTIN", dataIndex: "barcode", key: "barcode", render: (v?: string) => v || "-" },
    {
      title: "规格",
      dataIndex: "specifications",
      key: "specifications",
      render: (v: Record<string, string>) => v ? Object.entries(v).map(([k, val]) => `${k}: ${val}`).join(", ") : "-",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => (
        <Tag color={s === "active" ? "green" : "default"}>{s === "active" ? "启用" : "停用"}</Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: SKU) => (
        <Button type="link" size="small" onClick={() => openEdit(record)}>编辑</Button>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">SKU 管理</Title>
        <Space>
          <Select
            placeholder="按产品筛选"
            allowClear
            style={{ width: 200 }}
            value={undefined}
            onChange={(v) => { setFilter(v ? { product_id: v } : {}); }}
            options={products.map((p) => ({ value: p.id, label: p.name }))}
            showSearch
            optionFilterProp="label"
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建 SKU
          </Button>
        </Space>
      </div>
      <Table
        columns={columns}
        dataSource={skus}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page, total, pageSize: 20, onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
      />
      <Modal
        title={editItem ? "编辑 SKU" : "新建 SKU"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          {!editItem && (
            <Form.Item name="product_id" label="关联产品" rules={[{ required: true, message: "请选择产品" }]}>
              <Select
                placeholder="选择产品"
                options={products.map((p) => ({ value: p.id, label: p.name }))}
                showSearch
                optionFilterProp="label"
              />
            </Form.Item>
          )}
          <Form.Item name="code" label="SKU 编码" rules={[{ required: true, message: "请输入编码" }]}>
            <Input placeholder="例如 SKU-001" />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="package_type" label="包装类型">
            <Input placeholder="例如 袋装、盒装、礼盒" />
          </Form.Item>
          <Form.Item name="barcode" label="条码/GTIN">
            <Input placeholder="例如 6901234567890" />
          </Form.Item>
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
                <div className="mb-2 flex items-center justify-between">
                  <span>规格属性</span>
                  <Button size="small" onClick={() => add({ key: "", value: "" })}>添加规格</Button>
                </div>
                {fields.map((field) => (
                  <Space key={field.key} className="mb-2 flex" align="baseline">
                    <Form.Item {...field} name={[field.name, "key"]} className="!mb-0" rules={[{ required: true, message: "请输入规格名" }]}>
                      <Input placeholder="规格名，如 净含量" />
                    </Form.Item>
                    <Form.Item {...field} name={[field.name, "value"]} className="!mb-0" rules={[{ required: true, message: "请输入规格值" }]}>
                      <Input placeholder="规格值，如 5kg" />
                    </Form.Item>
                    <Button danger type="link" onClick={() => remove(field.name)}>删除</Button>
                  </Space>
                ))}
              </div>
            )}
          </Form.List>
        </Form>
      </Modal>
    </div>
  );
}
