"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useCrud } from "@/lib/hooks";
import { App, Button, Form, Modal, Select, Space, Switch, Table, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import SKUFormFields, { buildSkuPayload, type SKUFormValues } from "@/components/SKUFormFields";
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

export default function SKUsPage() {
  const { message } = App.useApp();
  const router = useRouter();
  const [products, setProducts] = useState<Product[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<SKU | null>(null);
  const [form] = Form.useForm();
  const submitModeRef = useRef<"close" | "continue">("close");

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
      package_type: sku.package_type ? [sku.package_type] : undefined,
      spec_entries: Object.entries(sku.specifications || {}).map(([key, value]) => ({ key, value })),
    });
    setModalOpen(true);
  };

  const handleSubmit = async (values: SKUFormValues) => {
    try {
      const payload = buildSkuPayload(values);
      if (editItem) {
        await update(editItem.id, payload);
        message.success("SKU 更新成功");
      } else {
        await create(payload);
        message.success("SKU 创建成功");
      }
      if (submitModeRef.current === "continue" && !editItem) {
        form.resetFields();
        form.setFieldValue("product_id", values.product_id);
      } else {
        setModalOpen(false);
        form.resetFields();
      }
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
      render: (v: Record<string, string>) => v && Object.keys(v).length
        ? Object.entries(v).map(([k, val]) => `${k}: ${val}`).join(", ")
        : "未填写",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string, record: SKU) => (
        <Switch
          checked={s === "active"}
          checkedChildren="启用"
          unCheckedChildren="停用"
          onChange={async (checked) => {
            try {
              await update(record.id, { status: checked ? "active" : "inactive" });
              message.success(checked ? "已启用" : "已停用");
            } catch {
              message.error("状态更新失败");
            }
          }}
        />
      ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: SKU) => (
        <Space>
          <Button type="link" size="small" onClick={() => router.push(`/skus/${record.id}`)}>详情</Button>
          <Button type="link" size="small" onClick={() => openEdit(record)}>编辑</Button>
        </Space>
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
        onOk={() => {
          submitModeRef.current = "close";
          form.submit();
        }}
        okText={editItem ? "更新 SKU" : "保存 SKU"}
        width={640}
        forceRender
        footer={(_, { OkBtn, CancelBtn }) => (
          <>
            <CancelBtn />
            {!editItem && (
              <Button
                onClick={() => {
                  submitModeRef.current = "continue";
                  form.submit();
                }}
              >
                保存并继续添加
              </Button>
            )}
            <OkBtn />
          </>
        )}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <SKUFormFields form={form} products={products} showProductSelect={!editItem} />
        </Form>
      </Modal>
    </div>
  );
}
